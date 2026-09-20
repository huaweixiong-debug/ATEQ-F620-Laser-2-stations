"""Read-only production readiness checks.  A failed check blocks LIVE startup."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .ateq import SerialAteq
from .config import Settings
from .date_codes import DateCodeCatalog, DateCodeError
from .laser import LaserFileWriter, LaserChannel
from .points import load_points, PointMapError
from .repository import PyMySQLRepository
from .model_settings import ModelSettingsService


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class PreflightReport:
    checks: tuple[PreflightCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def format(self) -> str:
        return "\n".join(f"{'PASS' if item.passed else 'BLOCKED'} {item.name}: {item.detail}"
                         for item in self.checks)


def _tcp(name: str, host: str, port: int) -> PreflightCheck:
    import socket
    try:
        with socket.create_connection((host, port), timeout=2):
            pass
        return PreflightCheck(name, True, f"{host}:{port} 可连接")
    except OSError as exc:
        return PreflightCheck(name, False, f"{host}:{port} {type(exc).__name__}: {exc}")


def _credentials(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise FileNotFoundError(f"数据库凭据文件不存在: {path}")
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, dict) or not values.get("user") or not values.get("password"):
        raise ValueError("数据库凭据文件缺少 user/password")
    return {"user": str(values["user"]), "password": str(values["password"])}


def run_preflight(settings: Settings, probe_devices: bool = True) -> PreflightReport:
    checks: list[PreflightCheck] = []

    # 点位表：电气点位表必须已提供并人工确认（points_confirmed）。
    try:
        load_points(settings.points_path)
        if not settings.points_confirmed:
            raise PointMapError("points_confirmed=false：点位地址尚未经电气确认")
        checks.append(PreflightCheck("PLC 点位表", True, f"{settings.points_path} 已加载并确认"))
    except Exception as exc:
        checks.append(PreflightCheck("PLC 点位表", False, str(exc)))

    remote = bool(settings.plc_relay_host)
    if remote:
        checks.append(_tcp("PLC 中转", settings.plc_relay_host, settings.plc_relay_port))
    elif settings.plc_profile == "fx":
        checks.append(PreflightCheck("PLC 串口配置", True,
                                     f"FX 直连 {settings.plc_com} "
                                     f"{settings.plc_baud} {settings.plc_databits}"
                                     f"{settings.plc_parity}{settings.plc_stopbits}"))
    else:
        checks.append(_tcp("PLC", settings.plc_ip, 102))
    if probe_devices:
        try:
            checks.append(_probe_plc_bits(settings))
        except Exception as exc:
            checks.append(PreflightCheck("PLC 点位读回", False, f"{type(exc).__name__}: {exc}"))

    if not settings.ports_confirmed:
        checks.append(PreflightCheck("ATEQ 映射", False, "COM 口尚未逐台确认（ports_confirmed=false）"))
    elif probe_devices:
        adapter = SerialAteq(settings.ateq_com, settings.station.value,
                             slave=settings.ateq_slave, timeout_s=0.6)
        try:
            adapter.connect()
            adapter.read_registers(adapter.REALTIME_ADDRESS, 1)
            checks.append(PreflightCheck(f"ATEQ {settings.station.value}", True,
                                         f"{settings.ateq_com} slave={settings.ateq_slave}"))
        except Exception as exc:
            checks.append(PreflightCheck(f"ATEQ {settings.station.value}", False,
                                         f"{settings.ateq_com}: {type(exc).__name__}: {exc}"))
        finally:
            adapter.close()

    if settings.weight_enabled:
        if probe_devices:
            checks.append(_probe_weight(settings))
        else:
            checks.append(PreflightCheck("称重", True, f"{settings.weight_com} 配置启用（未探测）"))

    # 激光监听目录可写（不触碰已有内容）。
    try:
        LaserFileWriter(LaserChannel(settings.laser_file(), settings.laser_encoding,
                                     settings.laser_newline)).preflight()
        checks.append(PreflightCheck("激光打码目录", True, str(settings.laser_file())))
    except Exception as exc:
        checks.append(PreflightCheck("激光打码目录", False, str(exc)))

    # 型号配置：至少一个型号；日期方案可解析（映射方案需要 日期对照.ini）。
    try:
        from .permissions import SecurityContext
        models = ModelSettingsService(SecurityContext(), settings.data_dir / "日期设置.ini")
        names = models.list_models()
        if not names:
            raise FileNotFoundError(f"{models.path} 中没有任何型号")
        date_path = settings.data_dir / "日期对照.ini"
        catalog = DateCodeCatalog.from_file(date_path) if date_path.is_file() else DateCodeCatalog({})
        from datetime import datetime
        for name in names:
            config = models.load(name)
            catalog.date_code(config.date_scheme, datetime.now())
        checks.append(PreflightCheck("日期/型号配置", True, f"{len(names)} 个型号，日期方案可解析"))
    except DateCodeError as exc:
        checks.append(PreflightCheck("日期/型号配置", False, str(exc)))
    except Exception as exc:
        checks.append(PreflightCheck("日期/型号配置", False, str(exc)))

    try:
        credential = _credentials(settings.credential_path)
        repo = PyMySQLRepository(host=settings.database_host, port=settings.database_port,
                                 user=credential["user"], password=credential["password"],
                                 database=settings.database)
        repo.connect_and_verify()
        checks.append(PreflightCheck("MySQL", True, f"{settings.database}.info_A/info_B 结构通过（v2）"))
    except Exception as exc:
        checks.append(PreflightCheck("MySQL", False, f"{type(exc).__name__}: {exc}"))
    return PreflightReport(tuple(checks))


def _probe_plc_bits(settings: Settings) -> PreflightCheck:
    """连接 PLC 并读取启动位与激光启动位（只读），验证点位表可达。"""
    from .composition import build_point_map, build_plc
    point_map = build_point_map(settings)
    plc = build_plc(settings)
    try:
        start = point_map.address("start")
        laser_start = point_map.address("laser_start")
        plc.read_bit(*start)
        plc.read_bit(*laser_start)
        if settings.plc_relay_host:
            target = f"中转 {settings.plc_relay_host}:{settings.plc_relay_port}"
        elif settings.plc_profile == "fx":
            target = settings.plc_com
        else:
            target = settings.plc_ip
        detail = (f"start=M{start[0]}.{start[1]} laser_start=M{laser_start[0]}.{laser_start[1]} 可读（{target}）")
        return PreflightCheck("PLC 点位读回", True, detail)
    finally:
        plc.disconnect()


def _probe_weight(settings: Settings) -> PreflightCheck:
    """只读探测称重 Modbus 设备（读配置寄存器一次，不写 PLC）。"""
    from .weight_scale import WeightScale
    scale = WeightScale(settings.weight_com, slave=settings.weight_slave,
                        register=settings.weight_register, timeout_s=0.8)
    try:
        scale.connect()
        value = scale.read_raw()
        return PreflightCheck("称重", True,
                              f"{settings.weight_com} slave={settings.weight_slave} "
                              f"{settings.weight_register}={value} -> {settings.weight_plc_register}")
    finally:
        scale.close()
