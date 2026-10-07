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


# --------------------------------------------------------------- TC12-TC25

class SpyAteq(FakeAteq):
    def __init__(self, result=Result.OK):
        super().__init__(result=result)
        self.calls = []

    def start_test(self):
        self.calls.append("start")
        return super().start_test()

    def run(self, request):
        self.calls.append("run")
        return super().run(request)


def _reforge_request(response, **changes):
    base = response.request
    payload = {"station": base.station, "cycle_id": base.cycle_id, "program": base.program,
               "sequence": base.sequence, "timestamp": base.timestamp}
    payload.update(changes)
    object.__setattr__(response, "request", AteqRequest(**payload))


FORGED_MUTATIONS = [
    ("station", lambda r: _reforge_request(r, station="B")),
    ("cycle", lambda r: _reforge_request(r, cycle_id="OTHER-CYCLE")),
    ("program", lambda r: _reforge_request(r, program="9")),
    ("sequence", lambda r: _reforge_request(r, sequence=r.request.sequence + 1)),
    ("timestamp", lambda r: _reforge_request(r, timestamp="2000-01-01T00:00:00+00:00")),
    ("empty-frame", lambda r: object.__setattr__(r, "raw_frame", b"")),
    ("empty-measurement-frame",
     lambda r: object.__setattr__(r.measurement, "raw_frame", b"")),
    ("short-frame",
     lambda r: (object.__setattr__(r, "raw_frame", b"AB"),
                object.__setattr__(r.measurement, "raw_frame", b"AB"))),
    ("cycle-id-as-frame",
     lambda r: (object.__setattr__(r, "raw_frame", r.request.cycle_id.encode()),
                object.__setattr__(r.measurement, "raw_frame", r.request.cycle_id.encode()))),
    ("mismatched-frames",
     lambda r: object.__setattr__(r, "raw_frame", b"SIMFRAME:other")),
]


class ForgedAteq(FakeAteq):
    def __init__(self, mutate):
        super().__init__(result=Result.OK, station="A")
        self.mutate = mutate

    def run(self, request):
        response = super().run(request)
        self.mutate(response)
        return response


class FlakyRepository(FakeRepository):
    def __init__(self, settings, fail_insert=False, fail_update=False):
        super().__init__(settings)
        self.fail_insert = fail_insert
        self.fail_update = fail_update

    def insert_stage1(self, record, capability=None):
        if self.fail_insert:
            raise RuntimeError("db insert failed")
        return super().insert_stage1(record, capability)

    def update_stage2(self, cycle_id, measurement, capability=None):
        if self.fail_update:
            raise RuntimeError("db update failed")
        return super().update_stage2(cycle_id, measurement, capability)


def _fresh_controller(repository, marker, ateq, tmp_path, safe_stop=None):
    return StationController(StationId.A, repository, marker, ateq,
                             CycleJournal(tmp_path / "journal.json"),
                             safe_stop=safe_stop, security=make_security())


def test_tc12_station_mismatch_rejected_without_side_effects(station_parts):
    controller, repository, marker, plc, _ = station_parts
    with pytest.raises(ValueError, match="工位不一致"):
        controller.start_cycle(make_selection(station=StationId.B))
    assert controller.phase is Phase.IDLE
    assert controller.record is None
    assert controller.ateq.requests == []
    assert repository.records == {}
    assert marker.intents == set()


def test_tc13_freeze_strips_date_scheme_and_keeps_station_prefix(station_parts):
    controller, repository, marker, plc, _ = station_parts
    from app.models import CycleSelection
    selection = CycleSelection(StationId.A, "PART-9", "OP-9", "single", "7",
                               date_scheme="  YYMMDD ")
    controller.start_cycle(selection, sample=False)
    record = controller.record
    assert record.date_scheme == "YYMMDD"
    assert record.part_no == "PART-9"
    assert record.person == "OP-9"
    assert record.ateq_program == "7"
    assert record.cycle_id.startswith("A-")
    assert record.sample_cycle is False


def test_tc17_dual_first_ng_ends_without_second_test(station_parts):
    controller, repository, marker, plc, _ = station_parts
    controller.start_cycle(make_selection(mode="dual"))
    controller.ateq.result = Result.NG
    controller.test_first()
    assert controller.phase is Phase.COMPLETE
    assert controller.record.first is not None
    assert controller.record.first.result is Result.NG
    assert controller.record.second is None
    assert len(controller.ateq.requests) == 1
    assert marker.intents == set()


def test_tc18_dual_sample_without_marking_completes_after_first(station_parts):
    controller, repository, marker, plc, _ = station_parts
    controller.start_cycle(make_selection(mode="dual"), sample=True)
    controller.test_first()
    assert controller.phase is Phase.COMPLETE
    assert controller.record.sample_cycle is True
    assert controller.record.second is None
    assert len(controller.ateq.requests) == 1
    assert marker.intents == set()


def test_tc19_production_dual_both_ok_waits_then_marks(station_parts):
    controller, repository, marker, plc, _ = station_parts
    controller.start_cycle(make_selection(mode="dual"))
    controller.test_first()
    assert controller.phase is Phase.WAIT_2
    controller.test_second()
    assert controller.phase is Phase.MARKING
    assert controller.mark() is True
    assert controller.phase is Phase.COMPLETE
    assert len(marker.intents) == 1
    assert len(controller.ateq.requests) == 2


def test_tc20_first_ok_second_ng_completes_without_marking(station_parts):
    controller, repository, marker, plc, _ = station_parts
    run_dual(controller, Result.OK, Result.NG)
    assert controller.phase is Phase.COMPLETE
    assert controller.record.second.result is Result.NG
    assert len(controller.ateq.requests) == 2
    assert marker.intents == set()


def test_tc21_external_start_adapter_never_issues_start_test(tmp_path):
    spy = SpyAteq()
    spy.external_start = True
    controller = _fresh_controller(FakeRepository(Settings()), FakeMarker(), spy, tmp_path)
    controller.start_cycle(make_selection(mode="single"))
    controller.test_first()
    assert spy.calls == ["run"]
    assert spy.start_count == 0
    assert controller.phase is Phase.MARKING


def test_tc22_simulation_adapter_starts_before_run(tmp_path):
    spy = SpyAteq()
    controller = _fresh_controller(FakeRepository(Settings()), FakeMarker(), spy, tmp_path)
    controller.start_cycle(make_selection(mode="single"))
    controller.test_first()
    assert spy.calls == ["start", "run"]
    assert spy.start_count == 1
    assert controller.phase is Phase.MARKING


@pytest.mark.parametrize("label,mutate", FORGED_MUTATIONS,
                         ids=[f"TC23-{item[0]}" for item in FORGED_MUTATIONS])
def test_tc23_forged_response_faults_before_db_or_marker(tmp_path, label, mutate):
    repository, marker = FakeRepository(Settings()), FakeMarker()
    controller = _fresh_controller(repository, marker, ForgedAteq(mutate), tmp_path,
                                   safe_stop=FakePlc())
    controller.start_cycle(make_selection(mode="single"))
    with pytest.raises(RuntimeError, match="身份不匹配"):
        controller.test_first()
    assert controller.phase is Phase.FAULT
    assert controller.recovery_required is True
    assert controller.record.first is None
    assert controller.ateq_results == []
    assert repository.records == {}
    assert marker.intents == set()


def test_tc24_ateq_timeout_faults_without_retry(station_parts):
    controller, repository, marker, plc, _ = station_parts

    class TimeoutAteq(FakeAteq):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        def run(self, request):
            self.attempts += 1
            raise TimeoutError("ateq timeout")

    timeout = TimeoutAteq()
    controller.ateq = timeout
    controller.start_cycle(make_selection(mode="single"))
    with pytest.raises(TimeoutError):
        controller.test_first()
    assert timeout.attempts == 1
    assert controller.phase is Phase.FAULT
    assert controller.recovery_required is True
    assert plc.last_safe_stop
    assert repository.records == {}
    assert marker.intents == set()


def test_tc25_db_insert_failure_faults_without_retry(tmp_path):
    repository = FlakyRepository(Settings(), fail_insert=True)
    marker = FakeMarker()
    controller = _fresh_controller(repository, marker, FakeAteq(), tmp_path,
                                   safe_stop=FakePlc())
    controller.start_cycle(make_selection(mode="single"))
    with pytest.raises(RuntimeError, match="db insert failed"):
        controller.test_first()
    assert controller.phase is Phase.FAULT
    assert controller.recovery_required is True
    assert len(controller.ateq.requests) == 1
    assert marker.intents == set()


def test_tc25_db_update_failure_faults_without_retry(tmp_path):
    repository = FlakyRepository(Settings(), fail_update=True)
    marker = FakeMarker()
    controller = _fresh_controller(repository, marker, FakeAteq(), tmp_path,
                                   safe_stop=FakePlc())
    controller.start_cycle(make_selection(mode="dual"))
    controller.test_first()
    assert controller.phase is Phase.WAIT_2
    with pytest.raises(RuntimeError, match="db update failed"):
        controller.test_second()
    assert controller.phase is Phase.FAULT
    assert controller.recovery_required is True
    assert len(controller.ateq.requests) == 2
    assert marker.intents == set()
