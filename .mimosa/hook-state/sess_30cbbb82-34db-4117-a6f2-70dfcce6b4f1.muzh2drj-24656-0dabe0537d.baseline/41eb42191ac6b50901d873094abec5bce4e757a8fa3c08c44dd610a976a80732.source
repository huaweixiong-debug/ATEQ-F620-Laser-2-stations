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
from .models import RunMode
class LiveCapability:
    __slots__ = ()
    def __new__(cls):
        raise TypeError("LiveCapability is issued only by the unavailable现场 gate")

@dataclass(frozen=True)
class Settings:
    mode: RunMode = RunMode.SIMULATE
    plc_ip: str = "192.168.2.1"
    plc_poll_ms: int = 100
    ateq_ports: tuple[str, str] = ("COM6", "COM7")
    database: str = "test"
    setup_path: Path = Path(r"D:\data\Setup.ini")
    require_live_gate: bool = True
    config_source: str = "default.toml"
    ports_confirmed: bool = False

    @classmethod
    def from_args(cls, mode: str | None = None) -> "Settings":
        selected = RunMode(mode.lower()) if mode else RunMode.SIMULATE
        return cls(mode=selected)

    @classmethod
    def from_file(cls, path: Path) -> "Settings":
        """Load observed GBK setup values without inventing missing ports.

        This method is read-only.  A missing/invalid ATEQ mapping is an explicit
        configuration blocker; callers must not silently substitute screenshot
        sample values.
        """
        if not path.exists():
            raise ValueError(f"Setup.ini 不存在: {path}")
        values, _ = parse_setup_ini(path)
        ports = (values.get("ateq_com_a"), values.get("ateq_com_b"))
        if any(not p for p in ports):
            raise ValueError("Setup.ini 缺少 ATEQ F620 A/B 映射")
        if any(not re.fullmatch(r"COM[1-9][0-9]*", str(p).upper()) for p in ports):
            raise ValueError(f"Setup.ini COM 映射无效: {ports}")
        return cls(ateq_ports=(str(ports[0]).upper(), str(ports[1]).upper()), setup_path=path,
                   config_source=str(path), ports_confirmed=True)

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
        unknown = set(values) - {"mode", "plc_ip", "plc_poll_ms", "ateq_com_a", "ateq_com_b", "database"}
        if unknown:
            raise ValueError(f"未知关键配置: {sorted(unknown)}")
        mode = str(values.get("mode", "simulate")).lower()
        if mode not in {item.value for item in RunMode}: raise ValueError("无效运行模式")
        poll = int(values.get("plc_poll_ms", 100))
        if not 20 <= poll <= 5000: raise ValueError("PLC 轮询范围 20..5000ms")
        ports = (str(values.get("ateq_com_a", "COM6")), str(values.get("ateq_com_b", "COM7")))
        if any(not re.fullmatch(r"COM[1-9][0-9]*", port.upper()) for port in ports): raise ValueError("无效 COM 口")
        ip = str(values.get("plc_ip", cls.plc_ip))
        try: ipaddress.ip_address(ip)
        except ValueError as exc: raise ValueError("无效 PLC IP") from exc
        return cls(RunMode(mode), ip, poll, ports)

    def can_write(self) -> bool:
        return False

    def issue_capability(self) -> LiveCapability:
        raise PermissionError("live capability requires现场门禁 and cannot be issued in this build")

def parse_setup_ini(path: Path) -> tuple[dict[str, str], list[str]]:
    """Read the observed GBK INI format, report unknown keys instead of masking errors."""
    known = {"ateqcom1", "ateqcom2", "ateq_com_a", "ateq_com_b",
             "ateqf620a(restartsoftwaretoactive)", "ateqf620b(restartsoftwaretoactive)"}; values, unknown = {}, []
    for line in path.read_text(encoding="gbk", errors="replace").splitlines():
        if "=" not in line or line.strip().startswith("["): continue
        key, value = line.split("=", 1); normalized = key.strip().lower().replace(" ", "")
        value = value.strip().strip('"')
        if normalized in known: values[normalized] = value
        else: unknown.append(key.strip())
    if "ateqf620a(restartsoftwaretoactive)" in values:
        values.setdefault("ateq_com_a", values["ateqf620a(restartsoftwaretoactive)"])
    if "ateqf620b(restartsoftwaretoactive)" in values:
        values.setdefault("ateq_com_b", values["ateqf620b(restartsoftwaretoactive)"])
    return values, unknown
