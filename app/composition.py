"""Capability-gated service composition for SIMULATE/SHADOW/LIVE (single station)."""
from __future__ import annotations
from dataclasses import dataclass
import json
from pathlib import Path
from .models import RunMode, StationId
from .config import Settings
from .ateq import FakeAteq, SerialAteq
from .plc import FakePlc, FxSerialPlc, Snap7Plc
from .plc_relay import PlcRelayServer, RemoteFxPlc
from .laser import FakeMarker, LaserMarker, LaserChannel, LaserFileWriter
from .date_codes import DateCodeCatalog
from .points import PointMap, load_points, sim_point_map
from .repository import FakeRepository, PyMySQLRepository
from .weight_scale import WeightScale, WeightService

class ReadOnlyPlc:
    def __init__(self): self._inner = FakePlc()
    def read_bit(self, byte: int, bit: int) -> bool: return self._inner.read_bit(byte, bit)
    def write_bit(self, byte: int, bit: int, value: bool) -> None: raise PermissionError("SHADOW 禁止 PLC 写入")
    def health(self) -> bool: return False
    def safe_stop(self, reason: str) -> None: self._inner.safe_stop(reason)
    def outputs_energized(self) -> bool: return self._inner.outputs_energized()

class ReadOnlyAteq:
    station = "SHADOW"
    program = ""
    def select_program(self, program: str) -> None: raise PermissionError("SHADOW 禁止 ATEQ 命令")
    def start_test(self) -> None: raise PermissionError("SHADOW 禁止 ATEQ 命令")
    def run(self, request): raise PermissionError("SHADOW 只能使用离线重放帧")

class ReadOnlyMarker:
    def mark(self, record) -> None: raise PermissionError("SHADOW 禁止打码")

class ReadOnlyRepository:
    def insert_stage1(self, record, capability=None): raise PermissionError("SHADOW 禁止数据库写入")
    def update_stage2(self, cycle_id, measurement, capability=None): raise PermissionError("SHADOW 禁止数据库写入")
    def mark_marked(self, cycle_id, capability=None): raise PermissionError("SHADOW 禁止数据库写入")
    def row_id(self, cycle_id): return None
    def get(self, cycle_id): return None
    def get_committed(self, cycle_id): return None
    def query(self, text=""): return []

@dataclass(frozen=True)
class CapabilityPolicy:
    mode: RunMode
    writes_allowed: bool = False
    physical_io_allowed: bool = False

    def require(self, capability: str) -> None:
        if self.mode is not RunMode.LIVE or not self.physical_io_allowed:
            raise PermissionError(f"{capability} 被 {self.mode.value} capability policy 拒绝")

def build_point_map(settings: Settings) -> PointMap:
    if settings.mode is RunMode.LIVE:
        return load_points(settings.points_path)
    try:
        return load_points(settings.points_path)
    except Exception:
        return sim_point_map()

def build_date_code_fn(settings: Settings):
    """Direct schemes work without 日期对照.ini; mapping schemes need it."""
    path = settings.data_dir / "日期对照.ini"
    catalog = DateCodeCatalog.from_file(path) if path.is_file() else DateCodeCatalog({})
    return catalog.date_code

def build_plc(settings: Settings):
    """按配置构建并连接 LIVE PLC 适配器（连接成功后放开写权限）。

    - plc_relay_host 非空：经 B 电脑中转（A 工位部署形态）；
    - plc_profile = "fx"：三菱 FX 编程口直连（240429，COM3）；
    - 否则 S7-200 SMART snap7（基线形态）。
    """
    if settings.plc_relay_host:
        plc = RemoteFxPlc(settings.plc_relay_host, settings.plc_relay_port,
                          token=settings.plc_relay_token)
        plc.connect()
        plc.enable_writes(True)
        return plc
    if settings.plc_profile == "fx":
        plc = FxSerialPlc(settings.plc_com, baudrate=settings.plc_baud,
                          parity=settings.plc_parity, bytesize=settings.plc_databits,
                          stopbits=settings.plc_stopbits)
        plc.connect()
        plc.enable_writes(True)
        return plc
    plc = Snap7Plc(settings.plc_ip)
    plc.connect()
    plc.enable_writes(True)
    return plc


def build_weight_service(settings: Settings, plc) -> WeightService | None:
    """称重 -> PLC 字寄存器转发服务（weight_enabled=false 返回 None）。"""
    if not settings.weight_enabled:
        return None
    scale = WeightScale(settings.weight_com, slave=settings.weight_slave,
                        register=settings.weight_register)
    scale.connect()
    plc_device = int(settings.weight_plc_register[1:])
    service = WeightService(scale, plc, plc_register=plc_device,
                            poll_s=settings.weight_poll_ms / 1000.0,
                            writes_enabled=True)
    service.start()
    return service


def start_relay_service(settings: Settings, plc) -> PlcRelayServer | None:
    """B 侧 PLC 中转服务（relay_enabled=false 返回 None）。"""
    if not settings.relay_enabled:
        return None
    server = PlcRelayServer(plc, port=settings.relay_port, token=settings.relay_token)
    server.start()
    return server


def build_services(settings: Settings, station: StationId = StationId.A, *, preflight_passed: bool = False):
    policy = CapabilityPolicy(settings.mode)
    if settings.mode is RunMode.SIMULATE:
        return (policy, FakePlc(), FakeAteq(station=station.value), FakeRepository(settings),
                FakeMarker(), build_point_map(settings))
    if settings.mode in (RunMode.CHARACTERIZATION, RunMode.SHADOW):
        # Shadow deliberately uses replay/Fake services; no serial, PLC, DB,
        # laser resource is opened and no write method can be reached.
        return (policy, ReadOnlyPlc(), ReadOnlyAteq(), ReadOnlyRepository(),
                ReadOnlyMarker(), build_point_map(settings))
    if not preflight_passed:
        raise RuntimeError("LIVE_BLOCKED: production composition requires a passing live preflight")
    credentials = json.loads(Path(settings.credential_path).read_text(encoding="utf-8"))
    repository = PyMySQLRepository(host=settings.database_host, port=settings.database_port,
                                   user=credentials["user"], password=credentials["password"],
                                   database=settings.database)
    repository.connect_and_verify()
    plc = build_plc(settings)
    start_relay_service(settings, plc)
    build_weight_service(settings, plc)
    ateq = SerialAteq(settings.ateq_com, station.value, slave=settings.ateq_slave)
    ateq.connect()
    point_map = build_point_map(settings)
    marker = LaserMarker(
        LaserFileWriter(LaserChannel(settings.laser_file(), settings.laser_encoding, settings.laser_newline)),
        plc, point_map,
        hold_seconds=settings.laser_hold_seconds, settle_seconds=settings.laser_settle_seconds,
        clear_after_seconds=settings.laser_clear_after_seconds,
        wait_done=settings.laser_wait_done, done_timeout_s=settings.laser_done_timeout_s,
        date_code_fn=build_date_code_fn(settings))
    return (CapabilityPolicy(settings.mode, writes_allowed=True, physical_io_allowed=True),
            plc, ateq, repository, marker, point_map)
