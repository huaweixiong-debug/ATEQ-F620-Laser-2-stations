from pathlib import Path
from types import SimpleNamespace

import pytest

from app.barprint import BarTenderCmdPrinter
from app.model_settings import ModelConfig, ModelSettingsService
from app.models import StationId, TraceRecord
from app.permissions import AuthSession, SecurityContext
from app.ui_replica import MainWindow


def _admin_security():
    security = SecurityContext(AuthSession(demo=True))
    assert security.login("admin", "simulate-admin")
    return security


def test_template_choices_include_nonconventional_production_names(tmp_path):
    names = (
        "labelA.btw", "labelA-1.btw", "labelB.btw", "labelB-1.btw",
        "E113015200-A.btw", "Cal_NG_A.btw", "Cal_OK_B.btw",
        "Calibration_A.btw", "notes.txt",
    )
    for name in names:
        (tmp_path / name).touch()

    choices = MainWindow._template_choices(SimpleNamespace(data_dir=tmp_path), "A")

    assert "labelA.btw" in choices
    assert "labelA-1.btw" in choices
    assert "labelB.btw" in choices
    assert "labelB-1.btw" in choices
    assert "E113015200-A.btw" in choices
    assert not any(name.startswith(("Cal_NG_", "Cal_OK_", "Calibration_"))
                   for name in choices)
    assert "notes.txt" not in choices


def test_model_settings_round_trip_independent_arbitrary_a_b_templates(tmp_path):
    service = ModelSettingsService(_admin_security(), tmp_path / "日期设置.ini")
    template_a = str(tmp_path / "labelA-1.btw")
    template_b = str(tmp_path / "labelB.btw")
    config = ModelConfig(
        part_no="PART-1",
        customer_no="CUSTOMER-1",
        template_family=str(tmp_path / "legacy-family.btw"),
        template_path_a=template_a,
        template_path_b=template_b,
    )

    service.save_all([config])
    loaded = service.load("PART-1")

    assert loaded.template_for("A") == template_a
    assert loaded.template_for("B") == template_b


def test_printer_uses_selected_template_name_and_station_field_files(tmp_path, monkeypatch):
    data_dir = tmp_path / "Data"
    data_dir.mkdir()
    executable = tmp_path / "bartend.exe"
    executable.touch()
    template = data_dir / "labelB-1.btw"
    template.touch()
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr("app.barprint.subprocess.run", fake_run)
    printer = BarTenderCmdPrinter(executable, data_dir, data_dir / "label_data.txt")
    record = TraceRecord(
        station=StationId.B,
        serial_no="0001",
        code_2d="QR-B-0001",
        part_no="PART-B",
        person="Operator",
        cycle_id="B-cycle-1",
        template_path=str(template),
    )

    receipt = printer.print_label(record)

    assert receipt.accepted
    assert len(calls) == 1
    assert Path(calls[0][0][1]) == template.resolve()
    assert (data_dir / "二维码B.txt").read_text(encoding="utf-8") == "QR-B-0001"
    assert not (data_dir / "二维码A.txt").exists()


@pytest.mark.parametrize("template_name", ["outside.btw", "Cal_NG_B.btw", "wrong-extension.txt"])
def test_printer_rejects_outside_calibration_or_non_btw_templates(
        tmp_path, monkeypatch, template_name):
    data_dir = tmp_path / "Data"
    data_dir.mkdir()
    executable = tmp_path / "bartend.exe"
    executable.touch()
    candidate = (tmp_path / template_name if template_name == "outside.btw"
                 else data_dir / template_name)
    candidate.touch()
    calls = []
    monkeypatch.setattr("app.barprint.subprocess.run",
                        lambda *args, **kwargs: calls.append(args))
    printer = BarTenderCmdPrinter(executable, data_dir, data_dir / "label_data.txt")
    record = TraceRecord(
        station=StationId.B,
        serial_no="0001",
        code_2d="QR-B-0001",
        cycle_id=f"B-{template_name}",
        template_path=str(candidate),
    )

    with pytest.raises((ValueError, FileNotFoundError)):
        printer.print_label(record)

    assert calls == []
