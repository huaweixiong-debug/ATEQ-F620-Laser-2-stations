"""PLC simulator and Snap7 adapter (reads live, writes gated).

Point addresses live in ``app/points.py`` + ``config/points.toml``; the
电气点位表 is a configuration input, not code.
"""
from __future__ import annotations
from dataclasses import dataclass
from threading import Lock, RLock
import time


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
    configured point map verifiable against the real program.  Writes stay
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
        try:
            return any(self.read_bytes(0, 21))
        except RuntimeError:
            return False
