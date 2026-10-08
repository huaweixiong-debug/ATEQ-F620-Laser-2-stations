import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime, timedelta, timezone

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from app.config import Settings
from app.models import Measurement, Result, StationId, TraceRecord
from app.ui import MainWindow


def _window():
    app = QApplication.instance() or QApplication([])
    window = MainWindow(); window.show(); app.processEvents()
    return app, window


def test_language_switch_translates_tabs_and_principal_labels_without_data_loss():
    app, window = _window()
    window.setup_username.setText("KEEP-PART")
    window.query_fields[(StationId.A, "code")].setText("KEEP-QR")
    for language, tabs in (("English", ["Main", "Setup", "Query", "Manual"]),
                           ("Français", ["Principale", "Coup Monté", "Requête", "Manuelle"]),
                           ("中文", ["测试", "设置", "查询", "手动"])):
        window.language_selector.setCurrentText(language)
        assert [window.tabs.tabBar().tabText(i) for i in range(4)] == tabs
        assert window.setup_username.text() == "KEEP-PART"
        assert window.query_fields[(StationId.A, "code")].text() == "KEEP-QR"
        assert window.findChild(QPushButton, "model_save") is not None
    window.close(); app.processEvents()


def test_query_datetime_ranges_are_independent_and_export_filtered(tmp_path):
    app, window = _window()
    now = datetime.now(timezone.utc)
    old = TraceRecord(StationId.A, "OLD", "OLD", first=Measurement(1, 1, Result.OK, b"old"), cycle_id="old", created_at=now - timedelta(days=3))
    recent = TraceRecord(StationId.A, "RECENT", "RECENT", first=Measurement(1, 1, Result.OK, b"recent"), cycle_id="recent", created_at=now)
    other = TraceRecord(StationId.B, "B-RECENT", "B-RECENT", first=Measurement(1, 1, Result.NG, b"b"), cycle_id="b", created_at=now)
    window.repository.records.update({"old": old, "recent": recent, "b": other})
    start = window.query_fields[(StationId.A, "start")]; finish = window.query_fields[(StationId.A, "finish")]
    start.setDateTime(QDateTime.fromSecsSinceEpoch(int((now - timedelta(days=1)).timestamp())))
    finish.setDateTime(QDateTime.fromSecsSinceEpoch(int((now + timedelta(days=1)).timestamp())))
    window.refresh_query()
    assert window.query_tables[StationId.A].item(0, 2).text() == "RECENT"
    assert window.query_tables[StationId.A].item(1, 2) is None or window.query_tables[StationId.A].item(1, 2).text() == ""
    # B has its own range and remains independent.
    bstart = window.query_fields[(StationId.B, "start")]; bfinish = window.query_fields[(StationId.B, "finish")]
    bstart.setDateTime(QDateTime.fromSecsSinceEpoch(int((now + timedelta(days=1)).timestamp())))
    bfinish.setDateTime(QDateTime.fromSecsSinceEpoch(int((now + timedelta(days=2)).timestamp())))
    window.refresh_query()
    assert window.query_tables[StationId.B].item(0, 2) is None or window.query_tables[StationId.B].item(0, 2).text() == ""
    # Invalid range is explicit and returns no data.
    start.setDateTime(finish.dateTime().addDays(2)); window.refresh_query()
    assert "开始时间" in window.query_status[StationId.A].text()
    out = tmp_path / "filtered.csv"; window.download_query(StationId.A, str(out))
    assert "RECENT" not in out.read_text(encoding="utf-8-sig")
    window.close(); app.processEvents()


def test_setup_ini_com_mapping_is_read_only_and_not_guessed(tmp_path):
    fixture = tmp_path / "Setup.ini"
    fixture.write_bytes("ATEQ F620 A (Restart Software to Active) = COM4\nATEQ F620 B (Restart Software to Active) = COM6\n".encode("gbk"))
    settings = Settings.from_file(fixture)
    assert settings.ports_confirmed and settings.ateq_ports == ("COM4", "COM6")
    missing = tmp_path / "missing.ini"
    try:
        Settings.from_file(missing)
    except ValueError as exc:
        assert "不存在" in str(exc)
    else:
        raise AssertionError("missing setup must be blocked")


def test_screenshot_sizes_and_manual_primary_structure():
    app, window = _window()
    window.resize(1920, 1080); window.show(); app.processEvents(); assert window.size().toTuple() == (1920, 1080); assert window.grab().size().toTuple() == (1920, 1080)
    window.resize(1366, 768); window.show(); app.processEvents(); assert window.size().toTuple() == (1366, 768); assert window.grab().size().toTuple() == (1366, 768)
    window.tabs.setCurrentIndex(3); app.processEvents()
    for station in ("A", "B"):
        for signal in ("clamp", "transfer", "block", "stamp", "door_disable", "manual"):
            assert window.findChild(QPushButton, f"manual_{signal}_{station}").isVisible()
        assert not window.findChild(QPushButton, f"manual_pressure_{station}").isVisible()
        assert not window.findChild(QPushButton, f"manual_start_{station}").isVisible()
    window.close(); app.processEvents()


def test_table_header_geometry_and_canonical_package_layout():
    app, window = _window()
    for table in [window.cards[0].table, window.cards[1].table, *window.query_tables.values()]:
        assert table.horizontalHeader().height() >= 40
        assert table.horizontalHeader().textElideMode().name == "ElideNone"
        assert all(table.columnWidth(i) >= 52 for i in range(10))
        assert all("/" not in (table.horizontalHeaderItem(i).text() or "") for i in range(10))
    package = os.path.join(os.path.dirname(__file__), "..", "package_dist_final")
    assert not os.path.isfile(os.path.join(package, "LeakTest2Channels.exe"))
    assert os.path.isfile(os.path.join(package, "LeakTest2Channels", "LeakTest2Channels.exe"))
    window.close(); app.processEvents()


def test_footer_indicator_labels_fit_text_at_1366_and_1920():
    app, window = _window()
    for size in ((1366, 768), (1920, 1080)):
        window.resize(*size); window.show(); app.processEvents()
        for language in ("中文", "English", "Français"):
            window.language_selector.setCurrentText(language); app.processEvents()
            for card in window.cards:
                for label in card.indicator_labels.values():
                    assert label.isVisible()
                    assert label.width() >= label.sizeHint().width()
                # Start validation is the fourth footer tile's button, not a
                # caption with a separate confirmation control underneath.
                start_button = card.start_validation_button
                assert start_button.isVisible()
                assert start_button.width() >= start_button.sizeHint().width()
            if language == "English":
                assert "Start" in window.cards[0].start_validation_button.text()
                assert "Validation" in window.cards[0].start_validation_button.text()
    window.close(); app.processEvents()
