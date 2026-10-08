"""ATEQ request correlation, simulator and fail-closed serial adapter."""
from __future__ import annotations
from .models import Measurement, Result
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class AteqRequest:
    station: str
    cycle_id: str
    program: str
    sequence: int
    timestamp: str

@dataclass(frozen=True)
class AteqResponse:
    request: AteqRequest
    measurement: Measurement
    raw_frame: bytes = b""

def modbus_crc16(payload: bytes) -> int:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc

class FakeAteq:
    def __init__(self, result: Result = Result.OK, station: str = "SIM") -> None:
        self.result = result
        self.station = station
        self.connected = True
        self.program = ""
        self.requests: list[AteqRequest] = []

    def select_program(self, program: str) -> None:
        if not self.connected:
            raise ConnectionError("ATEQ simulator disconnected")
        self.program = program

    def run(self, request: AteqRequest | str = "", sequence: int = 1) -> AteqResponse:
        if not self.connected:
            raise ConnectionError("ATEQ simulator disconnected")
        if isinstance(request, str):
            request = AteqRequest(self.station, request, self.program, sequence, datetime.now(timezone.utc).isoformat())
        self.requests.append(request)
        # A simulator frame is deliberately marked; controller rejects empty,
        # cycle-id-only or mismatched raw payloads before DB advancement.
        frame = b"SIMFRAME:" + request.cycle_id.encode()
        measurement = Measurement(pressure=10.0, leakage=0.02, result=self.result, raw_frame=frame)
        return AteqResponse(request, measurement, frame)


class SerialAteq:
    """Strict production shell. Unknown F620 command framing is a blocker.

    The adapter validates resources but intentionally refuses to invent a
    command protocol from incomplete Main.vi evidence. A future validated
    protocol implementation can replace ``exchange`` without changing the
    controller contract.
    """
    def __init__(self, port: str, station: str, baudrate: int = 9600) -> None:
        self.port, self.station, self.baudrate = port, station, baudrate
        self.program = ""
        self.connected = False

    def select_program(self, program: str) -> None:
        if not program.strip():
            raise ValueError("ATEQ 程序号不能为空")
        self.program = program
        raise RuntimeError("LIVE_BLOCKED: ATEQ F620 命令协议尚未由实采帧确认")

    def run(self, request: AteqRequest) -> AteqResponse:
        self._validate(request)
        raise RuntimeError("LIVE_BLOCKED: ATEQ F620 命令协议尚未由实采帧确认")

    def _validate(self, request: AteqRequest) -> None:
        if request.station != self.station or request.program != self.program:
            raise ValueError("ATEQ 请求身份不一致")
        if request.sequence < 1 or not request.cycle_id:
            raise ValueError("ATEQ 请求序列或周期无效")
