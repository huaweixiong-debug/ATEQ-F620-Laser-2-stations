from pathlib import Path
import pytest
from app.journal import CycleJournal
from app.permissions import Role, SecurityContext, allows
from app.scanner import ScannerGuard
from app.config import Settings, parse_setup_ini
from app.models import RunMode, TraceRecord, StationId
from app.repository import FakeRepository
from app.calibration import Calibration, CalibrationPhase
from app.scanner import ScannerFramer
from app.permissions import AuthSession

def test_scanner_rejects_immediate_duplicate():
    guard = ScannerGuard()
    assert guard.accept("ABC")
    assert not guard.accept("ABC")

def test_operator_cannot_use_dangerous_actions():
    assert not allows(Role.OPERATOR, "manual_output")
    assert allows(Role.ADMIN, "manual_output")

def test_cycle_journal_recovers(tmp_path: Path):
    journal = CycleJournal(tmp_path / "cycle.json")
    journal.write(station="A", cycle_id="A-1", phase="TEST_1")
    assert journal.recover()["cycle_id"] == "A-1"
    assert journal.recover()["phase"] == "TEST_1"

def test_corrupt_cycle_journal_is_rejected(tmp_path: Path):
    journal = CycleJournal(tmp_path / "cycle.json")
    (tmp_path / "cycle.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(Exception):
        journal.recover()

def test_forged_capability_cannot_enable_live_repository():
    repo = FakeRepository(Settings(mode=RunMode.LIVE))
    with pytest.raises(PermissionError):
        repo.insert_stage1(TraceRecord(StationId.A, "x", "x", cycle_id="x"), object())

def test_scanner_partial_frame_and_calibration_order():
    framer = ScannerFramer(b"\n")
    assert framer.feed(b"ABC") == []
    assert framer.feed(b"\nDEF\n") == ["ABC", "DEF"]
    calibration = Calibration()
    assert calibration.sample("NG") is CalibrationPhase.WAIT_OK
    assert calibration.sample("OK") is CalibrationPhase.COMPLETE

def test_gbk_setup_parser_reports_unknown(tmp_path: Path):
    path = tmp_path / "Setup.ini"
    path.write_bytes("ATEQ_COM_A = \"COM6\"\nUnknown = x\n".encode("gbk"))
    values, unknown = parse_setup_ini(path)
    assert values["ateq_com_a"] == "COM6" and "Unknown" in unknown

def test_authentication_does_not_allow_role_self_promotion():
    session = AuthSession()
    assert session.role is Role.OPERATOR
    assert not session.login("admin", "wrong")
    assert session.login("admin", "simulate-admin") and session.role is Role.ADMIN

def test_calibration_cancel_requires_admin_and_restarts_period():
    calibration = Calibration(station=StationId.B, initial_due=True,
                              period_seconds=2 * 60 * 60)
    security = SecurityContext(AuthSession())
    with pytest.raises(PermissionError):
        security.require("calibration_cancel")
    with pytest.raises(PermissionError):
        calibration.cancel("operator", "maintenance")
    assert security.login("admin", "simulate-admin")
    security.require("calibration_cancel")

    calibration.cancel("admin", "设备维护")

    assert calibration.due is False
    assert calibration.locked is False
    assert calibration.validation_started is False
    assert calibration.remaining_seconds == 2 * 60 * 60
    assert calibration.phase is CalibrationPhase.COMPLETE
    event = calibration.audit_events[-1]
    assert event["actor"] == "admin"
    assert event["reason"] == "设备维护"
    assert event["station"] == "B"
    assert event["time"]
