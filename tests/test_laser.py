import time
from datetime import datetime, timezone

import pytest

from app.ateq import FakeAteq
from app.laser import (FakeMarker, LaserChannel, LaserFileError, LaserFileWriter,
                       LaserMarker, MarkReceipt, build_mark_text)
from app.models import Measurement, Result, StationId, TraceRecord
from app.points import sim_point_map
from app.plc import FakePlc


def make_record(first_ok=True, second_ok=True, mode="dual",
                part="E118015100", person="张三") -> TraceRecord:
    record = TraceRecord(
        station=StationId.A, part_no=part, person=person,
        cycle_id="A-20260919120000-ab12cd",
        created_at=datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc),
        test_mode=mode, date_scheme="YYYYMMDD",
    )
    record.first = Measurement(500.123456, 0.4567, Result.OK if first_ok else Result.NG,
                               b"FRAME1", "Kpa", "ml/min")
    if mode == "dual":
        record.second = Measurement(-45.6789, 1.234, Result.OK if second_ok else Result.NG,
                                    b"FRAME2", "Kpa", "ml/min")
    return record


def make_writer(tmp_path, name="激光码信息.txt", encoding="gbk"):
    return LaserFileWriter(LaserChannel(tmp_path / name, encoding, "\r\n"))


def test_build_mark_text_fields(tmp_path):
    text = build_mark_text(make_record())
    lines = text.split("\n")
    assert lines == ["20260919", "E118015100", "500.123Kpa", "0.457ml/min",
                     "-45.679Kpa", "1.234ml/min", "OK", "张三"]


def test_build_mark_text_dual_requires_second():
    record = make_record(mode="single")
    record.second = None
    build_mark_text(record)  # single 无第二次测量：允许
    dual = make_record()
    dual.second = None
    with pytest.raises(ValueError, match="第二次测量"):
        build_mark_text(dual)


def test_build_mark_text_incomplete_fields():
    record = make_record(person="  ")
    with pytest.raises(ValueError, match="打码字段不完整"):
        build_mark_text(record)


def test_writer_publish_verify_clear(tmp_path):
    writer = make_writer(tmp_path)
    writer.publish("A\r\nB\r\n")
    assert writer.verify("A\r\nB")
    raw = (tmp_path / "激光码信息.txt").read_bytes()
    assert raw == "A\r\nB\r\n".encode("gbk")
    writer.clear()
    assert (tmp_path / "激光码信息.txt").read_bytes() == b""
    assert not writer.verify("A\r\nB")


def test_writer_encoding(tmp_path):
    writer = make_writer(tmp_path, encoding="utf-8")
    writer.publish("张三")
    assert (tmp_path / "激光码信息.txt").read_bytes() == "张三\r\n".encode("utf-8")


def test_marker_happy_path_pulses_and_clears(tmp_path):
    writer = make_writer(tmp_path)
    plc = FakePlc()
    marker = LaserMarker(writer, plc, sim_point_map(),
                         hold_seconds=0.05, settle_seconds=0.01,
                         clear_after_seconds=0.05)
    receipt = marker.mark(make_record())
    assert isinstance(receipt, MarkReceipt) and receipt.accepted
    assert receipt.job_id == "mark-A-20260919120000-ab12cd"
    # 文件内容 = 打码文本；启动位脉冲结束必须回到低电平。
    assert (tmp_path / "激光码信息.txt").read_bytes() == "20260919\r\nE118015100\r\n500.123Kpa\r\n0.457ml/min\r\n-45.679Kpa\r\n1.234ml/min\r\nOK\r\n张三\r\n".encode("gbk")
    assert plc.read_bit(20, 0) is False
    # 10 秒自毁：clear_after 到期后文件被清空。
    time.sleep(0.3)
    assert (tmp_path / "激光码信息.txt").read_bytes() == b""


def test_marker_file_failure_never_touches_plc(tmp_path):
    writer = make_writer(tmp_path)
    plc = FakePlc()

    class ExplodingWriter:
        def publish(self, text):
            raise LaserFileError("disk full")

        def clear(self):
            pass

    marker = LaserMarker(ExplodingWriter(), plc, sim_point_map())
    receipt = marker.mark(make_record())
    assert not receipt.accepted
    assert "打码文件写入失败" in receipt.receipt
    # fail-closed：PLC 上没有任何写入。
    assert not plc.outputs_energized()


def test_marker_rejected_when_start_bit_readback_low(tmp_path):
    writer = make_writer(tmp_path)
    plc = FakePlc()
    # 启动位置位后立即被外部清零：回读为低 → 拒绝并回滚。
    original_write = plc.write_bit

    def sabotage(byte, bit, value):
        original_write(byte, bit, value)
        if value and (byte, bit) == (20, 0):
            original_write(byte, bit, False)

    plc.write_bit = sabotage
    marker = LaserMarker(writer, plc, sim_point_map(), hold_seconds=0.05)
    receipt = marker.mark(make_record())
    assert not receipt.accepted
    assert "回读为低" in receipt.receipt


def test_marker_done_bit_timeout(tmp_path):
    writer = make_writer(tmp_path)
    plc = FakePlc()
    marker = LaserMarker(writer, plc, sim_point_map(), hold_seconds=0.05,
                         settle_seconds=0.01, wait_done=True, done_timeout_s=0.1)
    receipt = marker.mark(make_record())
    assert not receipt.accepted
    assert "完成位" in receipt.receipt


def test_marker_done_bit_satisfied(tmp_path):
    writer = make_writer(tmp_path)
    plc = FakePlc()

    def auto_done(byte, bit, value):
        FakePlc.write_bit(plc, byte, bit, value)
        if value and (byte, bit) == (20, 0):
            FakePlc.write_bit(plc, 20, 1, True)

    plc.write_bit = auto_done
    marker = LaserMarker(writer, plc, sim_point_map(), hold_seconds=0.05,
                         settle_seconds=0.01, wait_done=True, done_timeout_s=1.0)
    receipt = marker.mark(make_record())
    assert receipt.accepted


def test_marker_new_mark_cancels_pending_clear(tmp_path):
    writer = make_writer(tmp_path)
    plc = FakePlc()
    marker = LaserMarker(writer, plc, sim_point_map(),
                         hold_seconds=0.05, settle_seconds=0.01,
                         clear_after_seconds=0.2)
    assert marker.mark(make_record()).accepted
    time.sleep(0.05)
    # 新打码取消未决清空 → 文件在 0.2s 后仍有内容。
    assert marker.mark(make_record(part="E99999999")).accepted
    assert (tmp_path / "激光码信息.txt").read_bytes() != b""


def test_fake_marker_idempotent_intent():
    marker = FakeMarker()
    record = make_record(mode="single")
    assert marker.mark(record).accepted
    assert marker.mark(record).accepted
    assert marker.intents == {"mark-" + record.cycle_id}


# --------------------------------------------------------------- TC45-TC49

class SpyPlc:
    """Records every PLC read/write attempt on top of FakePlc state."""

    def __init__(self):
        self.inner = FakePlc()
        self.writes = []
        self.reads = []

    def read_bit(self, byte, bit):
        self.reads.append((byte, bit))
        return self.inner.read_bit(byte, bit)

    def write_bit(self, byte, bit, value):
        self.writes.append((byte, bit, value))
        self.inner.write_bit(byte, bit, value)

    def health(self):
        return True

    def outputs_energized(self):
        return self.inner.outputs_energized()

    def safe_stop(self, reason):
        self.inner.safe_stop(reason)


class ScriptedPlc(SpyPlc):
    """Raises at a configured 1-based operation index; can force read values."""

    def __init__(self, fail_at=(), forced_reads=None):
        super().__init__()
        self.ops = []
        self.fail_at = set(fail_at)
        self.forced_reads = dict(forced_reads or {})
        self._read_index = 0

    def _record(self, op):
        self.ops.append(op)
        if len(self.ops) in self.fail_at:
            raise RuntimeError(f"scripted PLC failure at op {len(self.ops)}")

    def write_bit(self, byte, bit, value):
        self._record(("write", value))
        self.inner.write_bit(byte, bit, value)

    def read_bit(self, byte, bit):
        self._read_index += 1
        self._record(("read",))
        if self._read_index in self.forced_reads:
            return self.forced_reads[self._read_index]
        return self.inner.read_bit(byte, bit)


def test_tc45_file_failure_never_touches_plc(tmp_path):
    class ExplodingWriter:
        def publish(self, text):
            raise LaserFileError("disk full")

        def clear(self):
            pass

        def verify(self, text):
            return False

    plc = SpyPlc()
    marker = LaserMarker(ExplodingWriter(), plc, sim_point_map(), clear_after_seconds=0)
    receipt = marker.mark(make_record())
    assert not receipt.accepted
    assert "打码文件写入失败" in receipt.receipt
    assert plc.writes == []
    assert plc.reads == []


def test_tc46_single_true_pulse_then_clean_false(tmp_path):
    writer = make_writer(tmp_path)
    plc = SpyPlc()
    marker = LaserMarker(writer, plc, sim_point_map(), hold_seconds=0.0,
                         settle_seconds=0.0, clear_after_seconds=0)
    receipt = marker.mark(make_record())
    assert receipt.accepted
    assert plc.writes == [(20, 0, True), (20, 0, False)]
    assert sum(1 for _, _, value in plc.writes if value) == 1


TC47_SCENARIOS = [
    ("fail-write-true", {"fail_at": {1}}),
    ("fail-read-high", {"fail_at": {2}}),
    ("readback-low", {"forced_reads": {1: False}}),
    ("fail-reset-write", {"fail_at": {3}}),
    ("fail-reset-read", {"fail_at": {4}}),
    ("reset-readback-high", {"forced_reads": {2: True}}),
]


@pytest.mark.parametrize("label,rules", TC47_SCENARIOS,
                         ids=[f"TC47-{item[0]}" for item in TC47_SCENARIOS])
def test_tc47_transaction_failures_cleanup_without_repulse(tmp_path, label, rules):
    writer = make_writer(tmp_path)
    plc = ScriptedPlc(**rules)
    marker = LaserMarker(writer, plc, sim_point_map(), hold_seconds=0.0,
                         settle_seconds=0.0, clear_after_seconds=0)
    receipt = marker.mark(make_record())
    assert not receipt.accepted
    assert "激光启动失败" in receipt.receipt
    assert sum(1 for op in plc.ops if op == ("write", True)) == 1
    assert plc.ops[-1] == ("write", False)  # unified cleanup attempt


def test_tc48_failed_cleanup_reports_deenergize_unconfirmed(tmp_path):
    writer = make_writer(tmp_path)
    plc = ScriptedPlc(fail_at={3, 4})  # reset write fails, cleanup write fails
    marker = LaserMarker(writer, plc, sim_point_map(), hold_seconds=0.0,
                         settle_seconds=0.0, clear_after_seconds=0)
    receipt = marker.mark(make_record())
    assert not receipt.accepted
    assert "断电未确认" in receipt.receipt
    assert sum(1 for op in plc.ops if op == ("write", True)) == 1


class FakeMonotonicClock:
    """P09: unbounded, strictly increasing fake clock; every call advances.

    A finite iterator is forbidden here: the hold diagnostic also reads the
    clock, so exhaustion would surface as a generic empty failure instead of
    the real completion-bit timeout.
    """

    def __init__(self, start: float = 0.0, increment: float = 0.5) -> None:
        self._now = start
        self._increment = increment

    def __call__(self) -> float:
        self._now += self._increment
        return self._now


def test_tc49_done_timeout_rejected_with_cleanup_no_repulse(tmp_path):
    writer = make_writer(tmp_path)
    plc = SpyPlc()
    clock = FakeMonotonicClock(start=0.0, increment=0.5)  # P09 unbounded clock
    marker = LaserMarker(writer, plc, sim_point_map(), hold_seconds=0.0,
                         settle_seconds=0.0, clear_after_seconds=0, wait_done=True,
                         done_timeout_s=0.1, clock=clock)
    receipt = marker.mark(make_record())
    assert receipt.accepted is False
    # P09: the real deadline was reached and reported as a completion-bit
    # timeout, not as an empty generic laser failure.
    assert "激光启动失败" in receipt.receipt
    assert "完成位" in receipt.receipt and "未置位" in receipt.receipt
    assert "断电未确认" not in receipt.receipt  # cleanup False succeeded
    # P09/TC49: exact write sequence -- one start, normal reset, one cleanup.
    assert plc.writes == [(20, 0, True), (20, 0, False), (20, 0, False)]
    assert sum(1 for _, _, value in plc.writes if value) == 1
    assert sum(1 for _, _, value in plc.writes if not value) == 2
    # The done point was polled while low and left de-energized; no re-pulse.
    assert (20, 1) in plc.reads
    assert plc.inner.read_bit(20, 1) is False
    assert plc.inner.outputs_energized() is False
