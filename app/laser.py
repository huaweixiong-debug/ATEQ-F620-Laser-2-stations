"""Laser marking adapter: mark-text file publication + PLC start-bit pulse.

移植自 xiezhong-heating（laser_files.py / live_laser.py），按本项目需求简化：

1. 打码内容是 5 行文本（时间/产品型号/负压压力+负压泄漏/正压压力+正压泄漏/
   结果+操作工+当日序号），单 TXT 文件（无二维码文件、无客户码分配）。
2. 写文件（临时文件 + os.replace + 回读字节校验）成功后才允许触碰 PLC；
   任何文件失败都绝不发出打码脉冲（fail-closed）。
3. 启动位脉冲：置位 → 保持 hold_seconds（带一次回读诊断）→ 复位 →
   settle；可选等待 laser_done 完成位。
4. 打码后 clear_after_seconds（默认 10 秒）自毁清空文件，防止下一件
   误用旧内容；新打码会取消未决的清空定时器。
"""
from __future__ import annotations

import os
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .contracts import MarkerPort
from .date_codes import DateCodeCatalog, DateCodeError
from .models import Measurement, TraceRecord

DEFAULT_TEMPLATE: tuple[str, ...] = (
    "{time}", "{part_no}", "{p1} {l1}", "{p2} {l2}", "{result} {person} {sequence}",
)


class LaserFileError(RuntimeError):
    """The mark-text file could not be published and verified."""


@dataclass(frozen=True)
class LaserChannel:
    path: Path | str
    encoding: str = "gbk"
    newline: str = "\r\n"

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("laser file path is required")
        if self.newline not in ("", "\n", "\r\n"):
            raise ValueError("newline must be '', '\\n', or '\\r\\n'")


@dataclass(frozen=True)
class MarkReceipt:
    accepted: bool
    job_id: str
    receipt: str = ""


class LaserFileWriter:
    """Publish exactly one mark text to the configured watched file."""

    def __init__(self, channel: LaserChannel) -> None:
        self.channel = channel
        self._lock = threading.RLock()

    @property
    def path(self) -> Path:
        return Path(self.channel.path)

    def _encode(self, text: str) -> bytes:
        lines = text.splitlines() or [""]
        payload = self.channel.newline.join(lines)
        if lines != [""]:
            payload += self.channel.newline
        return payload.encode(self.channel.encoding)

    def publish(self, text: str) -> None:
        """Atomically replace the watched file, then verify byte-for-byte."""
        payload = self._encode(text)
        path = self.path
        with self._lock:
            temp = self._write_temp(path, payload)
            old = self._read_bytes(path)
            try:
                os.replace(str(temp), str(path))
            except BaseException:
                try:
                    temp.unlink()
                except OSError:
                    pass
                self._restore(path, old)
                raise LaserFileError(f"failed to publish laser file {path}")
            if self._read_bytes(path) != payload:
                raise LaserFileError(f"laser file verification failed after publish: {path}")

    def verify(self, text: str) -> bool:
        with self._lock:
            return self._read_bytes(self.path) == self._encode(text)

    def clear(self) -> None:
        """Empty the watched file (self-destruct after marking)."""
        with self._lock:
            path = self.path
            temp = self._write_temp(path, b"")
            old = self._read_bytes(path)
            try:
                os.replace(str(temp), str(path))
            except BaseException:
                try:
                    temp.unlink()
                except OSError:
                    pass
                self._restore(path, old)
                raise LaserFileError(f"failed to clear laser file {path}")

    def preflight(self) -> None:
        """Verify the watched directory is writable without touching content."""
        path = self.path
        if not path.parent.is_dir():
            raise LaserFileError(f"激光监听目录不存在: {path.parent}")
        if not os.access(path.parent, os.W_OK):
            raise LaserFileError(f"激光监听目录不可写: {path.parent}")
        if path.is_file():
            with path.open("rb") as handle:
                handle.read(1)

    @staticmethod
    def _read_bytes(path: Path) -> bytes | None:
        try:
            return path.read_bytes()
        except OSError:
            return None

    @staticmethod
    def _write_temp(path: Path, payload: bytes) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, raw_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        temp = Path(raw_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            try:
                temp.unlink()
            except OSError:
                pass
            raise
        return temp

    @staticmethod
    def _restore(path: Path, old: bytes | None) -> None:
        if old is None:
            try:
                path.unlink()
            except OSError:
                pass
            return
        try:
            temp = LaserFileWriter._write_temp(path, old)
            os.replace(str(temp), str(path))
        except OSError:
            pass


def _measurement_text(value: Measurement | None, field: str) -> str:
    if value is None:
        return ""
    number = getattr(value, field)
    unit = value.pressure_unit if field == "pressure" else value.leakage_unit
    return f"{number:.3f}{unit}" if unit else f"{number:.3f}"


def build_mark_text(record: TraceRecord, *, template: tuple[str, ...] = DEFAULT_TEMPLATE,
                    date_code_fn: Callable[[str, datetime], str] | None = None) -> str:
    """Build the mark text lines from one committed record (打的数据=存的数据)."""
    if record.first is None:
        raise ValueError("打码记录缺少第一次测量")
    if record.test_mode == "dual" and record.second is None:
        raise ValueError("双测记录缺少第二次测量，禁止打码")
    measurement = record.second or record.first
    date_code = record.created_at.strftime("%Y%m%d")
    if date_code_fn is not None:
        try:
            date_code = date_code_fn(record.date_scheme, record.created_at)
        except DateCodeError:
            if record.date_scheme not in ("YYYYMMDD",):
                raise
    values = {
        "date": date_code,
        "time": record.created_at.astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "part_no": record.part_no.strip(),
        "p1": _measurement_text(record.first, "pressure"),
        "l1": _measurement_text(record.first, "leakage"),
        "p2": _measurement_text(record.second, "pressure"),
        "l2": _measurement_text(record.second, "leakage"),
        "result": measurement.result.value,
        "person": record.person.strip(),
        "sequence": (getattr(record, "daily_sequence", "") or "").strip(),
    }
    if any(not values[key] for key in ("time", "part_no", "result", "person")):
        raise ValueError(f"打码字段不完整: {sorted(key for key in ('time', 'part_no', 'result', 'person') if not values[key])}")
    lines = [line.format_map(values).rstrip() for line in template]
    return "\n".join(lines)


class LaserMarker(MarkerPort):
    """File + PLC start-bit production marker (single channel per station)."""

    def __init__(self, writer: LaserFileWriter, plc, point_map,
                 *, hold_seconds: float = 1.0, settle_seconds: float = 0.2,
                 clear_after_seconds: float = 10.0, wait_done: bool = False,
                 done_timeout_s: float = 10.0,
                 template: tuple[str, ...] = DEFAULT_TEMPLATE,
                 date_code_fn: Callable[[str, datetime], str] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.writer = writer
        self.plc = plc
        self.point_map = point_map
        self.hold_seconds = hold_seconds
        self.settle_seconds = settle_seconds
        self.clear_after_seconds = clear_after_seconds
        self.wait_done = wait_done
        self.done_timeout_s = done_timeout_s
        self.template = tuple(template)
        self.date_code_fn = date_code_fn
        self._clock = clock
        self._clear_timer: threading.Timer | None = None
        self._clear_lock = threading.RLock()  # 重入：_schedule_clear 持锁时调用 _cancel_clear_timer

    def mark(self, record: TraceRecord) -> MarkReceipt:
        job_id = f"mark-{record.cycle_id}"
        try:
            text = build_mark_text(record, template=self.template, date_code_fn=self.date_code_fn)
        except Exception as exc:
            return MarkReceipt(False, job_id, f"打码内容生成失败: {exc}")
        self._cancel_clear_timer()  # protect fresh files
        try:
            self.writer.publish(text)
        except Exception as exc:
            return MarkReceipt(False, job_id, f"打码文件写入失败: {exc}")
        byte, bit = self.point_map.address("laser_start")
        try:
            self.plc.write_bit(byte, bit, True)
            if not self.plc.read_bit(byte, bit):
                raise RuntimeError(f"激光启动位 M{byte}.{bit} 置位后回读为低")
            self._hold_with_diagnostic(byte, bit)
            self.plc.write_bit(byte, bit, False)
            if self.plc.read_bit(byte, bit):
                raise RuntimeError(f"激光启动位 M{byte}.{bit} 复位后回读为高")
            time.sleep(self.settle_seconds)
            if self.wait_done and self.point_map.has("laser_done"):
                done_byte, done_bit = self.point_map.address("laser_done")
                deadline = self._clock() + self.done_timeout_s
                while not self.plc.read_bit(done_byte, done_bit):
                    if self._clock() >= deadline:
                        raise RuntimeError(f"打码完成位 M{done_byte}.{done_bit} 在 {self.done_timeout_s:g} 秒内未置位")
                    time.sleep(0.05)
        except Exception as exc:
            deenergized = self._deenergize_start_bit(byte, bit)
            self._schedule_clear()
            note = "" if deenergized else "；启动位断电未确认"
            return MarkReceipt(False, job_id, f"激光启动失败: {exc}{note}")
        self._schedule_clear()
        return MarkReceipt(True, job_id, f"receipt-{job_id}")

    def _deenergize_start_bit(self, byte: int, bit: int) -> bool:
        """Best-effort start-bit reset; False means the de-energize write failed."""
        try:
            self.plc.write_bit(byte, bit, False)
            return True
        except Exception:
            return False

    def _hold_with_diagnostic(self, byte: int, bit: int) -> None:
        start = self._clock()
        sampled = False
        while self._clock() - start < self.hold_seconds:
            elapsed = self._clock() - start
            if not sampled and elapsed >= min(0.3, self.hold_seconds / 3.0):
                sampled = True
                try:
                    observed = bool(self.plc.read_bit(byte, bit))
                    if not observed:
                        # Log-only diagnostic: a False sample here means the
                        # start-bit address convention is wrong.
                        print(f"LASER_BIT_DIAGNOSTIC M{byte}.{bit} hold read-back=LOW (t={elapsed:.2f}s)")
                except Exception:
                    pass
            time.sleep(min(0.05, max(0.0, self.hold_seconds)))

    def _cancel_clear_timer(self) -> None:
        with self._clear_lock:
            timer, self._clear_timer = self._clear_timer, None
        if timer is not None:
            timer.cancel()

    def _schedule_clear(self) -> None:
        if self.clear_after_seconds <= 0:
            return
        with self._clear_lock:
            self._cancel_clear_timer()
            timer = threading.Timer(self.clear_after_seconds, self._clear_channel)
            timer.daemon = True
            self._clear_timer = timer
            timer.start()

    def _clear_channel(self) -> None:
        with self._clear_lock:
            self._clear_timer = None
        try:
            self.writer.clear()
        except Exception:
            pass


class FakeMarker(MarkerPort):
    """Simulate-mode marker: records intents, no file/PLC side effects."""

    def __init__(self) -> None:
        self.intents: set[str] = set()

    def mark(self, record: TraceRecord) -> MarkReceipt:
        job_id = f"mark-{record.cycle_id}"
        self.intents.add(job_id)
        return MarkReceipt(True, job_id, f"receipt-{job_id}")
