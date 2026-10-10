import dataclasses
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.ateq import AteqRequest, FakeAteq
from app.config import Settings
from app.journal import CycleJournal
from app.laser import FakeMarker, LaserMarker, MarkReceipt
from app.models import (CycleSelection, Measurement, Phase, MarkState, Result,
                        StationId, TraceRecord)
from app.plc import FakePlc
from app.points import sim_point_map
from app.repository import FakeRepository
from app.station import StationController

from tests.helpers import make_security, make_selection


def run_dual_ok(controller):
    controller.start_cycle(make_selection(mode="dual"))
    controller.test_first()
    controller.test_second()


def run_dual(controller, first: Result, second: Result):
    controller.start_cycle(make_selection(mode="dual"))
    controller.ateq.result = first
    controller.test_first()
    controller.ateq.result = second
    controller.test_second()


def test_cycle_selection_date_scheme_normalized_and_rejected():
    default = CycleSelection(StationId.A, "P", "OP", "dual", "1")
    assert default.date_scheme == "YYYYMMDD"
    normalized = CycleSelection(StationId.A, "P", "OP", "dual", "1", " YYMMDD ")
    assert normalized.date_scheme == "YYMMDD"
    for bad in ("", "   ", None):
        with pytest.raises(ValueError):
            CycleSelection(StationId.A, "P", "OP", "dual", "1", bad)


def test_start_cycle_copies_date_scheme_and_journal_roundtrip(station_parts):
    controller, repository, marker, plc, journal_path = station_parts
    controller.start_cycle(CycleSelection(StationId.A, "P", "OP", "dual", "1", "YYMMDD"))
    assert controller.record.date_scheme == "YYMMDD"
    recovered = StationController(StationId.A, repository, marker, FakeAteq(),
                                  CycleJournal(journal_path), safe_stop=FakePlc(),
                                  security=make_security())
    assert recovered.phase is Phase.FAULT
    assert recovered.record.date_scheme == "YYMMDD"


def test_dual_both_ok_reaches_marking_and_marks(station_parts):
    controller, repository, marker, plc, _ = station_parts
    run_dual_ok(controller)
    assert controller.phase is Phase.MARKING
    assert controller.mark() is True
    assert controller.phase is Phase.COMPLETE
    assert controller.mark_state is MarkState.MARKED
    assert controller.record.marked is True
    assert controller.record.marked_at is not None
    assert marker.intents == {f"mark-{controller.record.cycle_id}"}


def test_dual_ok_then_ng_never_marks(station_parts):
    controller, repository, marker, plc, _ = station_parts
    run_dual(controller, Result.OK, Result.NG)
    assert controller.phase is Phase.COMPLETE
    assert controller.record.marked is False
    assert marker.intents == set()


def test_dual_first_ng_ends_cycle_immediately(station_parts):
    """第一腔 NG：仪器自身终止检测，不会有第二次测试结果。

    周期立即完成（记录落库、结果 NG、不打码），第二个测试不允许再发起；
    下一次 PLC/StepCode 边沿直接开新周期。
    """
    controller, repository, marker, plc, _ = station_parts
    controller.start_cycle(make_selection(mode="dual"))
    controller.ateq.result = Result.NG
    controller.test_first()
    assert controller.phase is Phase.COMPLETE
    assert controller.record.marked is False
    assert marker.intents == set()
    with pytest.raises(RuntimeError, match="当前状态"):
        controller.test_second()
    row = repository.get(controller.record.cycle_id)
    assert row is not None and row.first.result is Result.NG and row.second is None


def test_single_ok_reaches_marking_single_ng_completes(station_parts):
    controller, repository, marker, plc, _ = station_parts
    controller.start_cycle(make_selection(mode="single"))
    controller.test_first()
    assert controller.phase is Phase.MARKING
    controller.mark()
    controller.reset()
    controller.start_cycle(make_selection(mode="single"))
    controller.ateq.result = Result.NG
    controller.test_first()
    assert controller.phase is Phase.COMPLETE
    assert len(marker.intents) == 1  # 仅第一次 OK 被打码


def test_sample_cycle_skips_marking_by_default(station_parts):
    controller, repository, marker, plc, _ = station_parts
    controller.start_cycle(make_selection(mode="single"), sample=True)
    controller.test_first()
    assert controller.phase is Phase.COMPLETE
    assert controller.record.sample_cycle is True
    assert marker.intents == set()


def test_sample_cycle_marks_when_enabled(tmp_path):
    controller = StationController(
        StationId.A, FakeRepository(Settings()), FakeMarker(), FakeAteq(),
        CycleJournal(tmp_path / "j.json"),
        security=make_security(), mark_samples=True)
    controller.start_cycle(make_selection(mode="single"), sample=True)
    controller.test_first()
    assert controller.phase is Phase.MARKING


def test_sample_dual_first_ok_waits_for_positive(station_parts):
    """OK 样件与正常产品同一时序：负压 OK 后必须等正压，不能提前结束。"""
    controller, repository, marker, plc, _ = station_parts
    controller.start_cycle(make_selection(mode="dual"), sample=True)
    controller.ateq.result = Result.OK
    controller.test_first()
    assert controller.phase is Phase.WAIT_2
    controller.test_second()
    assert controller.phase is Phase.COMPLETE
    assert controller.record.second.result is Result.OK
    assert marker.intents == set()


def test_sample_dual_first_ng_ends_without_second(station_parts):
    controller, repository, marker, plc, _ = station_parts
    controller.start_cycle(make_selection(mode="dual"), sample=True)
    controller.ateq.result = Result.NG
    controller.test_first()
    assert controller.phase is Phase.COMPLETE
    with pytest.raises(RuntimeError, match="当前状态"):
        controller.test_second()
    assert marker.intents == set()


def test_sample_dual_second_fault_enters_fault(station_parts):
    controller, repository, marker, plc, _ = station_parts
    controller.start_cycle(make_selection(mode="dual"), sample=True)
    controller.test_first()
    controller.ateq.connected = False
    with pytest.raises(ConnectionError):
        controller.test_second()
    assert controller.phase is Phase.FAULT
    assert controller.recovery_required is True
    assert marker.intents == set()


def test_start_cycle_requires_idle_and_matching_station(station_parts):
    controller, *_ = station_parts
    controller.start_cycle(make_selection(mode="dual"))
    with pytest.raises(ValueError, match="不允许开始新周期"):
        controller.start_cycle(make_selection(mode="dual"))
    controller.reset()
    with pytest.raises(ValueError, match="工位不一致"):
        controller.start_cycle(make_selection(station=StationId.B))


def test_start_cycle_rejects_incomplete_selection(station_parts):
    controller, *_ = station_parts
    with pytest.raises(ValueError):
        controller.start_cycle(make_selection(part="  "))


def test_remark_requires_marked_complete_cycle(station_parts):
    controller, repository, marker, plc, _ = station_parts
    run_dual_ok(controller)
    controller.mark()
    assert controller.remark() is True
    assert controller.phase is Phase.COMPLETE
    assert len(marker.intents) == 1  # 幂等：同一周期同一个 job
    controller.reset()
    with pytest.raises(RuntimeError, match="重打码"):
        controller.remark()


def test_journal_recovery_requires_manual_resolution(station_parts, tmp_path):
    controller, repository, marker, plc, journal_path = station_parts
    controller.start_cycle(make_selection(mode="dual"))
    controller.test_first()
    # 模拟崩溃后重启：同一 journal 恢复 → FAULT + recovery_required。
    recovered = StationController(StationId.A, repository, marker, FakeAteq(),
                                  CycleJournal(journal_path), safe_stop=FakePlc(),
                                  security=make_security())
    assert recovered.phase is Phase.FAULT
    assert recovered.recovery_required is True
    recovered.resolve_recovery("测试归档", require_permission=False)
    assert recovered.phase is Phase.IDLE
    assert recovered.record is None


def test_complete_cycle_journal_restores_complete(station_parts, tmp_path):
    controller, repository, marker, plc, journal_path = station_parts
    run_dual_ok(controller)
    controller.mark()
    recovered = StationController(StationId.A, repository, marker, FakeAteq(),
                                  CycleJournal(journal_path), safe_stop=FakePlc(),
                                  security=make_security())
    assert recovered.phase is Phase.COMPLETE
    assert recovered.recovery_required is False
    assert recovered.record.marked is True


class _MarkerSpy:
    def __init__(self, inner=None):
        self.inner = inner
        self.calls = []

    def mark(self, record):
        self.calls.append(record)
        if self.inner is None:
            return MarkReceipt(True, f"mark-{record.cycle_id}", "receipt")
        return self.inner.mark(record)


class _WriterSpy:
    def __init__(self):
        self.published = []
        self.cleared = []

    def publish(self, text):
        self.published.append(text)

    def clear(self):
        self.cleared.append(True)


class _PlcCallSpy(FakePlc):
    def __init__(self):
        super().__init__()
        self.reads = []
        self.writes = []

    def read_bit(self, byte, bit):
        self.reads.append((byte, bit))
        return super().read_bit(byte, bit)

    def write_bit(self, byte, bit, value):
        self.writes.append((byte, bit, value))
        return super().write_bit(byte, bit, value)


class _SafeStopSpy:
    def __init__(self):
        self.stops = []
        self.checks = 0
        self.energized = True

    def safe_stop(self, reason):
        self.stops.append(reason)
        self.energized = False

    def outputs_energized(self):
        self.checks += 1
        return self.energized


class _DivergentRepository:
    def __init__(self, inner):
        self.inner = inner
        self.committed = {}
        self.get_calls = 0

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def get(self, cycle_id):
        self.get_calls += 1
        raise AssertionError("get() must not be used by mark()")

    def get_committed(self, cycle_id):
        record = self.committed.get(cycle_id)
        return deepcopy(record) if record is not None else None


class _FailReadRepository:
    def __init__(self, inner, mode):
        self.inner = inner
        self.mode = mode

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def get_committed(self, cycle_id):
        if self.mode == "raise":
            raise RuntimeError("connection lost")
        return None


def _valid_committed(controller) -> TraceRecord:
    record = deepcopy(controller.record)
    record.station = controller.station
    record.cycle_id = controller.record.cycle_id
    record.part_no = "DB-PART"
    record.person = "DB-OP"
    record.created_at = datetime(2026, 10, 1, 12, 0, 0, tzinfo=timezone.utc)
    record.first = Measurement(111.125, 1.125, Result.OK, b"DB1", "kPa", "ml/min")
    record.second = Measurement(222.25, 2.25, Result.OK, b"DB2", "kPa", "ml/min")
    record.date_scheme = "DB-SCHEME"
    record.test_mode = controller.record.test_mode
    return record


_INVALID_READBACK_CASES = (
    "none", "wrong_type", "station_b", "cycle_mismatch", "cycle_empty",
    "created_none", "created_str", "part_blank", "part_none",
    "person_blank", "person_none", "first_none", "first_wrong_type",
    "pressure_nan", "pressure_inf", "pressure_neg_inf", "pressure_str",
    "pressure_bool", "leakage_str", "unit_none", "unit_int",
    "result_unknown", "result_ng", "result_str", "dual_second_none",
    "second_nan", "second_ng", "scheme_blank",
)


def _malformed_committed(controller, case):
    if case == "none":
        return None
    if case == "wrong_type":
        return object()
    record = _valid_committed(controller)
    if case == "station_b":
        record.station = StationId.B
    elif case == "cycle_mismatch":
        record.cycle_id = "OTHER"
    elif case == "cycle_empty":
        record.cycle_id = ""
    elif case == "created_none":
        record.created_at = None
    elif case == "created_str":
        record.created_at = "2026-10-01"
    elif case == "part_blank":
        record.part_no = "  "
    elif case == "part_none":
        record.part_no = None
    elif case == "person_blank":
        record.person = "  "
    elif case == "person_none":
        record.person = None
    elif case == "first_none":
        record.first = None
    elif case == "first_wrong_type":
        record.first = object()
    elif case == "pressure_nan":
        record.first = Measurement(float("nan"), 1.0, Result.OK, b"DB", "kPa", "ml/min")
    elif case == "pressure_inf":
        record.first = Measurement(float("inf"), 1.0, Result.OK, b"DB", "kPa", "ml/min")
    elif case == "pressure_neg_inf":
        record.first = Measurement(float("-inf"), 1.0, Result.OK, b"DB", "kPa", "ml/min")
    elif case == "pressure_str":
        record.first = Measurement("1.0", 1.0, Result.OK, b"DB", "kPa", "ml/min")
    elif case == "pressure_bool":
        record.first = Measurement(True, 1.0, Result.OK, b"DB", "kPa", "ml/min")
    elif case == "leakage_str":
        record.first = Measurement(1.0, "1.0", Result.OK, b"DB", "kPa", "ml/min")
    elif case == "unit_none":
        record.first = Measurement(1.0, 1.0, Result.OK, b"DB", None, "ml/min")
    elif case == "unit_int":
        record.first = Measurement(1.0, 1.0, Result.OK, b"DB", "kPa", 1)
    elif case == "result_unknown":
        record.first = Measurement(1.0, 1.0, Result.UNKNOWN, b"DB", "kPa", "ml/min")
    elif case == "result_ng":
        record.first = Measurement(1.0, 1.0, Result.NG, b"DB", "kPa", "ml/min")
    elif case == "result_str":
        record.first = Measurement(1.0, 1.0, "OK", b"DB", "kPa", "ml/min")
    elif case == "dual_second_none":
        record.second = None
    elif case == "second_nan":
        record.second = Measurement(float("nan"), 2.0, Result.OK, b"DB", "kPa", "ml/min")
    elif case == "second_ng":
        record.second = Measurement(2.0, 2.0, Result.NG, b"DB", "kPa", "ml/min")
    elif case == "scheme_blank":
        controller.record.date_scheme = "  "
    else:
        raise AssertionError(case)
    return record


@pytest.mark.parametrize("case", _INVALID_READBACK_CASES)
def test_mark_rejects_malformed_committed_readback(station_parts, case):
    controller, repository, marker, plc, _ = station_parts
    run_dual_ok(controller)
    spy = _MarkerSpy()
    controller.marker = spy
    divergent = _DivergentRepository(repository)
    divergent.committed[controller.record.cycle_id] = _malformed_committed(controller, case)
    controller.repository = divergent
    assert controller.mark() is False
    assert spy.calls == []
    assert controller.phase is Phase.FAULT
    assert controller.recovery_required is True


def test_mark_uses_committed_row_not_process_cache(station_parts):
    controller, repository, marker, plc, _ = station_parts
    run_dual_ok(controller)
    committed = _valid_committed(controller)
    divergent = _DivergentRepository(repository)
    divergent.committed[controller.record.cycle_id] = committed
    controller.repository = divergent
    spy = _MarkerSpy()
    controller.marker = spy
    assert controller.mark() is True
    assert divergent.get_calls == 0
    assert len(spy.calls) == 1
    captured, template = spy.calls[0], deepcopy(committed)
    for field in dataclasses.fields(TraceRecord):
        if field.name == "date_scheme":
            continue
        assert getattr(captured, field.name) == getattr(template, field.name), field.name
    assert captured.date_scheme == controller.record.date_scheme == "YYYYMMDD"
    for phase in ("first", "second"):
        captured_m, template_m = getattr(captured, phase), getattr(template, phase)
        for field in dataclasses.fields(Measurement):
            assert getattr(captured_m, field.name) == getattr(template_m, field.name), (phase, field.name)
    assert captured.part_no == "DB-PART" and captured.person == "DB-OP"


def test_mark_rejects_db_ng_when_memory_ok(station_parts):
    controller, repository, marker, plc, _ = station_parts
    run_dual_ok(controller)
    committed = _valid_committed(controller)
    committed.first = Measurement(111.0, 1.0, Result.NG, b"DB", "kPa", "ml/min")
    divergent = _DivergentRepository(repository)
    divergent.committed[controller.record.cycle_id] = committed
    controller.repository = divergent
    spy = _MarkerSpy()
    controller.marker = spy
    assert controller.mark() is False
    assert spy.calls == []
    assert controller.phase is Phase.FAULT
    assert controller.recovery_required is True


def test_mysql_shaped_single_readback_rejected_at_gate(station_parts):
    controller, repository, marker, plc, _ = station_parts
    controller.start_cycle(make_selection(mode="single"))
    controller.test_first()
    assert controller.phase is Phase.MARKING
    committed = _valid_committed(controller)
    committed.test_mode = "dual"   # MySQL _row_to_record default
    committed.second = None        # single-mode row has no second measurement
    divergent = _DivergentRepository(repository)
    divergent.committed[controller.record.cycle_id] = committed
    controller.repository = divergent
    spy = _MarkerSpy()
    controller.marker = spy
    assert controller.mark() is False
    assert spy.calls == []
    assert controller.phase is Phase.FAULT


def _purpose_built_marker_controller(tmp_path):
    settings = Settings()
    repository = FakeRepository(settings)
    writer_spy = _WriterSpy()
    marker_plc_spy = _PlcCallSpy()
    real_marker = LaserMarker(writer_spy, marker_plc_spy, sim_point_map(),
                              hold_seconds=0.05, settle_seconds=0.01)
    marker_spy = _MarkerSpy(real_marker)
    safe_stop_spy = _SafeStopSpy()
    controller = StationController(StationId.A, repository, marker_spy, FakeAteq(),
                                   CycleJournal(tmp_path / "mark-fail.json"),
                                   safe_stop=safe_stop_spy, security=make_security())
    run_dual_ok(controller)
    return controller, repository, writer_spy, marker_plc_spy, marker_spy, safe_stop_spy


@pytest.mark.parametrize("mode", ["raise", "none"])
def test_mark_readback_failure_fails_closed_without_marker_io(tmp_path, mode):
    (controller, repository, writer_spy, marker_plc_spy, marker_spy,
     safe_stop_spy) = _purpose_built_marker_controller(tmp_path)
    controller.repository = _FailReadRepository(repository, mode)
    marker_spy.calls.clear()
    writer_spy.published.clear()
    writer_spy.cleared.clear()
    marker_plc_spy.reads.clear()
    marker_plc_spy.writes.clear()
    safe_stop_spy.stops.clear()
    safe_stop_spy.checks = 0
    safe_stop_spy.energized = True
    assert controller.mark() is False
    assert controller.phase is Phase.FAULT
    assert controller.recovery_required is True
    assert controller.mark_state is MarkState.AMBIGUOUS
    assert controller.error.startswith("打码失败")
    assert marker_spy.calls == []
    assert writer_spy.published == [] and writer_spy.cleared == []
    assert marker_plc_spy.writes == [] and marker_plc_spy.reads == []
    assert len(safe_stop_spy.stops) == 1
    assert safe_stop_spy.stops[0].startswith("打码失败")
    assert safe_stop_spy.checks >= 1
    assert safe_stop_spy.energized is False
    assert "停止状态未确认" not in controller.error


def test_ateq_identity_mismatch_faults(station_parts):
    controller, repository, marker, plc, _ = station_parts
    controller.start_cycle(make_selection(mode="single"))

    class ForgedAteq(FakeAteq):
        def run(self, request):
            response = super().run(request)
            forged = AteqRequest(request.station, "OTHER-CYCLE", request.program,
                                 request.sequence, request.timestamp)
            object.__setattr__(response, "request", forged)
            return response

    controller.ateq = ForgedAteq(result=Result.OK)
    controller.ateq.station = "A"
    with pytest.raises(RuntimeError, match="身份不匹配"):
        controller.test_first()
    assert controller.phase is Phase.FAULT


def test_mark_failure_faults_and_marks_ambiguous(station_parts):
    controller, repository, marker, plc, _ = station_parts
    run_dual_ok(controller)

    class RejectingMarker:
        def mark(self, record):
            return MarkReceipt(False, "mark-x", "激光启动失败: 模拟故障")

    controller.marker = RejectingMarker()
    assert controller.mark() is False
    assert controller.phase is Phase.FAULT
    assert controller.mark_state is MarkState.AMBIGUOUS
    assert controller.recovery_required is True
