"""TC14-TC16, TC28-TC37, TC43/TC44, TC50-TC55: structured contracts.

Covers the frozen date scheme, the database-authoritative mark readback gate
(BR11/BR12), the service-layer remark permission (BR19), recovery journal
classification (BR18) and calibration pairing (BR20).  No database server,
serial port, PLC or laser is touched: repositories/markers are scripted fakes.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.ateq import FakeAteq
from app.calibration import Calibration, CalibrationPhase
from app.journal import CycleJournal
from app.laser import MarkReceipt, build_mark_text
from app.license import LicenseStatus
from app.models import (CycleSelection, MarkState, Measurement, Phase,
                        RecoveryRecord, Result, StationId, TraceRecord)
from app.permissions import AuthSession, SecurityContext
from app.station import StationController

from tests.helpers import make_security, make_selection


# ---------------------------------------------------------------- helpers

def _operator_security() -> SecurityContext:
    return SecurityContext(AuthSession(demo=True), license_status=LicenseStatus(True, "test"))


class RecordingRepository:
    """Scriptable RepositoryPort stub; get() must never be consulted for mark."""

    def __init__(self, committed=None, mark_error: Exception | None = None) -> None:
        self.committed = committed
        self.mark_error = mark_error
        self.get_calls = 0
        self.marked_calls = 0

    def insert_stage1(self, record, capability=None):
        return record

    def update_stage2(self, cycle_id, measurement, capability=None):
        return None

    def row_id(self, cycle_id):
        return 1

    def get(self, cycle_id):
        self.get_calls += 1
        return None

    def get_committed(self, cycle_id):
        if isinstance(self.committed, Exception):
            raise self.committed
        return self.committed

    def mark_marked(self, cycle_id, capability=None):
        self.marked_calls += 1
        if self.mark_error is not None:
            raise self.mark_error

    def query(self, text=""):
        return []


class RecordingMarker:
    def __init__(self, accepted: bool = True) -> None:
        self.accepted = accepted
        self.records: list[TraceRecord] = []

    def mark(self, record: TraceRecord) -> MarkReceipt:
        self.records.append(record)
        return MarkReceipt(self.accepted, f"mark-{record.cycle_id}", "")


def making_controller(tmp_path, repository, marker=None, security=None, mode="single"):
    """Drive one cycle into MARKING without touching hardware or a real DB."""
    controller = StationController(
        StationId.A, repository, marker or RecordingMarker(), FakeAteq(),
        CycleJournal(tmp_path / "journal.json"), security=security or make_security())
    controller.start_cycle(make_selection(mode=mode))
    controller.test_first()
    if mode == "dual":
        controller.test_second()
    assert controller.phase is Phase.MARKING
    return controller


def db_record(cycle_id="A-DB-1", station=StationId.A, part_no="DB-PART", person="DB-OP",
              test_mode="dual", first=None, second=None, date_scheme="YYYYMMDD",
              created_at=None) -> TraceRecord:
    record = TraceRecord(
        station=station, part_no=part_no, person=person, cycle_id=cycle_id,
        test_mode=test_mode, date_scheme=date_scheme,
        created_at=created_at or datetime(2026, 10, 7, 10, 0, 0, tzinfo=timezone.utc))
    record.first = first
    record.second = second
    return record


def ok_measurement(pressure=1.25, leakage=0.5, unit_p="Kpa", unit_l="ml/min",
                   result=Result.OK) -> Measurement:
    return Measurement(pressure, leakage, result, b"DB", unit_p, unit_l)


# --------------------------------------------------- TC14-TC16 date freeze

def test_tc14_five_positional_fields_keep_default_date_scheme():
    selection = CycleSelection(StationId.A, "P-1", "OP-1", "dual", "1")
    assert selection.date_scheme == "YYYYMMDD"


def test_tc15_blank_or_non_string_date_scheme_rejected():
    with pytest.raises(ValueError):
        CycleSelection(StationId.A, "P-1", "OP-1", "dual", "1", date_scheme="   ")
    with pytest.raises(ValueError):
        CycleSelection(StationId.A, "P-1", "OP-1", "dual", "1", date_scheme=20260101)
    selection = CycleSelection(StationId.A, "P-1", "OP-1", "dual", "1",
                               date_scheme="  YYMMDD ")
    assert selection.date_scheme == "YYMMDD"


def test_tc16_non_default_date_scheme_survives_journal_recovery(tmp_path):
    journal_path = tmp_path / "journal.json"
    marker = RecordingMarker()
    controller = StationController(
        StationId.A, RecordingRepository(), marker, FakeAteq(),
        CycleJournal(journal_path), security=make_security())
    selection = CycleSelection(StationId.A, "P-2", "OP-2", "single", "1",
                               date_scheme="YYMMDD")
    controller.start_cycle(selection)
    assert controller.record.date_scheme == "YYMMDD"
    recovered = StationController(
        StationId.A, RecordingRepository(), marker, FakeAteq(),
        CycleJournal(journal_path), security=make_security())
    assert recovered.phase is Phase.FAULT  # unfinished cycle needs manual recovery
    assert recovered.record.date_scheme == "YYMMDD"
    assert recovered.record.marked is False
    assert marker.records == []  # recovery never marks


# --------------------------------------------- TC28-TC37 committed readback

def test_tc28_missing_committed_row_faults_without_marker(tmp_path):
    repo = RecordingRepository(committed=None)
    marker = RecordingMarker()
    controller = making_controller(tmp_path, repo, marker)
    assert controller.mark() is False
    assert controller.phase is Phase.FAULT
    assert controller.mark_state is MarkState.AMBIGUOUS
    assert controller.recovery_required is True
    assert marker.records == []
    assert repo.marked_calls == 0
    assert repo.get_calls == 0  # cache get must not be used as a fallback


def test_tc29_committed_readback_exception_faults_without_cache_fallback(tmp_path):
    repo = RecordingRepository(committed=RuntimeError("db down"))
    marker = RecordingMarker()
    controller = making_controller(tmp_path, repo, marker)
    assert controller.mark() is False
    assert controller.phase is Phase.FAULT
    assert marker.records == []
    assert repo.get_calls == 0


def test_tc30_marker_receives_committed_row_not_process_memory(tmp_path):
    repo = RecordingRepository()
    marker = RecordingMarker()
    controller = making_controller(tmp_path, repo, marker, mode="single")
    committed_at = datetime(2026, 10, 7, 8, 30, 15, tzinfo=timezone.utc)
    repo.committed = db_record(
        cycle_id=controller.record.cycle_id, part_no="Y-PART", person="Y-OP",
        test_mode="dual",  # DB-shaped row without the schema-v2 metadata columns
        first=ok_measurement(pressure=33.375, leakage=4.1255, unit_p="kPa", unit_l="mL/min"),
        second=None, created_at=committed_at)
    assert controller.mark() is True
    assert len(marker.records) == 1
    payload = marker.records[0]
    assert payload is not repo.committed  # deep copy, no shared object
    assert payload.part_no == "Y-PART"
    assert payload.person == "Y-OP"
    assert payload.cycle_id == controller.record.cycle_id
    assert payload.created_at == committed_at
    assert payload.first.pressure == 33.375
    assert payload.first.leakage == 4.1255
    assert payload.first.pressure_unit == "kPa"
    assert payload.first.leakage_unit == "mL/min"
    assert payload.test_mode == "single"  # frozen metadata overlay only
    assert payload.part_no != controller.record.part_no  # no memory fallback
    assert controller.phase is Phase.COMPLETE
    assert controller.record.marked is True


@pytest.mark.parametrize("mode,first,second", [
    ("single", ok_measurement(result=Result.NG), None),
    ("single", ok_measurement(result=Result.UNKNOWN), None),
    ("dual", ok_measurement(), ok_measurement(result=Result.NG)),
    ("dual", ok_measurement(), ok_measurement(result=Result.UNKNOWN)),
], ids=["TC31-first-NG", "TC31-first-UNKNOWN", "TC31-second-NG", "TC31-second-UNKNOWN"])
def test_tc31_non_ok_committed_measurement_blocks_marking(tmp_path, mode, first, second):
    repo = RecordingRepository()
    marker = RecordingMarker()
    controller = making_controller(tmp_path, repo, marker, mode=mode)
    repo.committed = db_record(cycle_id=controller.record.cycle_id, test_mode=mode,
                               first=first, second=second)
    assert controller.mark() is False
    assert controller.phase is Phase.FAULT
    assert marker.records == []


def test_tc32_wrong_type_station_or_cycle_blocks_marking(tmp_path):
    repo = RecordingRepository()
    marker = RecordingMarker()
    controller = making_controller(tmp_path, repo, marker, mode="single")
    for bad in ({"cycle_id": controller.record.cycle_id},
                db_record(cycle_id=controller.record.cycle_id, station=StationId.B,
                          test_mode="single", first=ok_measurement(), second=None),
                db_record(cycle_id="A-OTHER", test_mode="single",
                          first=ok_measurement(), second=None)):
        repo.committed = bad
        controller.phase = Phase.MARKING
        controller.recovery_required = False
        assert controller.mark() is False
        assert controller.phase is Phase.FAULT
    assert marker.records == []


def test_tc33_missing_required_measurement_blocks_marking(tmp_path):
    repo = RecordingRepository()
    marker = RecordingMarker()
    controller = making_controller(tmp_path, repo, marker, mode="dual")
    repo.committed = db_record(cycle_id=controller.record.cycle_id, test_mode="dual",
                               first=None, second=ok_measurement())
    assert controller.mark() is False
    assert marker.records == []
    controller.phase = Phase.MARKING
    controller.recovery_required = False
    repo.committed = db_record(cycle_id=controller.record.cycle_id, test_mode="dual",
                               first=ok_measurement(), second=None)
    assert controller.mark() is False
    assert marker.records == []


@pytest.mark.parametrize("bad", [
    Measurement(float("nan"), 0.1, Result.OK, b"DB", "Kpa", "ml/min"),
    Measurement(float("inf"), 0.1, Result.OK, b"DB", "Kpa", "ml/min"),
    Measurement(True, 0.1, Result.OK, b"DB", "Kpa", "ml/min"),
    Measurement("1.0", 0.1, Result.OK, b"DB", "Kpa", "ml/min"),
    Measurement(1.0, float("-inf"), Result.OK, b"DB", "Kpa", "ml/min"),
    Measurement(1.0, False, Result.OK, b"DB", "Kpa", "ml/min"),
], ids=["TC34-nan", "TC34-inf", "TC34-bool-p", "TC34-str-p",
        "TC34-negative-inf", "TC34-bool-l"])
def test_tc34_non_finite_or_non_numeric_measurement_blocks_marking(tmp_path, bad):
    repo = RecordingRepository()
    marker = RecordingMarker()
    controller = making_controller(tmp_path, repo, marker, mode="single")
    repo.committed = db_record(cycle_id=controller.record.cycle_id, test_mode="single",
                               first=bad, second=None)
    assert controller.mark() is False
    assert controller.phase is Phase.FAULT
    assert marker.records == []


def test_tc34_second_measurement_fields_validated_too(tmp_path):
    repo = RecordingRepository()
    marker = RecordingMarker()
    controller = making_controller(tmp_path, repo, marker, mode="dual")
    repo.committed = db_record(cycle_id=controller.record.cycle_id, test_mode="dual",
                               first=ok_measurement(),
                               second=Measurement(float("nan"), 0.1, Result.OK,
                                                  b"DB", "Kpa", "ml/min"))
    assert controller.mark() is False
    assert marker.records == []


def test_tc35_malformed_identity_and_unit_fields_block_marking(tmp_path):
    repo = RecordingRepository()
    marker = RecordingMarker()
    controller = making_controller(tmp_path, repo, marker, mode="single")
    cycle_id = controller.record.cycle_id
    cases = [
        db_record(cycle_id=cycle_id, part_no="  ", test_mode="single",
                  first=ok_measurement(), second=None),
        db_record(cycle_id=cycle_id, part_no=None, test_mode="single",
                  first=ok_measurement(), second=None),
        db_record(cycle_id=cycle_id, person="   ", test_mode="single",
                  first=ok_measurement(), second=None),
        db_record(cycle_id=cycle_id, person=12345, test_mode="single",
                  first=ok_measurement(), second=None),
        db_record(cycle_id=cycle_id, test_mode="single",
                  first=ok_measurement(unit_p=123), second=None),
        db_record(cycle_id=cycle_id, test_mode="single",
                  first=ok_measurement(unit_l=None), second=None),
    ]
    bad_created = db_record(cycle_id=cycle_id, test_mode="single",
                            first=ok_measurement(), second=None)
    bad_created.created_at = "2026-10-07T10:00:00+00:00"
    cases.append(bad_created)
    for bad in cases:
        repo.committed = bad
        controller.phase = Phase.MARKING
        controller.recovery_required = False
        assert controller.mark() is False
        assert controller.phase is Phase.FAULT
    assert marker.records == []


def test_tc36_single_freeze_does_not_require_db_test_mode(tmp_path):
    repo = RecordingRepository()
    marker = RecordingMarker()
    controller = making_controller(tmp_path, repo, marker, mode="single")
    repo.committed = db_record(cycle_id=controller.record.cycle_id,
                               test_mode="dual",  # DB row defaults to dual
                               first=ok_measurement(), second=None)
    assert controller.mark() is True
    payload = marker.records[0]
    assert payload.test_mode == "single"
    lines = build_mark_text(payload).split("\n")
    assert len(lines) == 8
    assert lines[4] == "" and lines[5] == ""  # P2/L2 empty for single cycle


def test_tc37_single_freeze_with_db_ng_second_still_blocks_marking(tmp_path):
    repo = RecordingRepository()
    marker = RecordingMarker()
    controller = making_controller(tmp_path, repo, marker, mode="single")
    repo.committed = db_record(cycle_id=controller.record.cycle_id, test_mode="single",
                               first=ok_measurement(),
                               second=ok_measurement(result=Result.NG))
    assert controller.mark() is False
    assert controller.phase is Phase.FAULT
    assert marker.records == []


def test_tc43_marked_update_failure_after_pulse_is_ambiguous(tmp_path):
    repo = RecordingRepository(mark_error=RuntimeError("commit failed"))
    marker = RecordingMarker()
    controller = making_controller(tmp_path, repo, marker, mode="single")
    repo.committed = db_record(cycle_id=controller.record.cycle_id, test_mode="single",
                               first=ok_measurement(), second=None)
    assert controller.mark() is False
    assert controller.phase is Phase.FAULT
    assert controller.mark_state is MarkState.AMBIGUOUS
    assert len(marker.records) == 1  # one physical pulse, never re-marked
    assert repo.marked_calls == 1


def test_tc44_rejected_marker_never_updates_db(tmp_path):
    repo = RecordingRepository()
    marker = RecordingMarker(accepted=False)
    controller = making_controller(tmp_path, repo, marker, mode="single")
    repo.committed = db_record(cycle_id=controller.record.cycle_id, test_mode="single",
                               first=ok_measurement(), second=None)
    assert controller.mark() is False
    assert controller.phase is Phase.FAULT
    assert controller.mark_state is MarkState.AMBIGUOUS
    assert len(marker.records) == 1
    assert repo.marked_calls == 0


# ------------------------------------------- TC50-TC53 permission/recovery

def test_tc50_operator_remark_denied_without_side_effects(tmp_path):
    repo = RecordingRepository()
    marker = RecordingMarker()
    controller = making_controller(tmp_path, repo, marker, mode="single",
                                   security=_operator_security())
    repo.committed = db_record(cycle_id=controller.record.cycle_id, test_mode="single",
                               first=ok_measurement(), second=None)
    assert controller.mark() is True
    assert controller.phase is Phase.COMPLETE
    with pytest.raises(PermissionError):
        controller.remark()
    assert controller.phase is Phase.COMPLETE  # original phase preserved
    assert len(marker.records) == 1  # no extra pulse
    assert repo.marked_calls == 1  # no DB mutation
    assert controller.record.marked is True


def test_tc51_admin_remark_pulses_exactly_once_per_request(tmp_path):
    repo = RecordingRepository()
    marker = RecordingMarker()
    controller = making_controller(tmp_path, repo, marker, mode="single")
    repo.committed = db_record(cycle_id=controller.record.cycle_id, test_mode="single",
                               first=ok_measurement(), second=None)
    assert controller.mark() is True
    assert len(marker.records) == 1
    assert controller.remark() is True
    assert len(marker.records) == 2
    assert controller.phase is Phase.COMPLETE
    assert controller.remark() is True
    assert len(marker.records) == 3
    assert repo.marked_calls == 3


@pytest.mark.parametrize("mark_state,expected", [
    (MarkState.NONE, MarkState.NONE),
    (MarkState.INTENT, MarkState.AMBIGUOUS),
    (MarkState.PULSED, MarkState.AMBIGUOUS),
], ids=["TC52-plain", "TC52-intent", "TC52-pulsed"])
def test_tc52_unfinished_or_pulsed_journal_requires_recovery(tmp_path, mark_state, expected):
    path = tmp_path / "journal.json"
    record = db_record(cycle_id="A-J1", test_mode="single",
                       first=ok_measurement(), second=None)
    journal = CycleJournal(path)
    journal.write_record(RecoveryRecord(StationId.A, record.cycle_id, Phase.TEST_1, record,
                                        mark_state=mark_state, recovery_required=False))
    marker = RecordingMarker()
    controller = StationController(StationId.A, RecordingRepository(), marker, FakeAteq(),
                                   CycleJournal(path), security=make_security())
    assert controller.phase is Phase.FAULT
    assert controller.recovery_required is True
    assert controller.mark_state is expected
    assert marker.records == []


def test_tc53_complete_journal_restores_complete_without_marks(tmp_path):
    path = tmp_path / "journal.json"
    record = db_record(cycle_id="A-J2", test_mode="single",
                       first=ok_measurement(), second=None)
    record.marked = True
    record.marked_at = datetime(2026, 10, 7, 11, 0, 0, tzinfo=timezone.utc)
    journal = CycleJournal(path)
    journal.write_record(RecoveryRecord(StationId.A, record.cycle_id, Phase.COMPLETE, record,
                                        mark_state=MarkState.MARKED, recovery_required=False))
    marker = RecordingMarker()
    controller = StationController(StationId.A, RecordingRepository(), marker, FakeAteq(),
                                   CycleJournal(path), security=make_security())
    assert controller.phase is Phase.COMPLETE
    assert controller.recovery_required is False
    assert controller.record.marked is True
    assert marker.records == []


# ----------------------------------------------------- TC54-TC55 calibration

def test_tc54_two_sample_pairs_required_and_wrong_order_not_counted():
    calibration = Calibration(required_samples=2, station=StationId.A, initial_due=True)
    calibration.begin_validation()
    assert calibration.sample("NG") is CalibrationPhase.WAIT_OK
    with pytest.raises(ValueError, match="顺序错误"):
        calibration.sample("NG")  # WAIT_OK expects an OK sample
    assert calibration.ng_count == 1 and calibration.ok_count == 0
    assert calibration.sample("OK") is CalibrationPhase.WAIT_NG
    with pytest.raises(ValueError, match="顺序错误"):
        calibration.sample("OK")  # WAIT_NG expects an NG sample
    assert calibration.ng_count == 1 and calibration.ok_count == 1
    assert calibration.sample("NG") is CalibrationPhase.WAIT_OK
    assert calibration.sample("OK") is CalibrationPhase.COMPLETE
    assert calibration.ng_count == 2 and calibration.ok_count == 2


def test_tc55_period_expiry_locks_and_invalid_cancel_rejected():
    calibration = Calibration(required_samples=1, station=StationId.A, initial_due=True)
    calibration.begin_validation()
    calibration.sample("NG")
    calibration.sample("OK")
    calibration.clear_after_resume()
    assert calibration.tick(elapsed_seconds=calibration.period_seconds - 1) is False
    assert calibration.tick(elapsed_seconds=1) is True
    assert calibration.due is True and calibration.locked is True

    second = Calibration(required_samples=1, station=StationId.A, initial_due=True)
    second.begin_validation()
    with pytest.raises(PermissionError):
        second.cancel("operator", "换型暂停")
    with pytest.raises(PermissionError):
        second.cancel("admin", "   ")
    assert second.due is True and second.locked is True
    second.cancel("admin", "计划停机")
    assert second.due is False and second.locked is False
