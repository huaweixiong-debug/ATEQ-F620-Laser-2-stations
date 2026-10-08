"""Scanner framing and duplicate guard, independent per station."""
from __future__ import annotations
from dataclasses import dataclass
from time import monotonic

@dataclass
class ScannerGuard:
    duplicate_window_s: float = 2.0
    _last: str = ""
    _at: float = 0.0

    def accept(self, code: str) -> bool:
        value = code.strip()
        now = monotonic()
        duplicate = value == self._last and now - self._at < self.duplicate_window_s
        if duplicate or not value:
            return False
        self._last, self._at = value, now
        return True

class ScannerFramer:
    def __init__(self, terminator: bytes = b"\r") -> None:
        self.terminator, self.buffer = terminator, bytearray()
    def feed(self, data: bytes) -> list[str]:
        self.buffer.extend(data); result = []
        while self.terminator in self.buffer:
            raw, self.buffer = self.buffer.split(self.terminator, 1)
            if raw: result.append(raw.decode("utf-8", errors="replace"))
        return result


class SerialScanner:
    """Serial scanner boundary; construction validates only, I/O is explicit."""
    def __init__(self, port: str, baudrate: int = 9600) -> None:
        if not port.upper().startswith("COM") or not port[3:].isdigit():
            raise ValueError("无效扫码器 COM 口")
        if not 1200 <= baudrate <= 115200:
            raise ValueError("无效扫码器波特率")
        self.port, self.baudrate, self.framer = port, baudrate, ScannerFramer()
        self._serial = None

    def open(self) -> None:
        raise RuntimeError("LIVE_BLOCKED: 扫码器帧格式和资源独占尚未由现场证据确认")

    def feed(self, data: bytes) -> list[str]:
        return self.framer.feed(data)

    def close(self) -> None:
        if self._serial is not None:
            self._serial.close(); self._serial = None
