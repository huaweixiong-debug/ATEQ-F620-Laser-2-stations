from app.ateq import modbus_crc16
from app.config import Settings
from app.models import Phase, Result, RunMode, StationId
from app.printer import FakePrinter
from app.repository import FakeRepository
from app.station import StationController
from app.ateq import FakeAteq
from app.plc import FakePlc
from app.journal import CycleJournal
from app.permissions import SecurityContext, AuthSession
from app.license import LicenseVerifier

def sim_security():
    return SecurityContext(AuthSession(demo=True), LicenseVerifier(simulator=True).verify(b"SIMULATE", b"SIMULATE-SIGNATURE"))

def make_station(result=Result.OK):
    settings = Settings()
    repo, printer = FakeRepository(settings), FakePrinter()
    return StationController(StationId.A, repo, printer, FakeAteq(result), security=sim_security()), repo, printer

def test_simulation_completes_two_stages_and_label():
    station, repo, printer = make_station()
    station.scan("ABC")
    assert station.phase is Phase.READY
    station.test_first()
    assert station.record.cycle_id in repo.records
    assert repo.records[station.record.cycle_id].first is not None
    station.test_second()
    assert station.label()
    assert station.phase is Phase.COMPLETE
    assert repo.count_ok() == 1
    assert len(printer.intents) == 1

def test_duplicate_cycle_label_is_idempotent():
    station, _, printer = make_station()
    station.scan("ABC")
    station.test_first(); station.test_second(); station.label(); station.phase = Phase.LABELING; station.label()
    assert len(printer.intents) == 1

def test_ng_first_stage_does_not_run_second():
    station, _, _ = make_station(Result.NG)
    station.scan("ABC", test_mode="single")
    station.test_first()
    assert station.phase is Phase.COMPLETE

def test_dual_mode_waits_for_second_result_even_when_first_is_ng():
    station, repo, _ = make_station(Result.NG)
    station.scan("ABC", test_mode="dual")
    station.test_first()
    assert station.phase is Phase.WAIT_2
    assert repo.records[station.record.cycle_id].first.result is Result.NG
    station.test_second()
    assert station.record.second.result is Result.NG
    assert repo.records[station.record.cycle_id].second.result is Result.NG

def test_external_plc_start_ateq_is_not_started_by_controller():
    station, _, _ = make_station()
    station.ateq.external_start = True
    station.scan("ABC")
    station.test_first()
    assert station.ateq.start_count == 0

def test_live_mode_is_not_write_enabled_by_default():
    assert not Settings(mode=RunMode.SIMULATE).can_write()
    assert not Settings(mode=RunMode.LIVE).can_write()

def test_crc_known_vector():
    assert modbus_crc16(bytes.fromhex("01030000000A")) == 0xCDC5

def test_two_stations_have_independent_cycles():
    settings = Settings(); repo, printer = FakeRepository(settings), FakePrinter()
    a = StationController(StationId.A, repo, printer, FakeAteq(), security=sim_security())
    b = StationController(StationId.B, repo, printer, FakeAteq(), security=sim_security())
    a.scan("A"); b.scan("B"); a.test_first(); b.test_first()
    assert a.record.cycle_id != b.record.cycle_id
    assert len(repo.records) == 2

def test_ateq_requests_are_correlated_and_sequenced():
    station, _, _ = make_station()
    station.ateq.select_program("P1")
    station.scan("ABC"); station.test_first(); station.test_second()
    assert [req.cycle_id for req in station.ateq.requests] == [station.record.cycle_id] * 2
    assert [req.sequence for req in station.ateq.requests] == [1, 2]

def test_active_reset_safe_stops_and_persists_fault(tmp_path):
    settings = Settings(); repo, printer = FakeRepository(settings), FakePrinter(); plc = FakePlc()
    journal = CycleJournal(tmp_path / "A.json")
    station = StationController(StationId.A, repo, printer, FakeAteq(), journal, plc, security=sim_security())
    station.scan("ABC"); plc.write_bit(0, 5, True); station.reset()
    assert station.phase is Phase.FAULT and not plc.outputs_energized()
    assert journal.recover()["phase"] == "FAULT"

def test_mark_labeled_failure_persists_ambiguous_print(tmp_path):
    class BrokenRepository(FakeRepository):
        def mark_labeled(self, cycle_id, capability=None):
            raise RuntimeError("db down")
    repo, printer = BrokenRepository(Settings()), FakePrinter(); journal = CycleJournal(tmp_path / "A.json")
    station = StationController(StationId.A, repo, printer, FakeAteq(), journal, security=sim_security())
    station.scan("ABC"); station.test_first(); station.test_second(); assert not station.label()
    snapshot = journal.recover()
    assert snapshot["print_state"] == "AMBIGUOUS" and snapshot["receipt"]
