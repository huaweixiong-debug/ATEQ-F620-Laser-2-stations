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
    # 240429 箱体气密封机：PLC 档位/串口、称重、中转、校准周期
    "plc_profile", "plc_com", "plc_baud", "plc_parity", "plc_databits", "plc_stopbits",
    "weight_enabled", "weight_com", "weight_slave", "weight_register",
    "weight_plc_register", "weight_poll_ms",
    "plc_relay_host", "plc_relay_port", "plc_relay_token",
    "relay_enabled", "relay_port", "relay_token",
    "calibration_period_hours",
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
    # 240429：PLC 档位（s7=S7-200 SMART snap7 / fx=三菱 FX 编程口直连）
    plc_profile: str = "s7"
    plc_com: str = "COM3"
    plc_baud: int = 9600
    plc_parity: str = "E"
    plc_databits: int = 7
    plc_stopbits: int = 1
    # 称重（COM6 Modbus RTU 40002 -> PLC D900）
    weight_enabled: bool = False
    weight_com: str = "COM6"
    weight_slave: int = 1
    weight_register: int = 40002
    weight_plc_register: str = "D900"
    weight_poll_ms: int = 500
    # A 侧：PLC 经 B 电脑中转（host 空 = 本机直连）；B 侧：开启中转服务
    plc_relay_host: str = ""
    plc_relay_port: int = 9101
    plc_relay_token: str = ""
    relay_enabled: bool = False
    relay_port: int = 9101
    relay_token: str = ""
    # NG/OK 样件验证周期（小时），默认 8h，UI 全局设置可改
    calibration_period_hours: float = 8
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

        # ---- 240429 新键校验 ----
        plc_profile = str(values.get("plc_profile", "s7")).strip().lower()
        if plc_profile not in ("s7", "fx"):
            raise ValueError("plc_profile 必须是 s7 或 fx")
        plc_com = str(values.get("plc_com", cls.plc_com)).upper()
        if not re.fullmatch(r"COM[1-9][0-9]*", plc_com):
            raise ValueError("无效 plc_com COM 口")
        plc_baud = int(values.get("plc_baud", 9600))
        if plc_baud not in (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200):
            raise ValueError("无效 plc_baud")
        plc_parity = str(values.get("plc_parity", "E")).strip().upper()
        if plc_parity not in ("N", "E", "O"):
            raise ValueError("plc_parity 必须是 N/E/O")
        plc_databits = int(values.get("plc_databits", 7))
        plc_stopbits = int(values.get("plc_stopbits", 1))
        if plc_databits not in (7, 8) or plc_stopbits not in (1, 2):
            raise ValueError("plc_databits 必须 7/8，plc_stopbits 必须 1/2")
        weight_enabled = _flag("weight_enabled")
        weight_com = str(values.get("weight_com", cls.weight_com)).upper()
        if not re.fullmatch(r"COM[1-9][0-9]*", weight_com):
            raise ValueError("无效 weight_com COM 口")
        weight_slave = int(values.get("weight_slave", 1))
        if not 1 <= weight_slave <= 255:
            raise ValueError("无效 weight_slave 从站地址")
        weight_register_raw = values.get("weight_register", cls.weight_register)
        try:
            weight_register = int(str(weight_register_raw))
        except ValueError as exc:
            raise ValueError("weight_register 必须为 4xxxx 台账地址") from exc
        if not 40001 <= weight_register <= 465535:
            raise ValueError("weight_register 必须为 4xxxx 台账地址")
        weight_plc_register = str(values.get("weight_plc_register", cls.weight_plc_register)).strip().upper()
        if not re.fullmatch(r"D[0-9]{1,5}", weight_plc_register):
            raise ValueError("weight_plc_register 必须为 D 字寄存器（如 D900）")
        weight_poll_ms = int(values.get("weight_poll_ms", 500))
        if not 50 <= weight_poll_ms <= 60000:
            raise ValueError("weight_poll_ms 范围 50..60000ms")
        plc_relay_host = str(values.get("plc_relay_host", "")).strip()
        plc_relay_port = int(values.get("plc_relay_port", 9101))
        plc_relay_token = str(values.get("plc_relay_token", ""))
        relay_enabled = _flag("relay_enabled")
        relay_port = int(values.get("relay_port", 9101))
        relay_token = str(values.get("relay_token", ""))
        for name, port_value in (("plc_relay_port", plc_relay_port), ("relay_port", relay_port)):
            if not 1024 <= port_value <= 65535:
                raise ValueError(f"{name} 范围 1024..65535")
        if plc_relay_host and not plc_relay_token:
            raise ValueError("配置 plc_relay_host 时必须设置 plc_relay_token")
        if relay_enabled and not relay_token:
            raise ValueError("开启 relay_enabled 时必须设置 relay_token")
        calibration_period_hours = float(values.get("calibration_period_hours", 8))
        if not 0.1 <= calibration_period_hours <= 720:
            raise ValueError("calibration_period_hours 范围 0.1..720 小时")
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
                   plc_profile=plc_profile, plc_com=plc_com,
                   plc_baud=plc_baud, plc_parity=plc_parity,
                   plc_databits=plc_databits, plc_stopbits=plc_stopbits,
                   weight_enabled=weight_enabled, weight_com=weight_com,
                   weight_slave=weight_slave, weight_register=weight_register,
                   weight_plc_register=weight_plc_register,
                   weight_poll_ms=weight_poll_ms,
                   plc_relay_host=plc_relay_host, plc_relay_port=plc_relay_port,
                   plc_relay_token=plc_relay_token,
                   relay_enabled=relay_enabled, relay_port=relay_port,
                   relay_token=relay_token,
                   calibration_period_hours=calibration_period_hours,
                   config_source=str(path))

    def laser_file(self) -> Path:
        return self.laser_dir / self.laser_filename

    def can_write(self) -> bool:
        return False

    def issue_capability(self) -> LiveCapability:
        raise PermissionError("live capability requires现场门禁 and cannot be issued in this build")
