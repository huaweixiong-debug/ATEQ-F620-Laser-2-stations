import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from app.models import StationId
from app.ui import MainWindow


def _window():
    app = QApplication.instance() or QApplication([])
    window = MainWindow(); window.resize(1920, 1080); window.show(); app.processEvents()
    return app, window


def test_error_ack_lifecycle_all_languages_both_stations():
    app, window = _window()
    a, b = window.cards
    QTest.mouseClick(a.reprint, Qt.MouseButton.LeftButton)
    QTest.mouseClick(b.reprint, Qt.MouseButton.LeftButton)
    app.processEvents()
    for language in ("中文", "English", "Français"):
        window.language_selector.setCurrentText(language); app.processEvents()
        for card in window.cards:
            assert card.error_ack.isVisible()
            assert card.error_ack.objectName() in ("error_ack_A", "error_ack_B")
            assert card.next_action.text()
            assert card.error_reset.isVisible()
    QTest.mouseClick(a.error_ack, Qt.MouseButton.LeftButton); app.processEvents()
    assert not a.error_ack.isVisible() and b.error_ack.isVisible()
    QTest.mouseClick(b.error_reset, Qt.MouseButton.LeftButton); app.processEvents()
    assert not b.error_reset.isVisible() and not b.error_summary.isVisible()
    window.close(); app.processEvents()


def test_footer_labels_and_buttons_nonoverlap_at_canonical_size_all_languages():
    app, window = _window()
    for language in ("中文", "English", "Français"):
        window.language_selector.setCurrentText(language); app.processEvents()
        for card in window.cards:
            for signal, label in card.indicator_labels.items():
                name = f"start_{card.station.value}" if signal == "start_validation" else f"{signal}_button_{card.station.value}"
                button = window.findChild(QPushButton, name)
                assert label.height() >= label.sizeHint().height()
                assert not label.geometry().intersects(button.geometry())
    window.close(); app.processEvents()


def test_qtest_visible_reprint_failure_and_ack_isolated_per_station():
    app, window = _window()
    a, b = window.cards
    for language in ("中文", "English", "Français"):
        window.language_selector.setCurrentText(language); app.processEvents()
        before = (dict(window.plc._bits), len(window.repository.records), a.controller.phase, b.controller.phase)
        QTest.mouseClick(a.reprint, Qt.MouseButton.LeftButton); app.processEvents()
        assert a.error_ack.isVisible() and not b.error_ack.isVisible()
        expected = {"中文": "重打拒绝：操作失败", "English": "Reprint denied: Operation failed", "Français": "Réimpression refusée : Opération échouée"}[language]
        assert a.error_summary.text() == expected
        a.refresh(); assert a.error_summary.text() == expected
        window.language_selector.setCurrentText(language); assert a.error_summary.text() == expected
        QTest.mouseClick(a.error_ack, Qt.MouseButton.LeftButton); app.processEvents()
        assert not a.error_ack.isVisible() and not a.error_summary.isVisible() and not b.error_ack.isVisible()
        assert before == (dict(window.plc._bits), len(window.repository.records), a.controller.phase, b.controller.phase)
    window.close(); app.processEvents()
