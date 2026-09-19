import pytest

from app.ateq import AteqRequest, FakeAteq
from app.config import Settings
from app.journal import CycleJournal
from app.laser import FakeMarker, MarkReceipt
from app.models import Phase, MarkState, Result, StationId
from app.plc import FakePlc
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


def test_dual_ng_then_ok_never_marks(station_parts):
    """用户规则：正压、负压都合格才打码；第一次 NG 即使第二次 OK 也不打码。"""
    controller, repository, marker, plc, _ = station_parts
    run_dual(controller, Result.NG, Result.OK)
    assert controller.phase is Phase.COMPLETE
    assert controller.record.marked is False
    assert marker.intents == set()


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
