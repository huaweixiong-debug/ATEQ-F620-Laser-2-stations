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


class _SpyWriter:
    def __init__(self):
        self.published: list[str] = []
        self.cleared: list[bool] = []

    def publish(self, text):
        self.published.append(text)

    def clear(self):
        self.cleared.append(True)


class _SpyPlc(FakePlc):
    def __init__(self):
        super().__init__()
        self.reads: list[tuple[int, int]] = []
        self.writes: list[tuple[int, int, bool]] = []

    def read_bit(self, byte, bit):
        self.reads.append((byte, bit))
        return super().read_bit(byte, bit)

    def write_bit(self, byte, bit, value):
        self.writes.append((byte, bit, value))
        return super().write_bit(byte, bit, value)


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


@pytest.mark.parametrize("scheme", ["", "BOGUS", "年+月"])
def test_marker_invalid_date_scheme_never_publishes_or_touches_plc(scheme):
    from app.date_codes import DateCodeCatalog

    record = make_record()
    record.date_scheme = scheme
    writer = _SpyWriter()
    plc = _SpyPlc()
    marker = LaserMarker(writer, plc, sim_point_map(), hold_seconds=0.05,
                         settle_seconds=0.01,
                         date_code_fn=DateCodeCatalog({}).date_code)
    receipt = marker.mark(record)
    assert not receipt.accepted
    assert writer.published == []
    assert writer.cleared == []
    assert plc.writes == []
    assert plc.reads == []


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


class _ReadFailingPlc(FakePlc):
    """Start-bit set succeeds; the readback immediately after it raises."""

    def __init__(self):
        super().__init__()
        self.fail_reads = False

    def write_bit(self, byte, bit, value):
        super().write_bit(byte, bit, value)
        if value and (byte, bit) == (20, 0):
            self.fail_reads = True

    def read_bit(self, byte, bit):
        if self.fail_reads and (byte, bit) == (20, 0):
            raise ConnectionError("bus fault")
        return super().read_bit(byte, bit)


def test_marker_read_after_set_raises_deenergizes(tmp_path):
    writer = make_writer(tmp_path)
    plc = _ReadFailingPlc()
    marker = LaserMarker(writer, plc, sim_point_map(),
                         hold_seconds=0.05, settle_seconds=0.01)
    receipt = marker.mark(make_record())
    assert not receipt.accepted
    assert "激光启动失败" in receipt.receipt
    assert FakePlc.read_bit(plc, 20, 0) is False


class _AmbiguousStartPlc(FakePlc):
    """Start-bit write sets the bit but then raises (acknowledgement lost)."""

    def write_bit(self, byte, bit, value):
        super().write_bit(byte, bit, value)
        if value and (byte, bit) == (20, 0):
            raise ConnectionError("lost ack")


def test_marker_ambiguous_start_write_deenergizes_and_schedules_clear(tmp_path):
    writer = make_writer(tmp_path)
    plc = _AmbiguousStartPlc()
    marker = LaserMarker(writer, plc, sim_point_map(), hold_seconds=0.05,
                         settle_seconds=0.01, clear_after_seconds=0.05)
    receipt = marker.mark(make_record())
    assert not receipt.accepted
    assert FakePlc.read_bit(plc, 20, 0) is False
    assert (tmp_path / "激光码信息.txt").read_bytes() != b""
    time.sleep(0.3)
    assert (tmp_path / "激光码信息.txt").read_bytes() == b""


class _ResetFailingPlc(FakePlc):
    """Start-bit reset (False) always fails; the bit stays energized."""

    def write_bit(self, byte, bit, value):
        if not value and (byte, bit) == (20, 0):
            raise ConnectionError("reset circuit fault")
        super().write_bit(byte, bit, value)


def test_marker_reset_write_failure_reports_unconfirmed_and_schedules_clear(tmp_path):
    writer = make_writer(tmp_path)
    plc = _ResetFailingPlc()
    marker = LaserMarker(writer, plc, sim_point_map(), hold_seconds=0.05,
                         settle_seconds=0.01, clear_after_seconds=0.05)
    receipt = marker.mark(make_record())
    assert not receipt.accepted
    assert "断电未确认" in receipt.receipt
    assert (tmp_path / "激光码信息.txt").read_bytes() != b""
    time.sleep(0.3)
    assert (tmp_path / "激光码信息.txt").read_bytes() == b""


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
