import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QEvent, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from app.models import Phase, StationId
from app.ui import MainWindow


def _trace_lines(window, path):
    if path.exists():
        return path.read_text(encoding="utf-8").splitlines()
    return []


def test_b_start_validation_click_reaches_handler_and_traces(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    trace_path = tmp_path / "trace.log"
    window.live_trace_path = trace_path
    window.b_live = True
    try:
        card = window.cards[1]
        assert card.start_validation_button.isEnabled()
        card.start_validation_button.click()
        app.processEvents()
        assert window.calibration[StationId.B].validation_started
        log = "\n".join(_trace_lines(window, trace_path))
        assert "B CAL_BUTTON_PRESSED enabled=True" in log
        assert "CAL_START_REQUEST station=B" in log
    finally:
        window.b_live = False
        window.close()


def test_click_on_disabled_start_button_is_traced(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    trace_path = tmp_path / "trace.log"
    window.live_trace_path = trace_path
    window.b_live = True
    try:
        card = window.cards[1]
        button = card.start_validation_button
        card.controller.phase = Phase.READY  # non-terminal phase forces can_start False
        card.refresh()
        app.processEvents()
        assert not button.isEnabled()
        # Offscreen layout can place the footer outside the card bounds, so
        # drive the filter logic directly instead of relying on hit-testing.
        center = QPointF(button.rect().center())
        press = QMouseEvent(QEvent.Type.MouseButtonPress, center, center,
                            center, Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        original_child_at = card.childAt
        card.childAt = lambda pos: button
        try:
            card.eventFilter(card, press)
        finally:
            card.childAt = original_child_at
        app.processEvents()
        log = "\n".join(_trace_lines(window, trace_path))
        assert "B CAL_BUTTON_HIT_DISABLED" in log
    finally:
        window.b_live = False
        window.close()


def test_b_trigger_file_cannot_dispatch_a_test(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    window.b_live = True
    try:
        card = window.cards[1]
        calls = []
        card.first = lambda: calls.append("first")
        card.second = lambda: calls.append("second")
        trigger = tmp_path / "b_test_trigger.txt"
        window.b_trigger_path = trigger
        card.controller.phase = Phase.READY

        trigger.write_text("start", encoding="utf-8")
        window._poll_b_trigger_file()
        assert calls == []
        assert not trigger.exists()

        card.controller.phase = Phase.WAIT_2
        trigger.write_text("start", encoding="utf-8")
        window._poll_b_trigger_file()
        assert calls == []

        trigger.write_text("junk", encoding="utf-8")
        window._poll_b_trigger_file()
        assert calls == []
        assert not trigger.exists()

        window._poll_b_trigger_file()  # no file -> no action
        assert calls == []
    finally:
        window.b_live = False
        window.close()


def _write_qr_data_dir(data):
    data.mkdir(parents=True, exist_ok=True)
    (data / "E113015200-B.btw").write_bytes(b"B")
    (data / "序列号B.txt").write_text("0001", encoding="utf-8")
    serial = str(data / "序列号B.txt").replace("\\", "/")
    template = str(data / "E113015200-B.btw").replace("\\", "/")
    (data / "日期设置.ini").write_text(
        "[E113015200]\n"
        "条码规则=客户型号+日期+工位号+流水号\n"
        "客户型号=E113015400\n"
        "日期=年方案2+月方案1+日方案2\n"
        "工位号A=8\n工位号B=9\n"
        f"流水号A={serial}\n流水号B={serial}\n"
        "ATEQ程序号=7\n"
        f"打印模板={template}\n",
        encoding="utf-8")
    (data / "日期对照.ini").write_text(
        "[年方案2]\n年=25,26,27,28,29,30\n"
        "[月方案1]\n月=01,02,03,04,05,06,07,08,09,10,11,12\n"
        "[日方案2]\n日=01,02,03,04,05,06,07,08,09,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31\n",
        encoding="utf-8")


def test_ok_validation_bridge_creates_cycle_after_ng(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    trace_path = tmp_path / "trace.log"
    window.live_trace_path = trace_path
    window.b_live = True
    try:
        card = window.cards[1]
        data = tmp_path / "data"
        _write_qr_data_dir(data)
        window.data_dir = data
        card.set_choices(["E113015200"], ["Operator"])
        app.processEvents()
        if card.payload is None:
            card.payload = window._payload_for(StationId.B, "E113015200")
        calibration = window.calibration[StationId.B]
        window.start_calibration(StationId.B)
        window.calibration_sample("NG", StationId.B)
        assert calibration.phase is not None
        # 现场流程中 NG 测试完成后控制器停在“完成”；离线测试直接置相位。
        card.controller.phase = Phase.COMPLETE
        # 桥接：等待OK样件 + 控制器完成 -> 归档 NG 周期并建立 OK 周期
        assert window._begin_ok_validation_cycle(card)
        assert card.controller.phase is Phase.READY
        assert card.controller.record is not None
        log = "\n".join(_trace_lines(window, trace_path))
        assert "CAL_OK_CYCLE_READY station=B" in log
    finally:
        window.b_live = False
        window.close()
