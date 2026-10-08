import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTime
from PySide6.QtWidgets import QApplication

from app.models import Phase, StationId
from app.ui import MainWindow


def test_ui_starts_due_then_requires_start_validation_and_ng_ok_before_scan():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        card = window.cards[0]
        calibration = window.calibration[StationId.A]
        assert calibration.due and calibration.locked
        assert card.start_validation_button.isEnabled()
        assert "校准到期" in card.calibration_notice.text()
        assert card.indicators["calibration_due"].property("state") == "ng"
        assert not card.scan("START-BEFORE-CALIBRATION")
        assert card.controller.phase is Phase.IDLE

        assert window.start_calibration(StationId.A)
        assert calibration.validation_started and calibration.locked
        assert "NG" in card.calibration_notice.text()
        window.calibration_sample("NG", StationId.A)
        assert "cal-NG-A" in window.printer.calibration_intents
        assert calibration.indicators == (True, True, False)
        assert "OK" in card.calibration_notice.text()
        window.calibration_sample("OK", StationId.A)
        assert "cal-OK-A" in window.printer.calibration_intents
        # 现场规则：验证完成后立即清灯并启动倒计时，不再等待扫码。
        assert not calibration.locked and not calibration.clear_pending
        assert not calibration.due
        assert calibration.indicators == (False, False, False)
        assert calibration.remaining_seconds == calibration.period_seconds
    finally:
        window.close()
        app.processEvents()


def test_calibration_period_is_applied_as_independent_seconds_and_expires(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        window.global_settings.path = tmp_path / "global.ini"
        window.cal_period.setTime(QTime(2, 0, 0))
        window.username.setText("admin")
        window.password.setText("simulate-admin")
        window.login()
        window._save_global_settings()
        assert all(item.period_seconds == 2 * 60 * 60 for item in window.calibration.values())

        station = StationId.B
        calibration = window.calibration[station]
        card = window.cards[1]
        window.start_calibration(station)
        window.calibration_sample("NG", station)
        window.calibration_sample("OK", station)
        assert card.scan("NORMAL-B-AFTER-CALIBRATION")
        assert calibration.remaining_seconds == 2 * 60 * 60
        assert window.calibration_countdown_b.value() == 2 * 60 * 60
        assert window.calibration_countdown_b.text() == "02:00"

        assert not calibration.tick(2 * 60 * 60 - 1)
        assert not calibration.due
        assert calibration.tick(1)
        card.refresh()
        assert calibration.due and calibration.locked
        assert card.indicators["calibration_due"].property("state") == "ng"
        assert not window.calibration[StationId.A].due or window.calibration[StationId.A].remaining_seconds >= 0
    finally:
        window.close()
        app.processEvents()
