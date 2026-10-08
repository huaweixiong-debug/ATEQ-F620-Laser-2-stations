import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton, QTableWidget, QWidget
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from app.models import StationId
from app.ui import MainWindow


def _window():
    app = QApplication.instance() or QApplication([])
    window = MainWindow(); window.show(); app.processEvents()
    return app, window


def test_replica_tabs_and_main_station_structure():
    app, window = _window()
    assert [window.tabs.tabText(i) for i in range(4)] == ["测试", "设置", "查询", "手动"]
    assert [window.tabs.tabBar().tabText(i) for i in range(4)] == ["测试/Main/Principale", "设置/Setup/Coup Monté", "查询/Query/Requête", "手动/Manual/Manuelle"]
    for station in ("A", "B"):
        assert window.findChild(QPushButton, f"single_dual_{station}") is not None
        for name in ("main_code", "total_today", "ok_today", "ateq_no", "part_no", "staff", "scanner_indicator", "reprint", "watchdog", "next_action"):
            assert window.findChild(QWidget, f"{name}_{station}") is not None
        table = window.findChild(QTableWidget, f"{station}List")
        assert table.rowCount() == 30 and table.columnCount() == 10
        assert table.horizontalHeaderItem(2).text() == "QR Code"
    window.close(); app.processEvents()


def test_replica_setup_query_manual_bindings_and_language():
    app, window = _window()
    setup = window.findChild(QTableWidget, "setupParameterTable")
    assert setup.rowCount() == 40 and setup.columnCount() == 4
    window.language_selector.setCurrentText("English")
    assert "English" in window.settings_status.text()

    # A and B query filters are independent and both tables have Main.vi columns.
    assert window.findChild(QTableWidget, "query_table_A").rowCount() == 30
    assert window.findChild(QTableWidget, "query_table_B").rowCount() == 30
    window.query_fields[(StationId.A, "code")].setText("ONLY-A")
    assert window.query_fields[(StationId.B, "code")].text() == ""

    # Manual screenshot controls are mirrored, permission gated and isolated.
    window.username.setText("admin"); window.password.setText("simulate-admin"); window.login()
    window.confirmation_callback = lambda *_args: True
    a_clamp = window.findChild(QPushButton, "manual_clamp_A")
    b_clamp = window.findChild(QPushButton, "manual_clamp_B")
    QTest.mouseClick(a_clamp, Qt.MouseButton.LeftButton)
    assert window.plc.read_bit(4, 4) and not window.plc.read_bit(3, 4)
    QTest.mouseClick(b_clamp, Qt.MouseButton.LeftButton)
    assert window.plc.read_bit(3, 4)
    window.close(); app.processEvents()
