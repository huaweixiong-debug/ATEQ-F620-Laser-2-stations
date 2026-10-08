"""Print intent with duplicate protection; real BarTender is never called in SIMULATE."""
from __future__ import annotations
from .models import TraceRecord
from threading import Lock
from dataclasses import dataclass
from pathlib import Path
import subprocess

@dataclass(frozen=True)
class PrintReceipt:
    accepted: bool
    job_id: str
    receipt: str = ""

class FakePrinter:
    def __init__(self) -> None:
        self.intents: set[str] = set()
        self._lock = Lock()

    def print_label(self, record: TraceRecord) -> PrintReceipt:
        with self._lock:
            if record.cycle_id in self.intents:
                return PrintReceipt(True, f"label-{record.cycle_id}", f"receipt-{record.cycle_id}")
            self.intents.add(record.cycle_id)
            return PrintReceipt(True, f"label-{record.cycle_id}", f"receipt-{record.cycle_id}")


class BarTenderPrinter:
    """Production adapter with explicit invocation and no implicit retry."""
    def __init__(self, executable: Path, template: Path) -> None:
        self.executable, self.template = executable, template

    def print_label(self, record: TraceRecord) -> PrintReceipt:
        if not self.executable.exists() or not self.template.exists():
            raise RuntimeError("LIVE_BLOCKED: BarTender 可执行文件或模板不存在")
        job_id = f"label-{record.cycle_id}"
        raise RuntimeError("LIVE_BLOCKED: BarTender 回执协议尚未确认，禁止盲目打印")
