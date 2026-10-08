"""PLC point map and simulator. Real Snap7 adapter is intentionally gated."""
from __future__ import annotations
from dataclasses import dataclass
from threading import Lock
from .models import StationId

POINTS = {
    "scan_ok": {StationId.A: (0, 1), StationId.B: (0, 0)},
    "reset": {StationId.A: (0, 3), StationId.B: (0, 2)},
    "pressure": {StationId.A: (0, 5), StationId.B: (0, 4)},
    "door_disable": {StationId.A: (0, 6), StationId.B: (0, 7)},
    "calibration_due": {StationId.A: (1, 0), StationId.B: (1, 1)},
    "ng_sample": {StationId.A: (1, 2), StationId.B: (1, 4)},
    "ok_sample": {StationId.A: (1, 3), StationId.B: (1, 5)},
    "manual": {StationId.A: (2, 0), StationId.B: (2, 1)},
    "transfer": {StationId.A: (4, 0), StationId.B: (3, 0)},
    "block": {StationId.A: (4, 2), StationId.B: (3, 2)},
    "clamp": {StationId.A: (4, 4), StationId.B: (3, 4)},
    "stamp": {StationId.A: (4, 6), StationId.B: (3, 6)},
    "start": {StationId.A: (16, 0), StationId.B: (16, 1)},
}

class FakePlc:
    def __init__(self) -> None:
        self._bits: dict[tuple[int, int], bool] = {}
        self._lock = Lock()
        self.connected = True

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
    """Capability-gated S7-200 SMART boundary.

    Marker ownership and write atomicity must be confirmed against the actual
    PLC program before any live write is allowed.
    """
    def __init__(self, ip: str, rack: int = 0, slot: int = 1) -> None:
        import ipaddress
        ipaddress.ip_address(ip)
        self.ip, self.rack, self.slot = ip, rack, slot
        self.connected = False
        self._writes_enabled = False

    def enable_writes(self, approved: bool) -> None:
        self._writes_enabled = bool(approved)

    def read_bit(self, byte: int, bit: int) -> bool:
        raise RuntimeError("LIVE_BLOCKED: Snap7 PLC 连接未在现场门禁后启用")

    def write_bit(self, byte: int, bit: int, value: bool) -> None:
        if not self._writes_enabled:
            raise PermissionError("PLC 写入被 capability policy 拒绝")
        raise RuntimeError("LIVE_BLOCKED: PLC M 区原子位写/所有权证据尚未确认")

    def health(self) -> bool:
        return False

    def safe_stop(self, reason: str) -> None:
        self._writes_enabled = False

    def outputs_energized(self) -> bool:
        return False
