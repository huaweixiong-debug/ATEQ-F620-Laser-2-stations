from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.ateq import AteqResponse, FakeAteq
from app.config import Settings
from app.composition import build_services, ReadOnlyAteq
from app.journal import CycleJournal
from app.license import LicenseVerifier
from app.models import Measurement, Phase, Result, RunMode, StationId
from app.models import RecoveryRecord
from app.permissions import AuthSession, SecurityContext
from app.plc import FakePlc
from app.printer import FakePrinter
from app.repository import FakeRepository, PyMySQLRepository
from app.station import StationController
from app.contracts import AteqPort, PrinterPort, RepositoryPort
from app.settings_service import ProductSettingsService


def security(valid: bool = True) -> SecurityContext:
    status = LicenseVerifier(simulator=True).verify(b"SIMULATE", b"SIMULATE-SIGNATURE") if valid else None
    return SecurityContext(AuthSession(demo=True), status)


def controller(tmp_path: Path, *, plc=None, ateq=None, sec=None) -> StationController:
    return StationController(StationId.A, FakeRepository(Settings()), FakePrinter(), ateq or FakeAteq(),
                              CycleJournal(tmp_path / "A.json"), plc, security=sec or security())


def test_fault_with_active_record_cannot_be_cleared_by_reset(tmp_path):
    plc = FakePlc(); station = controller(tmp_path, plc=plc, ateq=FakeAteq())
    station.scan("P0"); plc.write_bit(4, 4, True); station.reset()
    assert station.recovery_required and station.phase is Phase.FAULT and not plc.outputs_energized()
    with pytest.raises(RuntimeError): station.reset()
    with pytest.raises(PermissionError): station.resolve_recovery()
    station.security.login("admin", "simulate-admin")
    station.resolve_recovery("audited")
    assert station.phase is Phase.IDLE and station.security.audit_events[-1]["action"] == "recovery_resolve"


def test_corrupt_startup_safe_stops_preenergized_plc(tmp_path):
    journal_path = tmp_path / "A.json"; journal_path.write_text("not-json", encoding="utf-8")
    plc = FakePlc(); plc.write_bit(4, 4, True)
    station = controller(tmp_path, plc=plc)
    assert station.recovery_required and station.phase is Phase.FAULT and not plc.outputs_energized()


class BareMeasurementAteq(FakeAteq):
    def run(self, request):
        return Measurement(1, 1, Result.OK, b"bare")


class WrongStationAteq(FakeAteq):
    def run(self, request):
        wrong = AteqResponse(request.__class__("B", request.cycle_id, request.program, request.sequence, request.timestamp), Measurement(1, 1, Result.OK, b"x"), b"x")
        return wrong


def test_ateq_bare_or_wrong_identity_is_rejected_before_db(tmp_path):
    for adapter in (BareMeasurementAteq(), WrongStationAteq()):
        repo = FakeRepository(Settings()); station = StationController(StationId.A, repo, FakePrinter(), adapter, security=security())
        station.scan("X")
        with pytest.raises(Exception): station.test_first()
        assert not repo.records


class CallFailsAteq(FakeAteq):
    def run(self, request):
        raise TimeoutError("timeout after command")


def test_ateq_intent_survives_call_failure_and_reinstantiation(tmp_path):
    station = controller(tmp_path, ateq=CallFailsAteq())
    station.scan("X")
    with pytest.raises(TimeoutError): station.test_first()
    snap = station.journal.recover()
    assert snap["ateq_intents"] and snap["ateq_intents"][-1]["state"] == "TEST_INTENT"
    restored = controller(tmp_path, ateq=FakeAteq())
    assert restored.recovery_required and restored.phase is Phase.FAULT


def test_pymysql_constructor_is_non_connecting_and_fail_closed():
    repo = PyMySQLRepository(host="127.0.0.1", user="u", password="p", database="test")
    with pytest.raises(RuntimeError): repo.insert_stage1(None)


def test_missing_license_is_denied_at_service_layer():
    sec = SecurityContext(AuthSession(demo=False), None)
    station = StationController(StationId.A, FakeRepository(Settings()), FakePrinter(), FakeAteq(), security=sec)
    with pytest.raises(PermissionError): station.scan("NO-LICENSE")


def test_product_settings_draft_cannot_bypass_authenticated_commit():
    operator = ProductSettingsService(SecurityContext(AuthSession(demo=False), security().license_status))
    with pytest.raises(PermissionError): operator.save("OPERATOR-DRAFT")
    assert operator.current_product() == "SIM-PART"
    admin_security = security(); admin_security.login("admin", "simulate-admin")
    service = ProductSettingsService(admin_security)
    service.save("COMMITTED")
    assert service.current_product() == "COMMITTED"


def test_shadow_services_have_no_write_paths():
    policy, plc, ateq, repo, printer = build_services(Settings(mode=RunMode.SHADOW))
    with pytest.raises(PermissionError): plc.write_bit(0, 0, True)
    with pytest.raises(PermissionError): ateq.select_program("P")
    with pytest.raises(PermissionError): repo.mark_labeled("x")
    with pytest.raises(PermissionError): printer.print_label(None)


def test_fake_adapters_conform_to_runtime_protocols():
    assert isinstance(FakeAteq(), AteqPort)
    assert isinstance(FakeRepository(Settings()), RepositoryPort)
    assert isinstance(FakePrinter(), PrinterPort)
    assert isinstance(ReadOnlyAteq(), AteqPort)


def test_smoke_cycle_is_explicit_and_two_station():
    root = Path(__file__).parents[1]
    result = subprocess.run([sys.executable, "-m", "app.main", "--mode", "simulate", "--smoke-cycle"], cwd=root, capture_output=True, text=True)
    assert result.returncode == 0
    assert "stations=2 records=2 labels=2" in result.stdout


def test_two_real_threads_and_independent_controllers():
    repo, printer = FakeRepository(Settings()), FakePrinter()
    def run(station_id):
        c = StationController(station_id, repo, printer, FakeAteq(), security=security())
        c.scan(station_id.value); c.test_first(); c.test_second(); c.label(); return c.record.cycle_id
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(run, [StationId.A, StationId.B]))
    assert len(set(ids)) == 2 and len(repo.records) == 2 and len(printer.intents) == 2


def test_complete_record_reconstructs_on_restart(tmp_path):
    station = controller(tmp_path)
    station.scan("RESTORE"); station.test_first(); station.test_second(); assert station.label()
    restored = controller(tmp_path)
    assert restored.phase is Phase.COMPLETE and restored.record and restored.record.second


@pytest.mark.parametrize(("current", "stored"), [(StationId.A, StationId.B), (StationId.B, StationId.A)])
def test_wrong_station_journal_is_faulted_and_preserved(tmp_path, current, stored):
    path = tmp_path / f"{current.value}.json"
    journal = CycleJournal(path)
    journal.write_record(RecoveryRecord(stored, f"{stored.value}-foreign", Phase.READY, None))
    plc = FakePlc(); plc.write_bit(4, 4, True)
    station = StationController(current, FakeRepository(Settings()), FakePrinter(), FakeAteq(), journal, plc, security=security())
    assert station.phase is Phase.FAULT and station.recovery_required and path.exists() and not plc.outputs_energized()
    assert journal.recover()["station"] == stored.value


class EmptyRawAteq(FakeAteq):
    def run(self, request):
        measurement = Measurement(1, 1, Result.OK, b"")
        return AteqResponse(request, measurement, b"")


def test_empty_ateq_raw_is_rejected_before_db(tmp_path):
    repo = FakeRepository(Settings())
    station = StationController(StationId.A, repo, FakePrinter(), EmptyRawAteq(), security=security())
    station.scan("EMPTY")
    with pytest.raises(Exception): station.test_first()
    assert not repo.records


def test_recovery_audit_survives_restart_and_contains_snapshot(tmp_path):
    station = controller(tmp_path)
    station.scan("AUDIT")
    station._safe_fault(RuntimeError("operator abort"), "fault")
    station.security.login("admin", "simulate-admin")
    station.resolve_recovery("audited reason")
    records = CycleJournal(tmp_path / "A.json").audit_records()
    assert records and records[-1]["actor"] == "admin" and records[-1]["reason"] == "audited reason"
    assert records[-1]["cycle_id"].startswith("A-") and records[-1]["snapshot"] and records[-1]["snapshot_sha256"]
