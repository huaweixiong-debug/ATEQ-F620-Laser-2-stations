"""Shared fixtures: single-station controller wired to fakes and sim points."""
import pytest

from app.ateq import FakeAteq
from app.config import Settings
from app.journal import CycleJournal
from app.laser import FakeMarker
from app.license import LicenseStatus
from app.models import CycleSelection, StationId
from app.permissions import AuthSession, SecurityContext
from app.plc import FakePlc
from app.points import sim_point_map
from app.repository import FakeRepository
from app.station import StationController


def make_security(admin: bool = True) -> SecurityContext:
    security = SecurityContext(AuthSession(demo=True),
                               license_status=LicenseStatus(True, "test"))
    if admin:
        security.login("admin", "simulate-admin")
    return security


def make_selection(station: StationId = StationId.A, mode: str = "dual",
                   part: str = "E118015100", person: str = "张三") -> CycleSelection:
    return CycleSelection(station, part, person, mode, "1")


@pytest.fixture
def station_parts(tmp_path):
    """Return (controller, repository, marker, plc, journal_path)."""
    settings = Settings()
    repository = FakeRepository(settings)
    marker = FakeMarker()
    plc = FakePlc()
    journal_path = tmp_path / "journal.json"
    controller = StationController(StationId.A, repository, marker, FakeAteq(),
                                   CycleJournal(journal_path), safe_stop=plc,
                                   security=make_security(), mark_samples=False)
    return controller, repository, marker, plc, journal_path
