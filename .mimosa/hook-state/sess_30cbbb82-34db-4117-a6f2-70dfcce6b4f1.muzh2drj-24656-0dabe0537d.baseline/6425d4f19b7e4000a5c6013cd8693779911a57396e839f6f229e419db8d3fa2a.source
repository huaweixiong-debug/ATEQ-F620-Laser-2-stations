import os
from itertools import combinations

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from app.models import Phase, StationId
from app.ui import MainWindow


LANGUAGE_ERRORS = {
    "中文": "重打拒绝：操作失败",
    "English": "Reprint denied: Operation failed",
    "Français": "Réimpression refusée : Opération échouée",
}


def _shown_window(width: int, height: int, language: str):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(language)
    window.resize(width, height)
    window.show()
    app.processEvents()
    return app, window


def _window_rect(widget, window) -> QRect:
    return QRect(widget.mapTo(window, QPoint(0, 0)), widget.size())


def _station_footer_widgets(window, card):
    leds = [card.indicators[signal] for signal in ("calibration_due", "start_validation", "ng_sample", "ok_sample")]
    labels = list(card.indicator_labels.values())
    actions = [
        window.findChild(QPushButton, f"{signal}_button_{card.station.value}")
        if signal != "start_validation"
        else window.findChild(QPushButton, f"start_{card.station.value}")
        for signal in ("calibration_due", "start_validation", "ng_sample", "ok_sample")
    ]
    return leds, labels, actions, [card.next_action, card.error_summary, card.error_ack, card.error_reset]


def test_simultaneous_reprint_error_footer_is_disjoint_at_both_sizes_and_languages():
    """Real A+B reprint failures keep every visible localized footer item readable."""
    for width, height in ((1366, 768), (1920, 1080)):
        for language, expected in LANGUAGE_ERRORS.items():
            app, window = _shown_window(width, height, language)
            try:
                for card in window.cards:
                    QTest.mouseClick(card.reprint, Qt.MouseButton.LeftButton)
                app.processEvents()
                for card in window.cards:
                    leds, labels, actions, diagnostics = _station_footer_widgets(window, card)
                    assert card.error_summary.isVisible()
                    assert card.error_summary.text() == expected
                    visible = [widget for widget in (*leds, *labels, *actions, *diagnostics) if widget is not None and widget.isVisible()]
                    assert all(widget.text().strip() for widget in visible)
                    for widget in visible:
                        assert widget.width() >= widget.sizeHint().width()
                    rectangles = [_window_rect(widget, window) for widget in visible]
                    assert all(not left.intersects(right) for left, right in combinations(rectangles, 2))
            finally:
                window.close()
                app.processEvents()


def _lifecycle_snapshot(window):
    repository = window.repository
    journals = tuple(
        (card.station.value, card.controller.journal.path.read_bytes() if card.controller.journal.path.exists() else b"")
        for card in window.cards
    )
    records = tuple(sorted((key, value.to_dict()) for key, value in repository.records.items()))
    controllers = tuple(
        (
            card.controller.phase,
            card.controller.record.cycle_id if card.controller.record else None,
            card.controller.record.station if card.controller.record else None,
            card.controller.record.code_2d if card.controller.record else None,
        )
        for card in window.cards
    )
    return (
        tuple(sorted(window.plc._bits.items())),
        tuple(sorted(repository.records)),
        records,
        controllers,
        frozenset(window.printer.intents),
        journals,
        tuple((getattr(window.plc, "last_safe_stop", None), window.plc.outputs_energized()) for _ in (0,)),
    )


def _operator_scan(window, station, code):
    window.scanner_input.setText(code)
    QTest.mouseClick(window.findChild(QPushButton, f"scanner_route_{station.value}"), Qt.MouseButton.LeftButton)
    QApplication.processEvents()


def _simulate_device_progression_to_labeling(card, code):
    """Fixture: feed simulated scanner/ATEQ/service events directly to a controller."""
    controller = card.controller
    controller.scan(code, card.product_provider() if hasattr(card, "product_provider") else "", "")
    controller.test_first()
    controller.test_second()
    card.refresh()
    assert controller.phase is Phase.LABELING


def _login_as_admin(app, window):
    window.tabs.setCurrentIndex(3)
    app.processEvents()
    window.username.setText("admin")
    window.password.setText("simulate-admin")
    QTest.mouseClick(window.findChild(QPushButton, "login_button"), Qt.MouseButton.LeftButton)
    app.processEvents()
    window.tabs.setCurrentIndex(0)
    app.processEvents()


@pytest.mark.parametrize("target_index", [0, 1], ids=["station-A", "station-B"])
def test_qtest_ack_and_reset_are_station_local_and_ack_is_ui_only(target_index):
    """Actual visible Acknowledge/Reset controls preserve service invariants."""
    app, window = _shown_window(1920, 1080, "English")
    try:
        target, other = window.cards[target_index], window.cards[1 - target_index]
        QTest.mouseClick(target.reprint, Qt.MouseButton.LeftButton)
        QTest.mouseClick(other.reprint, Qt.MouseButton.LeftButton)
        app.processEvents()
        before_ack = _lifecycle_snapshot(window)
        QTest.mouseClick(target.error_ack, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert _lifecycle_snapshot(window) == before_ack
        assert not target.error_ack.isVisible() and other.error_ack.isVisible()

        # Recreate only this station's visible error, then invoke its reset.
        QTest.mouseClick(target.reprint, Qt.MouseButton.LeftButton)
        app.processEvents()
        before_reset = _lifecycle_snapshot(window)
        other_controller = (other.controller.phase, other.controller.record)
        QTest.mouseClick(target.error_reset, Qt.MouseButton.LeftButton)
        app.processEvents()
        after_reset = _lifecycle_snapshot(window)
        assert target.controller.phase is Phase.IDLE
        assert target.controller.record is None
        assert not target.error_reset.isVisible() and not target.error_summary.isVisible()
        assert other_controller == (other.controller.phase, other.controller.record)
        assert after_reset[1:5] == before_reset[1:5]
        assert window.plc.outputs_energized() is False
        assert window.plc.last_safe_stop == f"UI reset {target.station.value}"
    finally:
        window.close()
        app.processEvents()


@pytest.mark.parametrize("target_index", [0, 1], ids=["station-A", "station-B"])
def test_qtest_reprint_ui_clears_simulated_device_labeling_error(target_index):
    """QTest drives authentic reprint/login UI after simulated device setup."""
    for language, expected in LANGUAGE_ERRORS.items():
        app, window = _shown_window(1920, 1080, language)
        try:
            target = window.cards[target_index]
            other = window.cards[1 - target_index]
            _simulate_device_progression_to_labeling(target, f"SIM-{target.station.value}-{language}")

            # Operator is unauthorized: this creates the active visible error.
            QTest.mouseClick(target.reprint, Qt.MouseButton.LeftButton)
            app.processEvents()
            assert target.error_summary.text() == expected
            assert target.error_ack.isVisible() and target.error_reset.isVisible()
            assert other.error_ack.isVisible() is False

            _login_as_admin(app, window)
            QTest.mouseClick(target.reprint, Qt.MouseButton.LeftButton)
            app.processEvents()
            assert target.controller.phase is Phase.COMPLETE
            assert not target.error_summary.isVisible()
            assert not target.error_ack.isVisible() and not target.error_reset.isVisible()
            assert target.controller.record is not None
            assert target.controller.record.labeled
        finally:
            window.close()
            app.processEvents()


def test_qtest_visible_scanner_next_operation_clears_existing_error_all_languages():
    """A successful scanner operation clears its station's active UI error."""
    for language in LANGUAGE_ERRORS:
        app, window = _shown_window(1366, 768, language)
        try:
            card = window.cards[0]
            QTest.mouseClick(card.reprint, Qt.MouseButton.LeftButton)
            app.processEvents()
            assert card.error_summary.isVisible()
            _operator_scan(window, card.station, f"NEXT-{language}")
            assert card.controller.phase is Phase.READY
            assert not card.error_summary.isVisible()
            assert not card.error_ack.isVisible() and not card.error_reset.isVisible()
        finally:
            window.close()
            app.processEvents()


def test_main_page_has_no_test_stage_controls_and_unauthenticated_ui_cannot_print():
    """Approved Main hierarchy excludes test-stage widgets and unauthenticated print side effects."""
    app, window = _shown_window(1920, 1080, "English")
    try:
        production_names = {widget.objectName() for widget in window.findChildren(QWidget)}
        assert not any(name and ("test1" in name.lower() or "test2" in name.lower() or name.lower().startswith("label_")) for name in production_names)
        assert window.findChild(QPushButton, "test_first_A") is None
        assert window.findChild(QPushButton, "test_second_A") is None
        assert window.findChild(QPushButton, "label_A") is None

        for card in window.cards:
            _operator_scan(window, card.station, f"UNAUTH-PRINT-{card.station.value}")
            QTest.mouseClick(card.reprint, Qt.MouseButton.LeftButton)
        app.processEvents()
        assert not window.printer.intents
        assert not window.repository.records
        assert not any(record.labeled for record in window.repository.records.values())
        assert all(card.controller.print_job_id == "" and card.controller.print_state.name == "NONE" for card in window.cards)
    finally:
        window.close()
        app.processEvents()
