"""Print intent with duplicate protection; real BarTender is never called in SIMULATE."""
from __future__ import annotations
from .models import TraceRecord, StationId
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
        self.calibration_intents: set[str] = set()
        self._lock = Lock()

    def print_label(self, record: TraceRecord) -> PrintReceipt:
        with self._lock:
            if record.cycle_id in self.intents:
                return PrintReceipt(True, f"label-{record.cycle_id}", f"receipt-{record.cycle_id}")
            self.intents.add(record.cycle_id)
            return PrintReceipt(True, f"label-{record.cycle_id}", f"receipt-{record.cycle_id}")

    def print_calibration(self, station: StationId, result: str,
                          measurement=None, when=None) -> PrintReceipt:
        """Record one idempotent calibration-label print intent."""
        result = str(result).strip().upper()
        if result not in ("NG", "OK"):
            raise ValueError("校准标签结果必须是 NG/OK")
        job_id = f"cal-{result}-{station.value}"
        with self._lock:
            self.calibration_intents.add(job_id)
        return PrintReceipt(True, job_id, f"receipt-{job_id}")


class BarTenderPrinter:
    """Production adapter with explicit invocation and no implicit retry."""
    def __init__(self, executable: Path, template: Path) -> None:
        self.executable, self.template = executable, template

    def print_label(self, record: TraceRecord) -> PrintReceipt:
        if not self.executable.exists() or not self.template.exists():
            raise RuntimeError("LIVE_BLOCKED: BarTender 可执行文件或模板不存在")
        job_id = f"label-{record.cycle_id}"
        raise RuntimeError("LIVE_BLOCKED: BarTender 回执协议尚未确认，禁止盲目打印")

    def print_calibration(self, station: StationId, result: str,
                          measurement=None, when=None) -> PrintReceipt:
        """Keep calibration printing behind the same LIVE receipt gate."""
        result = str(result).strip().upper()
        if result not in ("NG", "OK"):
            raise ValueError("校准标签结果必须是 NG/OK")
        template = self.template.with_name(f"Cal_{result}_{station.value}.btw")
        if not self.executable.exists() or not template.exists():
            raise RuntimeError("LIVE_BLOCKED: 校准 BarTender 可执行文件或模板不存在")
        raise RuntimeError("LIVE_BLOCKED: BarTender 校准标签回执协议尚未确认，禁止盲目打印")
