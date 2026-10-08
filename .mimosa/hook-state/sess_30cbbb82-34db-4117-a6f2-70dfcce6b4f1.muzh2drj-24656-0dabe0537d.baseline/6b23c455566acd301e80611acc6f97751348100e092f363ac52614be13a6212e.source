import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QFrame
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from app.ui import MainWindow
from app.models import StationId

def test_simulate_ui_has_four_functional_tabs():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show(); app.processEvents()
    assert window.centralWidget().count() == 4
    assert [window.centralWidget().tabText(i) for i in range(4)] == ["测试", "设置", "查询", "手动"]
    window.close(); app.processEvents()

def test_visible_scanner_calibration_and_settings_commit_flows(tmp_path):
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    # Seed one model into a temporary ini so the model table has a row to select.
    ini = tmp_path / "日期设置.ini"
    ini.write_text("[TEST-MODEL]\n条码规则 = 客户型号+日期+工位号+流水号\n客户型号 = CUST1\n打印路径A = D:\\data\\TEST-MODEL-A.btw\n打印路径B = D:\\data\\TEST-MODEL-B.btw\nATEQ程序号A = 1\nATEQ程序号B = 1\n日期 = 年方案2+月方案1+日方案2\n", encoding="utf-8")
    window.model_settings.path = ini
    window._refresh_models()
    # Operator cannot select the current model; the check is reverted.
    window.tabs.setCurrentIndex(1)
    window.setup_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    assert window.product_settings.current_product() == "SIM-PART"
    # Authenticated admin selects the model through the table checkbox.
    window.username.setText("admin"); window.password.setText("simulate-admin"); window.login()
    window.setup_table.item(0, 0).setCheckState(Qt.CheckState.Checked)
    assert window.product_settings.current_product() == "TEST-MODEL"
    # New installations start locked: the visible Start Validation button
    # must be used before a production scan is accepted.
    QTest.mouseClick(window.findChild(QPushButton, "start_A"), Qt.MouseButton.LeftButton)
    window.on_calibration_sample("NG", StationId.A)
    assert "等待OK" in window.calibration_status.text()
    window.on_calibration_sample("OK", StationId.A)
    assert "校准完成" in window.calibration_status.text()
    # Visible scanner control routes through framer and guard.
    window.scanner_input.setText("QR-UI-001")
    scanner = next(button for button in window.tabs.widget(0).findChildren(QPushButton) if "扫码到 A" in button.text())
    QTest.mouseClick(scanner, Qt.MouseButton.LeftButton)
    assert window.cards[0].controller.record.part_no == "TEST-MODEL"
    window.cards[0].controller._safe_fault(RuntimeError("ui recovery"), "测试故障")
    window.recovery_reason.setText("UI audited recovery")
    recover = next(button for button in window.tabs.widget(3).findChildren(QPushButton) if "归档工位 A" in button.text())
    QTest.mouseClick(recover, Qt.MouseButton.LeftButton)
    assert window.cards[0].controller.phase.name == "IDLE" and "已审计归档" in window.recovery_status.text()
    window.close(); app.processEvents()

def test_mainvi_controls_are_visible_and_a_b_readback_is_independent():
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.confirmation_callback = lambda *_args: True
    window.show(); app.processEvents()
    indicators = {"scan_ok", "calibration_due", "ng_sample", "ok_sample", "door_disable", "manual", "pressure"}
    actuators = {"manual", "transfer", "block", "clamp", "stamp", "pressure", "start"}
    for station in ("A", "B"):
        assert window.findChild(QFrame, f"stationCard_{station}") is not None
        for signal in indicators:
            assert window.findChild(QLabel, f"{signal}_{station}") is not None
        for signal in actuators:
            assert window.findChild(QPushButton, f"manual_{signal}_{station}") is not None
            assert window.findChild(QPushButton, f"dashboard_{signal}_{station}") is not None
        assert window.findChild(QPushButton, f"start_{station}") is not None
    assert window.findChild(QLabel, "systemStatusBar").text().startswith("模式：SIMULATE")

    # Operator cannot write a manual output; authenticated admin can, and
    # the other station remains unchanged (the same M-point map is used by UI).
    a_transfer = window.findChild(QPushButton, "manual_transfer_A")
    b_transfer = window.findChild(QPushButton, "manual_transfer_B")
    QTest.mouseClick(a_transfer, Qt.MouseButton.LeftButton)
    assert not window.plc.read_bit(4, 0)
    window.username.setText("admin"); window.password.setText("simulate-admin"); window.login()
    QTest.mouseClick(a_transfer, Qt.MouseButton.LeftButton)
    assert window.plc.read_bit(4, 0)
    assert not window.plc.read_bit(3, 0)
    QTest.mouseClick(b_transfer, Qt.MouseButton.LeftButton)
    assert window.plc.read_bit(3, 0)
    a_start = window.findChild(QPushButton, "manual_start_A")
    QTest.mouseClick(a_start, Qt.MouseButton.LeftButton)
    assert window.plc.read_bit(16, 0) and not window.plc.read_bit(16, 1)
    assert window.findChild(QPushButton, "start_A").isVisible()
    assert not window.findChild(QLabel, "next_action_A").isVisible()
    window.close(); app.processEvents()

def test_dangerous_output_cancel_then_confirm_and_scanner_failure_propagates():
    app = QApplication.instance() or QApplication([])
    window = MainWindow(); window.show(); app.processEvents()
    window.username.setText("admin"); window.password.setText("simulate-admin"); window.login()
    calls = []
    window.confirmation_callback = lambda station, signal, requested, current: calls.append((station.value, signal, requested, current)) or False
    clamp = window.findChild(QPushButton, "manual_clamp_A")
    QTest.mouseClick(clamp, Qt.MouseButton.LeftButton)
    assert not window.plc.read_bit(4, 4)
    assert calls[-1] == ("A", "clamp", True, False)
    window.confirmation_callback = lambda *_args: True
    QTest.mouseClick(clamp, Qt.MouseButton.LeftButton)
    assert window.plc.read_bit(4, 4)

    card = window.cards[0]; card.code.setText("KEEP-CODE")
    card.controller._safe_fault(RuntimeError("forced UI fault"), "测试故障")
    window.scanner_input.setText("FAULT-CODE")
    route = window.findChild(QPushButton, "scanner_route_A")
    QTest.mouseClick(route, Qt.MouseButton.LeftButton)
    assert "scan OK" not in window.scanner_status.text()
    assert "错误" in window.scanner_status.text()
    assert card.controller.record is None and card.code.text() == "KEEP-CODE"
    window.close(); app.processEvents()

def test_next_action_is_hidden_from_calibration_footer():
    app = QApplication.instance() or QApplication([])
    window = MainWindow(); window.resize(1366, 768); window.show(); app.processEvents()
    for station in ("A", "B"):
        prompt = window.findChild(QLabel, f"next_action_{station}")
        assert not prompt.isVisible()
    window.close(); app.processEvents()
