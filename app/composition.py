"""Capability-gated service composition for SIMULATE/SHADOW/LIVE (single station)."""
from __future__ import annotations
from dataclasses import dataclass
import json
from pathlib import Path
from .models import RunMode, StationId
from .config import Settings
from .ateq import FakeAteq, SerialAteq
from .plc import FakePlc, Snap7Plc
from .laser import FakeMarker, LaserMarker, LaserChannel, LaserFileWriter
from .date_codes import DateCodeCatalog
from .points import PointMap, load_points, sim_point_map
from .repository import FakeRepository, PyMySQLRepository

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
    plc = Snap7Plc(settings.plc_ip)
    plc.connect()
    plc.enable_writes(True)
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
