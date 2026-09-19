"""Safe configuration; live mode is explicitly gated."""
from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import ipaddress
import re
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # Python 3.10 offline deployment
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None
from .models import RunMode, StationId

class LiveCapability:
    __slots__ = ()
    def __new__(cls):
        raise TypeError("LiveCapability is issued only by the unavailable现场 gate")

_KNOWN_KEYS = {
    "mode", "station", "plc_ip", "plc_poll_ms", "ateq_com", "ateq_slave",
    "ports_confirmed", "points_confirmed", "points_path",
    "database", "database_host", "database_port", "credential_path", "data_dir",
    "laser_dir", "laser_filename", "laser_encoding", "laser_newline",
    "laser_hold_seconds", "laser_settle_seconds", "laser_clear_after_seconds",
    "laser_wait_done", "laser_done_timeout_s", "mark_samples", "laser_date_scheme",
}

@dataclass(frozen=True)
class Settings:
    mode: RunMode = RunMode.SIMULATE
    station: StationId = StationId.A
    plc_ip: str = "192.168.2.1"
    plc_poll_ms: int = 100
    ateq_com: str = "COM6"
    ateq_slave: int = 255
    ports_confirmed: bool = False
    points_confirmed: bool = False
    points_path: Path = Path(__file__).parents[1] / "config" / "points.toml"
    database: str = "test"
    database_host: str = "127.0.0.1"
    database_port: int = 3306
    credential_path: Path = Path(r"C:\ProgramData\LaserLeakTest\db.json")
    data_dir: Path = Path(r"D:\data")
    laser_dir: Path = Path(r"D:\激光打码")
    laser_filename: str = "激光码信息.txt"
    laser_encoding: str = "gbk"
    laser_newline: str = "\r\n"
    laser_hold_seconds: float = 1.0
    laser_settle_seconds: float = 0.2
    laser_clear_after_seconds: float = 10.0
    laser_wait_done: bool = False
    laser_done_timeout_s: float = 10.0
    mark_samples: bool = False
    laser_date_scheme: str = "YYYYMMDD"
    config_source: str = "default.toml"

    @classmethod
    def from_args(cls, mode: str | None = None) -> "Settings":
        selected = RunMode(mode.lower()) if mode else RunMode.SIMULATE
        return cls(mode=selected)

    @classmethod
    def from_toml(cls, path: Path) -> "Settings":
        if tomllib is not None:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        else:
            raw = {}
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.split("#", 1)[0].strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                value = value.strip().strip('"')
                try:
                    value = int(value)
                except ValueError:
                    pass
                raw[key.strip()] = value
        values = {str(k): v for k, v in raw.items()}
        unknown = set(values) - _KNOWN_KEYS
        if unknown:
            raise ValueError(f"未知关键配置: {sorted(unknown)}")
        mode = str(values.get("mode", "simulate")).lower()
        if mode not in {item.value for item in RunMode}: raise ValueError("无效运行模式")
        station_raw = str(values.get("station", "A")).strip().upper()
        if station_raw not in ("A", "B"): raise ValueError("station 必须是 A 或 B")
        poll = int(values.get("plc_poll_ms", 100))
        if not 20 <= poll <= 5000: raise ValueError("PLC 轮询范围 20..5000ms")
        port = str(values.get("ateq_com", "COM6"))
        if not re.fullmatch(r"COM[1-9][0-9]*", port.upper()): raise ValueError("无效 COM 口")
        slave = int(values.get("ateq_slave", 255))
        if not 1 <= slave <= 255: raise ValueError("无效 ATEQ 从站地址")
        ip = str(values.get("plc_ip", cls.plc_ip))
        try: ipaddress.ip_address(ip)
        except ValueError as exc: raise ValueError("无效 PLC IP") from exc
        database_port = int(values.get("database_port", 3306))
        if not 1 <= database_port <= 65535: raise ValueError("无效数据库端口")
        hold = float(values.get("laser_hold_seconds", 1.0))
        settle = float(values.get("laser_settle_seconds", 0.2))
        clear_after = float(values.get("laser_clear_after_seconds", 10.0))
        done_timeout = float(values.get("laser_done_timeout_s", 10.0))
        if not 0.05 <= hold <= 30: raise ValueError("laser_hold_seconds 范围 0.05..30")
        if not 0 <= settle <= 30: raise ValueError("laser_settle_seconds 范围 0..30")
        if not 0 <= clear_after <= 600: raise ValueError("laser_clear_after_seconds 范围 0..600")
        if not 0.5 <= done_timeout <= 300: raise ValueError("laser_done_timeout_s 范围 0.5..300")

        def _flag(name: str, default=False) -> bool:
            raw_value = values.get(name, default)
            return raw_value if isinstance(raw_value, bool) else str(raw_value).lower() == "true"

        newline = str(values.get("laser_newline", "\r\n"))
        if newline not in ("", "\n", "\r\n"): raise ValueError("laser_newline 必须是空、\\n 或 \\r\\n")
        encoding = str(values.get("laser_encoding", "gbk"))
        try:
            "编码校验".encode(encoding)
        except LookupError as exc:
            raise ValueError(f"无效 laser_encoding: {encoding}") from exc
        return cls(mode=RunMode(mode), station=StationId(station_raw), plc_ip=ip, plc_poll_ms=poll,
                   ateq_com=port.upper(), ateq_slave=slave,
                   ports_confirmed=_flag("ports_confirmed"), points_confirmed=_flag("points_confirmed"),
                   points_path=Path(str(values.get("points_path", cls.points_path))),
                   database=str(values.get("database", "test")),
                   database_host=str(values.get("database_host", "127.0.0.1")),
                   database_port=database_port,
                   credential_path=Path(str(values.get("credential_path", r"C:\ProgramData\LaserLeakTest\db.json"))),
                   data_dir=Path(str(values.get("data_dir", r"D:\data"))),
                   laser_dir=Path(str(values.get("laser_dir", r"D:\激光打码"))),
                   laser_filename=str(values.get("laser_filename", "激光码信息.txt")),
                   laser_encoding=encoding, laser_newline=newline,
                   laser_hold_seconds=hold, laser_settle_seconds=settle,
                   laser_clear_after_seconds=clear_after,
                   laser_wait_done=_flag("laser_wait_done"),
                   laser_done_timeout_s=done_timeout,
                   mark_samples=_flag("mark_samples"),
                   laser_date_scheme=str(values.get("laser_date_scheme", "YYYYMMDD")),
                   config_source=str(path))

    def laser_file(self) -> Path:
        return self.laser_dir / self.laser_filename

    def can_write(self) -> bool:
        return False

    def issue_capability(self) -> LiveCapability:
        raise PermissionError("live capability requires现场门禁 and cannot be issued in this build")
