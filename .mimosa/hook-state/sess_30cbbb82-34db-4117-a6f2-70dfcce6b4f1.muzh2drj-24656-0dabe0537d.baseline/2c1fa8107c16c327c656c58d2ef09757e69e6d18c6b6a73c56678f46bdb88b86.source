"""BarTender label printing, reproducing the original Barcode.py contract.

The label templates in D:\\data bind one BarTender text-file data source
per field.  The original system (Barcode.py) wrote these per-field txt
files in UTF-8 and invoked bartend.exe as:

    bartend.exe D:\\data\\<part>-<station>.btw /p/min=SystemTray

This module keeps that contract:

* write_field_files() refreshes the per-field txt files the template
  reads (二维码/序列号/产品型号/作业员/客户产品号/正压值/负压值/打印路径).
* label_data.txt is additionally written as a single-row summary of the
  cycle for human review; no template depends on it.
* Explicit per-model A/B template selections may use any ordinary .btw
  basename in D:\\data. Legacy records without an explicit selection still
  resolve <part_no>-<station>.btw.
* Printing is explicit and single-shot: no implicit retry, a failed
  print returns a rejected receipt for the operator to handle.
"""
from __future__ import annotations
import subprocess
from datetime import datetime
from pathlib import Path
from threading import Lock

from .models import StationId, TraceRecord
from .printer import PrintReceipt
from .barcode_rules import _atomic_write

_PRINT_TRANSACTION_LOCK = Lock()

LABEL_HEADER = ["station", "serial_no", "code_2d", "part_no", "person",
                "test1_pressure", "test1_leakage", "test1_result",
                "test2_pressure", "test2_leakage", "test2_result",
                "final_result", "cycle_id", "print_time"]

# 模板直接读取的按字段 txt（工位相关）
FIELD_FILES = {
    "code_2d": "二维码{station}.txt",
    "serial_no": "序列号{station}.txt",
    "part_no": "产品型号.txt",
    "person": "作业员.txt",
    "customer": "客户产品号{station}.txt",
    "positive": "正压值{station}.txt",
    "negative": "负压值{station}.txt",
    "template": "打印路径{station}.txt",
}

CALIBRATION_TEMPLATE_PREFIXES = ("cal_ng_", "cal_ok_", "calibration_")


def is_calibration_template(path: str | Path) -> bool:
    """Keep dedicated calibration labels out of normal production templates."""
    return Path(path).stem.casefold().startswith(CALIBRATION_TEMPLATE_PREFIXES)


def record_to_row(record: TraceRecord) -> list[str]:
    final = ""
    if record.second is not None:
        final = record.second.result.value
    elif record.first is not None:
        final = record.first.result.value

    def fmt(measurement, key: str) -> str:
        if measurement is None:
            return ""
        if key == "result":
            return measurement.result.value
        value = getattr(measurement, key)
        return "" if value is None else str(value)

    return [
        record.station.value,
        record.serial_no,
        record.code_2d,
        record.part_no,
        record.person,
        fmt(record.first, "pressure"), fmt(record.first, "leakage"), fmt(record.first, "result"),
        fmt(record.second, "pressure"), fmt(record.second, "leakage"), fmt(record.second, "result"),
        final,
        record.cycle_id,
        record.created_at.astimezone().strftime("%Y-%m-%d-%H:%M:%S"),
    ]


def _csv_cell(value: str) -> str:
    if any(ch in value for ch in (",", '"', "\r", "\n")):
        return '"' + value.replace('"', '""') + '"'
    return value


def write_label_data(record: TraceRecord, path: Path) -> Path:
    row = record_to_row(record)
    lines = [",".join(LABEL_HEADER), ",".join(_csv_cell(cell) for cell in row)]
    _atomic_write(path, "\r\n".join(lines) + "\r\n")
    return path


def _measurement_line(measurement, when=None) -> str:
    if measurement is None:
        return ""
    # 原系统格式: 2025/12/25 09:31:33 250.870Kpa  9.640ml/min
    # 时间戳取自本机时刻，固定为 年月日-HH:MM:SS；真实 Modbus 原始帧
    # 是二进制，无法像旧系统那样从中取出可打印时间。
    stamp = (when or datetime.now()).strftime("%Y-%m-%d-%H:%M:%S")
    return f"{stamp} {measurement.pressure:.3f}Kpa  {measurement.leakage:.3f}ml/min"


def write_field_files(record: TraceRecord, data_dir: Path, template: Path) -> dict[str, Path]:
    """Refresh the per-field txt files the label template reads (UTF-8)."""
    station = record.station.value
    values = {
        "code_2d": record.code_2d or "",
        "serial_no": record.serial_no or "",
        "part_no": record.part_no or "",
        "person": record.person or "",
        "customer": record.customer_no or "",
        "positive": _measurement_line(record.first),
        "negative": _measurement_line(record.second),
        "template": str(template),
    }
    written: dict[str, Path] = {}
    for key, name in FIELD_FILES.items():
        path = Path(data_dir) / name.format(station=station)
        _atomic_write(path, values[key])
        written[key] = path
    return written


def bartend_command(executable: Path, template: Path, close_after: bool = True) -> list[str]:
    parts = [str(executable), str(template), "/p/min=SystemTray"]
    if close_after:
        parts.append("/X")
    return parts


def resolve_template(template_dir: Path, part_no: str, station: StationId) -> Path:
    wanted = f"{part_no.strip()}-{station.value}".lower()
    candidates = [p for p in Path(template_dir).glob("*.btw") if p.stem.lower() == wanted]
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise FileNotFoundError(f"模板不唯一: {wanted} 匹配 {[str(p) for p in candidates]}")
    available = sorted(p.name for p in Path(template_dir).glob("*.btw"))
    raise FileNotFoundError(
        f"未找到模板 {part_no}-{station.value}.btw; 模板目录现有: {available}")


def _validate_production_template(template_dir: Path, candidate: str | Path) -> Path:
    """Require a real production .btw under Data, without constraining its name."""
    root = Path(template_dir).resolve()
    path = Path(candidate)
    if not path.is_absolute():
        path = root / path
    path = path.resolve(strict=True)
    if not path.is_file():
        raise FileNotFoundError(f"打印模板不是文件: {path}")
    if path.suffix.casefold() != ".btw":
        raise ValueError(f"生产打印模板必须是 .btw 文件: {path}")
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"生产打印模板必须位于 Data 文件夹内: {path}") from exc
    if is_calibration_template(path):
        raise ValueError(f"校准模板不能作为普通生产模板: {path.name}")
    return path


class BarTenderCmdPrinter:
    """Single-shot BarTender printer driven through bartend.exe."""

    def __init__(self, executable: Path, template_dir: Path, data_file: Path,
                 close_after: bool = True, timeout_s: float = 120.0) -> None:
        self.executable, self.template_dir = Path(executable), Path(template_dir)
        self.data_file = Path(data_file)
        self.close_after, self.timeout_s = close_after, timeout_s
        self.intents: set[str] = set()
        self.calibration_intents: set[str] = set()
        self._lock = Lock()

    def print_label(self, record: TraceRecord) -> PrintReceipt:
        if not self.executable.exists():
            raise FileNotFoundError(f"BarTender 可执行文件不存在: {self.executable}")
        job_id = f"label-{record.cycle_id}"
        # A/B share one printer and a common set of TXT data sources.  The
        # complete write -> invoke -> receipt sequence must therefore be atomic.
        with _PRINT_TRANSACTION_LOCK, self._lock:
            if record.cycle_id in self.intents:
                return PrintReceipt(True, job_id, f"receipt-{record.cycle_id}")
            template = Path(record.template_path) if record.template_path else resolve_template(
                self.template_dir, record.part_no or "", record.station)
            template = _validate_production_template(self.template_dir, template)
            self.intents.add(record.cycle_id)
            write_field_files(record, self.template_dir, template)
            write_label_data(record, self.data_file)
            command = bartend_command(self.executable, template, self.close_after)
            try:
                completed = subprocess.run(command, capture_output=True, text=True,
                                           timeout=self.timeout_s, encoding="utf-8",
                                           errors="replace")
            except subprocess.TimeoutExpired as exc:
                return PrintReceipt(False, job_id, f"BarTender 打印超时({self.timeout_s}s): {exc}")
            if completed.returncode == 0:
                return PrintReceipt(True, job_id, f"{template.name} 已发送打印")
            detail = (completed.stderr or completed.stdout or "").strip()
            return PrintReceipt(False, job_id, f"BarTender 返回码 {completed.returncode}: {detail}")

    def print_calibration(self, station: StationId, result: str,
                          measurement=None, when=None) -> PrintReceipt:
        """Print one real calibration label using the station-specific template.

        The measurement line (年月日-HH:MM:SS 压力 泄漏) is written to both
        正压值/负压值 station sources while the print lock is held, so the
        label carries the actual sample values instead of stale files.
        """
        result = str(result).strip().upper()
        if result not in ("NG", "OK"):
            raise ValueError("校准标签结果必须是 NG/OK")
        if not self.executable.exists():
            raise FileNotFoundError(f"BarTender 可执行文件不存在: {self.executable}")
        template = self.template_dir / f"Cal_{result}_{station.value}.btw"
        if not template.is_file():
            raise FileNotFoundError(f"校准模板不存在: {template}")
        job_id = f"cal-{result}-{station.value}"
        with _PRINT_TRANSACTION_LOCK, self._lock:
            if job_id in self.calibration_intents:
                return PrintReceipt(True, job_id, f"receipt-{job_id}")
            # Legacy calibration BTW files reference the common QR source
            # “二维码.txt”. Populate it from the station source while
            # holding the print lock, so A/B jobs cannot cross-write it.
            station_qr = self.template_dir / f"二维码{station.value}.txt"
            if not station_qr.is_file():
                raise FileNotFoundError(f"校准二维码源不存在: {station_qr}")
            _atomic_write(
                self.template_dir / "二维码.txt",
                station_qr.read_text(encoding="utf-8").strip())
            _atomic_write(self.template_dir / f"打印路径{station.value}.txt", str(template))
            measurement_line = _measurement_line(measurement, when)
            _atomic_write(self.template_dir / f"正压值{station.value}.txt", measurement_line)
            _atomic_write(self.template_dir / f"负压值{station.value}.txt", measurement_line)
            completed = subprocess.run(
                bartend_command(self.executable, template, self.close_after),
                capture_output=True, text=True, timeout=self.timeout_s,
                encoding="utf-8", errors="replace")
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout or "").strip()
                return PrintReceipt(False, job_id, f"BarTender 返回码 {completed.returncode}: {detail}")
            self.calibration_intents.add(job_id)
            return PrintReceipt(True, job_id, f"{template.name} 已发送打印")
