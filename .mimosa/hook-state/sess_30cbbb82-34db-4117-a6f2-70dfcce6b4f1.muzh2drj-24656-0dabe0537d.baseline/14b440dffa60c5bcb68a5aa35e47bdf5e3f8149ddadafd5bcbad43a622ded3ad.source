"""Capability-gated service composition for SIMULATE/SHADOW/LIVE."""
from __future__ import annotations
from dataclasses import dataclass
from .models import RunMode, StationId
from .config import Settings
from .ateq import FakeAteq, SerialAteq
from .plc import FakePlc, Snap7Plc
from .printer import FakePrinter, BarTenderPrinter
from .repository import FakeRepository, PyMySQLRepository
from .scanner import SerialScanner

class ReadOnlyPlc:
    def __init__(self): self._inner = FakePlc()
    def read_bit(self, byte: int, bit: int) -> bool: return self._inner.read_bit(byte, bit)
    def write_bit(self, byte: int, bit: int, value: bool) -> None: raise PermissionError("SHADOW 禁止 PLC 写入")
    def health(self) -> bool: return False
    def safe_stop(self, reason: str) -> None: self._inner.safe_stop(reason)
    def outputs_energized(self) -> bool: return self._inner.outputs_energized()

class ReadOnlyAteq:
    station = "SHADOW"
    program = ""
    def select_program(self, program: str) -> None: raise PermissionError("SHADOW 禁止 ATEQ 命令")
    def run(self, request): raise PermissionError("SHADOW 只能使用离线重放帧")

class ReadOnlyPrinter:
    def print_label(self, record) -> None: raise PermissionError("SHADOW 禁止打印")

class ReadOnlyRepository:
    def insert_stage1(self, record, capability=None): raise PermissionError("SHADOW 禁止数据库写入")
    def update_stage2(self, cycle_id, measurement, capability=None): raise PermissionError("SHADOW 禁止数据库写入")
    def mark_labeled(self, cycle_id, capability=None): raise PermissionError("SHADOW 禁止数据库写入")
    def row_id(self, cycle_id): return None
    def query(self, text=""): return []

@dataclass(frozen=True)
class CapabilityPolicy:
    mode: RunMode
    writes_allowed: bool = False
    physical_io_allowed: bool = False

    def require(self, capability: str) -> None:
        if self.mode is not RunMode.LIVE or not self.physical_io_allowed:
            raise PermissionError(f"{capability} 被 {self.mode.value} capability policy 拒绝")

def build_services(settings: Settings, station: StationId = StationId.A):
    policy = CapabilityPolicy(settings.mode)
    if settings.mode is RunMode.SIMULATE:
        return policy, FakePlc(), FakeAteq(station=station.value), FakeRepository(settings), FakePrinter()
    if settings.mode in (RunMode.CHARACTERIZATION, RunMode.SHADOW):
        # Shadow deliberately uses replay/Fake services; no serial, PLC, DB or
        # printer resource is opened and no write method can be reached.
        return policy, ReadOnlyPlc(), ReadOnlyAteq(), ReadOnlyRepository(), ReadOnlyPrinter()
    policy.require("live composition")
    raise RuntimeError("LIVE_BLOCKED: production composition requires现场书面批准和已验证协议")
