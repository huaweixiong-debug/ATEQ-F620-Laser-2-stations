"""Regression tests for operator-selected ATEQ program synchronization."""
import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication

from app.ateq import SerialAteq
from app.model_settings import ModelConfig, ModelSettingsService, PersonnelService
from app.models import Phase
from tests.helpers import make_security


PART = "E118015100"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def window(qapp, tmp_path):
    from app.ui_replica import MainWindow

    window = MainWindow()
    security = make_security()
    window.model_settings = ModelSettingsService(security, tmp_path / "日期设置.ini")
    window.model_settings.save(ModelConfig(
        part_no=PART, customer_no=PART, date_scheme="YYYYMMDD", ateq_program="1"))
    window.personnel = PersonnelService(security, tmp_path / "作业员列表.txt")
    window.personnel.write_all(["张三"])
    window._refresh_runtime_choices()
    yield window
    window.close()
    window.deleteLater()


def test_model_change_after_completed_cycle_selects_program(window, monkeypatch):
    card = window.cards[0]
    card.part_no.setCurrentText(PART)
    ateq = SerialAteq("COMX", "A", serial_factory=lambda **kwargs: None)
    card.controller.ateq = ateq
    card.controller.phase = Phase.COMPLETE
    selected = []
    monkeypatch.setattr(ateq, "select_program", selected.append)
    monkeypatch.setattr(ateq, "current_program", lambda: int(selected[-1]))

    card._product_changed()

    assert selected == ["1"]
    assert card.ateq_no.text() == "1"
