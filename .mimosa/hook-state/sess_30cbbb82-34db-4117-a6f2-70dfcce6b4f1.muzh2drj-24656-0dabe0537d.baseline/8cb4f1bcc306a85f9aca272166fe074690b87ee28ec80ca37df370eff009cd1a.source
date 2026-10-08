"""PLC point map, simulator and Snap7 adapter (reads live, writes gated).

Point map corrected from OPC config 20250828opc.opf (LabVIEW project).
"""
from __future__ import annotations
from dataclasses import dataclass
from threading import Lock, RLock
import time
from .models import StationId

# Confirmed PLC M-area point map from PLC通讯点位表.xlsx.  The A/B order in
# this table is intentionally not inferred from byte adjacency: several
# production signals are interleaved in M0/M1, while the operator start bits
# are M16.0/M16.1.
POINTS = {
    "door_disable": {StationId.A: (0, 6), StationId.B: (0, 7)},     # 安全门禁用
    "reset":        {StationId.A: (0, 3), StationId.B: (0, 2)},     # 复位
    "ng_sample":    {StationId.A: (1, 2), StationId.B: (1, 4)},     # 工位NG样件需求
    "ok_sample":    {StationId.A: (1, 3), StationId.B: (1, 5)},     # 工位OK样件需求
    "calibration":  {StationId.A: (1, 0), StationId.B: (1, 1)},     # 工位校准时间到
    "start":        {StationId.A: (16, 0), StationId.B: (16, 1)},   # 启动信号
    "manual":       {StationId.A: (2, 0), StationId.B: (2, 1)},     # 切换为手动
    "scan_ok":      {StationId.A: (0, 1), StationId.B: (0, 0)},     # 扫码OK
    "block":        {StationId.A: (4, 2), StationId.B: (3, 2)},     # 手动封堵
    "stamp":        {StationId.A: (4, 6), StationId.B: (3, 6)},     # 手动盖章
    "clamp":        {StationId.A: (4, 4), StationId.B: (3, 4)},     # 手动夹紧
    "transfer":     {StationId.A: (4, 0), StationId.B: (3, 0)},     # 手动移载
    "pressure":     {StationId.A: (0, 5), StationId.B: (0, 4)},     # 正负压开启
}
# Legacy widget-name alias kept for both UI layers; same physical point.
POINTS["calibration_due"] = POINTS["calibration"]

class FakePlc:
    def __init__(self) -> None:
        self._bits: dict[tuple[int, int], bool] = {}
        self._lock = Lock()
        self.connected = True
        self.last_safe_stop = ""

    def read_bit(self, byte: int, bit: int) -> bool:
        with self._lock:
            return self._bits.get((byte, bit), False)

    def write_bit(self, byte: int, bit: int, value: bool) -> None:
        if not self.connected:
            raise ConnectionError("PLC simulator disconnected")
        with self._lock:
            self._bits[(byte, bit)] = value

    def health(self) -> bool:
        return self.connected

    def safe_stop(self, reason: str) -> None:
        with self._lock:
            self._bits.clear()
        self.last_safe_stop = reason

    def outputs_energized(self) -> bool:
        with self._lock:
            return any(self._bits.values())


class Snap7Plc:
    """S7-200 SMART adapter over python-snap7 (pure-Python wheel, no DLL).

    Reads are available as soon as connect() succeeds, which makes the
    point map in POINTS verifiable against the real program.  Writes stay
    behind enable_writes(): M-area bit ownership and write atomicity must
    be confirmed against the actual PLC program before any live write.
    """
    def __init__(self, ip: str, rack: int = 0, slot: int = 1) -> None:
        import ipaddress
        ipaddress.ip_address(ip)
        self.ip, self.rack, self.slot = ip, rack, slot
        self.connected = False
        self._writes_enabled = False
        self._client = None
        self._area = None
        self.last_error = ""
        self._io_lock = RLock()

    def enable_writes(self, approved: bool) -> None:
        self._writes_enabled = bool(approved)

    def connect(self) -> None:
        if self.connected and self._client is not None:
            return
        try:
            import snap7
        except ImportError as exc:
            raise RuntimeError("python-snap7 未安装，无法连接 PLC") from exc
        try:
            from snap7.type import Area
        except ImportError:  # python-snap7 1.x compatibility
            from snap7.types import Areas as Area
        self._area = Area
        client = snap7.client.Client()
        try:
            client.connect(self.ip, self.rack, self.slot)
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise RuntimeError(f"PLC {self.ip} 连接失败: {self.last_error}") from exc
        self._client = client
        self.connected = True
        self.last_error = ""

    def disconnect(self) -> None:
        client, self._client = self._client, None
        self.connected = False
        if client is not None:
            try:
                client.disconnect()
            except Exception:
                pass

    def _require_connection(self) -> None:
        if self._client is None or self._area is None:
            raise RuntimeError("PLC 未连接，先调用 connect()")
        if not self.connected:
            raise RuntimeError(f"PLC {self.ip} 连接已断开: {self.last_error}")

    def read_bytes(self, start: int, count: int) -> bytearray:
        with self._io_lock:
            self._require_connection()
            try:
                return self._client.read_area(self._area.MK, 0, start, count)
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                self.connected = False
                raise RuntimeError(f"PLC {self.ip} 读取失败: {self.last_error}") from exc

    def read_byte(self, byte: int) -> int:
        return self.read_bytes(byte, 1)[0]

    def read_bit(self, byte: int, bit: int) -> bool:
        if not 0 <= bit <= 7:
            raise ValueError(f"无效位号: {bit}")
        return bool(self.read_byte(byte) & (1 << bit))

    def write_bit(self, byte: int, bit: int, value: bool) -> None:
        if not self._writes_enabled:
            raise PermissionError("PLC 写入被 capability policy 拒绝")
        if not 0 <= bit <= 7:
            raise ValueError(f"无效位号: {bit}")
        with self._io_lock:
            self._require_connection()
            current = self.read_byte(byte)
            mask = 1 << bit
            updated = (current | mask) if value else (current & ~mask)
            if updated == current:
                return
            try:
                self._client.write_area(self._area.MK, 0, byte, bytearray([updated]))
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                self.connected = False
                raise RuntimeError(f"PLC {self.ip} 写入失败: {self.last_error}") from exc

    def health(self) -> bool:
        if not self.connected or self._client is None:
            return False
        try:
            return bool(self._client.get_connected())
        except Exception:
            return False

    def safe_stop(self, reason: str) -> None:
        self._writes_enabled = False
        self.disconnect()

    def outputs_energized(self) -> bool:
        # The confirmed start signals live at M16.0/M16.1, so include M0..M16
        # rather than the old eight-byte window when checking safe-stop state.
        try:
            return any(self.read_bytes(0, 17))
        except RuntimeError:
            return False


def signal_scan_and_wait_clear(plc, station: StationId, timeout_s: float = 5.0,
                               poll_s: float = 0.05) -> None:
    """Set the confirmed A/B scan-OK bit once and wait for PLC clear."""
    byte, bit = POINTS["scan_ok"][station]
    if plc.read_bit(byte, bit):
        raise RuntimeError(f"工位 {station.value} 扫码位在触发前已为 1")
    plc.write_bit(byte, bit, True)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not plc.read_bit(byte, bit):
            return
        time.sleep(poll_s)
    raise TimeoutError(f"PLC 未在 {timeout_s:g} 秒内清零 M{byte}.{bit}")
