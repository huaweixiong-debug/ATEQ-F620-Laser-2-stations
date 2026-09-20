"""Configurable S7-200 SMART M-area point map.

电气点位表尚未提供：所有地址都是占位值，等点位表到位后只改
``config/points.toml``，不改代码。live 预检要求 ``points_confirmed``
标志为真才会放行，占位地址不可能被误用于生产。

Address format is ``M<byte>.<bit>`` (S7-200 SMART M 区，字节 0-31、位 0-7)。
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from pathlib import Path
try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # Python 3.10 offline deployment
    try:
        import tomli as tomllib
    except ModuleNotFoundError:
        tomllib = None

REQUIRED_SIGNALS = (
    "start",          # 启动信号（硬件上升沿开周期）
    "reset",          # 复位
    "calibration",    # 校准时间到（PLC 请求样件验证）
    "ng_sample",      # NG 样件需求
    "ok_sample",      # OK 样件需求
    "manual",         # 自动/手动切换
    "block",          # 手动封堵
    "stamp",          # 手动盖章
    "clamp",          # 手动夹紧
    "transfer",       # 手动移载
    "pressure",       # 正/负压开启
    "door_disable",   # 安全门使能/禁用
    "laser_start",    # 激光打码启动位（上位机脉冲输出）
)
OPTIONAL_SIGNALS = (
    "laser_done",     # 激光打码完成位（可选反馈）
)

# FX 档（240429 箱体气密封机，三菱 FX）：点位沿用老机 OPC 标签契约。
# ⚠️ FX 位元件按 byte.bit 拆分书写：元件号 = byte*8 + bit
#    （M0→M0.0、M1→M0.1、M5→M0.5、M466→M58.2、M467→M58.3、
#     M890→M111.2、M891→M111.3）；FxSerialPlc 会乘回元件号。
# 每台电脑实例只填本工位信号（A=左，B=右）。
REQUIRED_SIGNALS_FX = (
    "start",          # 本工位启动（A=M0.0 左复位 / B=M0.3 右复位，上升沿开周期）
    "result_ok",      # 本工位合格回写（A=M0.1 即 M1 / B=M0.4 即 M4）
    "result_ng",      # 本工位不合格回写（A=M0.5 即 M5 / B=M0.6 即 M6）
    "laser_start",    # 激光打码启动位（⚠️ 待电气确认具体地址）
)
OPTIONAL_SIGNALS_FX = (
    "laser_done",     # 激光打码完成位（可选）
    "reset",          # 本工位复位（若与启动分开）
    "sample",         # 样件模式（老 M467 → M58.3）
    "isolation_init", # 隔离箱初始化（老 M466 → M58.2）
    "shield_cylinder",# 本工位屏蔽气缸（A=M111.3 即 M891 / B=M111.2 即 M890）
)

_ADDRESS_RE = re.compile(r"^M([0-9]|[1-9][0-9]?[0-9]?)\.([0-7])$")

# 档位：s7 = S7-200 SMART（M 区 0..31）；fx = 三菱 FX（M 0..8191）。
_PROFILE_LIMITS = {"s7": 31, "fx": 8191}

# simulate/单测使用的演示点位表：只保证 FakePlc 可读写，无物理意义。
SIM_POINTS = {
    "start": "M16.0",
    "reset": "M0.3",
    "calibration": "M1.0",
    "ng_sample": "M1.2",
    "ok_sample": "M1.3",
    "manual": "M2.0",
    "block": "M4.2",
    "stamp": "M4.6",
    "clamp": "M4.4",
    "transfer": "M4.0",
    "pressure": "M0.5",
    "door_disable": "M0.6",
    "laser_start": "M20.0",
    "laser_done": "M20.1",
}


class PointMapError(ValueError):
    pass


def parse_address(value: str, *, max_byte: int = 31) -> tuple[int, int]:
    match = _ADDRESS_RE.fullmatch(str(value).strip().upper())
    if not match:
        raise PointMapError(f"无效 PLC 位地址: {value!r}（格式 M<byte>.<bit>）")
    byte, bit = int(match.group(1)), int(match.group(2))
    if not 0 <= byte <= max_byte:
        raise PointMapError(f"M 区字节号超范围: {value!r}（上限 {max_byte}）")
    return byte, bit


@dataclass(frozen=True)
class PointMap:
    addresses: dict[str, tuple[int, int]]
    source: str = "sim"

    def address(self, signal: str) -> tuple[int, int]:
        try:
            return self.addresses[signal]
        except KeyError as exc:
            raise PointMapError(f"点位表缺少信号: {signal}") from exc

    def has(self, signal: str) -> bool:
        return signal in self.addresses


def _parse_mapping(raw: dict, *, profile: str = "s7") -> dict[str, tuple[int, int]]:
    if profile == "fx":
        required, optional = REQUIRED_SIGNALS_FX, OPTIONAL_SIGNALS_FX
    else:
        required, optional = REQUIRED_SIGNALS, OPTIONAL_SIGNALS
    unknown = set(raw) - set(required) - set(optional)
    if unknown:
        raise PointMapError(f"点位表存在未知信号: {sorted(unknown)}")
    missing = [signal for signal in required if signal not in raw]
    if missing:
        raise PointMapError(f"点位表缺少必填信号: {missing}")
    max_byte = _PROFILE_LIMITS.get(profile, 31)
    return {signal: parse_address(value, max_byte=max_byte) for signal, value in raw.items()}


def sim_point_map() -> PointMap:
    return PointMap(_parse_mapping(dict(SIM_POINTS)), source="sim")


def load_points(path: Path) -> PointMap:
    """加载配置点位表；缺少必填信号或地址非法时拒绝启动。

    点位表可用 ``[meta] profile = "s7" | "fx"`` 声明档位（缺省 s7），
    FX 档按三菱 FX 的信号集与 M 区范围校验。
    """
    path = Path(path)
    if not path.is_file():
        raise PointMapError(f"点位表文件不存在: {path}")
    if tomllib is not None:
        raw_doc = tomllib.loads(path.read_text(encoding="utf-8"))
    else:
        raw_doc = {}
        section = None
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip()
                raw_doc.setdefault(section, {})
                continue
            if section is None or "=" not in line:
                continue
            key, value = line.split("=", 1)
            raw_doc[section][key.strip()] = value.strip().strip('"')
    raw = raw_doc.get("points")
    if not isinstance(raw, dict) or not raw:
        raise PointMapError(f"点位表缺少 [points] 节: {path}")
    meta = raw_doc.get("meta", {})
    profile = str(meta.get("profile", "s7")).strip().lower() if isinstance(meta, dict) else "s7"
    if profile not in _PROFILE_LIMITS:
        raise PointMapError(f"未知点位档位: {profile!r}（支持 s7 / fx）")
    return PointMap(_parse_mapping(raw, profile=profile), source=str(path))
