import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import re

from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QGroupBox, QLineEdit, QTableWidget

from app.models import StationId
from app.ui import MainWindow
from app.ui_theme import METRICS, PALETTE, UiTextCatalog


def _window():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.resize(METRICS.canonical_width, METRICS.canonical_height)
    window.show()
    app.processEvents()
    return app, window


def test_canonical_theme_metrics_and_equal_station_geometry():
    assert METRICS.canonical_width == 1920
    assert METRICS.canonical_height == 1080
    assert (METRICS.title_font_size, METRICS.operation_font_size, METRICS.label_font_size) == (18, 15, 13)
    assert (METRICS.input_height, METRICS.primary_button_height, METRICS.table_row_height) == (36, 40, 22)
    assert METRICS.card_radius == 10 and METRICS.control_radius == 6
    assert PALETTE.page.startswith("#") and PALETTE.accent.startswith("#")
    app, window = _window()
    assert abs(window.cards[0].width() - window.cards[1].width()) <= 2
    assert window.cards[0].table.rowHeight(0) == METRICS.table_row_height
    assert window.cards[0].table.horizontalHeader().height() >= 40
    # Sol P2 uses the whole tab work area as denominator.  The station
    # parameter/alert region must remain compact while the list dominates the
    # page and the mirrored cards stay aligned.
    work_height = window.tabs.height() - window.tabs.tabBar().height()
    for card in window.cards:
        assert card.table.geometry().y() <= 135
        assert 0.64 <= card.table.height() / work_height <= 0.70
        assert card.bottom_indicators.height() <= METRICS.footer_max_height
    assert abs(window.cards[0].table.geometry().y() - window.cards[1].table.geometry().y()) <= 2
    assert window.scanner_input.height() <= METRICS.scanner_height + 2
    window.close(); app.processEvents()


def test_explicit_manual_targets_have_readback_and_a_b_isolation():
    app, window = _window()
    window.confirmation_callback = lambda *_: True
    window.username.setText("admin"); window.password.setText("simulate-admin"); window.login()
    for station in ("A", "B"):
        for signal in ("clamp", "transfer", "block", "stamp"):
            assert window.findChild(QPushButton, f"manual_{signal}_{station}_back") is not None
            assert window.findChild(QPushButton, f"manual_{signal}_{station}_forward") is not None
            assert window.findChild(QLabel, f"manual_readback_{signal}_{station}") is not None
        assert window.findChild(QPushButton, f"manual_door_disable_{station}_enable") is not None
        assert window.findChild(QPushButton, f"manual_door_disable_{station}_disable") is not None
        assert window.findChild(QPushButton, f"manual_manual_{station}_automatic") is not None
        assert window.findChild(QPushButton, f"manual_manual_{station}_manual") is not None
    assert window.command_manual_target(StationId.A, "clamp", True)
    assert window.plc.read_bit(4, 4) and not window.plc.read_bit(3, 4)
    assert window.command_manual_target("B", "clamp", True)
    assert window.plc.read_bit(3, 4)
    assert any(token in window.findChild(QLabel, "manual_readback_clamp_A").text() for token in ("ON", "开", "MARCHE"))
    window.close(); app.processEvents()


def test_language_catalog_is_selected_only_and_preserves_manual_data():
    app, window = _window()
    window.product_edit.setText("KEEP-PART")
    window.language_selector.setCurrentText("English")
    for language in UiTextCatalog.LANGUAGES:
        window.language_selector.setCurrentText(language)
        app.processEvents()
        assert tuple(window.tabs.tabBar().tabText(i) for i in range(4)) == UiTextCatalog.tabs(language)
        assert window.product_edit.text() == "KEEP-PART"
    window.close(); app.processEvents()


def test_every_visible_page_string_uses_one_selected_language():
    app, window = _window()
    allowed_cn = {"ATEQ", "OK", "NG", "QR", "Code", "COM", "PLC", "SIMULATE", "A", "B", "F"}
    for language in UiTextCatalog.LANGUAGES:
        window.language_selector.setCurrentText(language)
        app.processEvents()
        for page in range(4):
            window.tabs.setCurrentIndex(page); app.processEvents()
            values = []
            values.extend(x.text() for x in window.findChildren(QLabel) if x.isVisible())
            values.extend(x.text() for x in window.findChildren(QPushButton) if x.isVisible())
            values.extend(x.title() for x in window.findChildren(QGroupBox) if x.isVisible())
            values.extend(x.placeholderText() for x in window.findChildren(QLineEdit) if x.isVisible())
            values.extend(window.tabs.tabBar().tabText(i) for i in range(4))
            for table in window.findChildren(QTableWidget):
                if table.isVisible():
                    values.extend(table.horizontalHeaderItem(i).text() for i in range(table.columnCount()) if table.horizontalHeaderItem(i))
            joined = " ".join(values)
            assert window.windowTitle() == {"中文": "气密检测", "English": "Leak Test 2 Channels", "Français": "Test d'étanchéité à deux voies"}[language]
            if language == "中文":
                words = set(re.findall(r"[A-Za-z]+", joined)) - allowed_cn
                assert not words, (page, words, joined)
                assert not re.search(r"[éèàùçÉÈÀÙÇ]", joined)
            elif language == "English":
                assert not re.search(r"[\u4e00-\u9fff]", joined)
            else:
                assert not re.search(r"[\u4e00-\u9fff]", joined)
                assert "Single" not in joined and "Setup" not in joined and "Query" not in joined
    window.close(); app.processEvents()
