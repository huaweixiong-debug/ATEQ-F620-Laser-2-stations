import os
import json
from types import SimpleNamespace
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QInputDialog, QPushButton, QTableWidget, QWidget
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from app.models import Phase, StationId
from app.models import Measurement, Result, TraceRecord
from app.calibration import CalibrationPhase
from app.ui import MainWindow


def _window():
    app = QApplication.instance() or QApplication([])
    window = MainWindow(); window.show(); app.processEvents()
    return app, window


class _ScannerCommandSpy:
    def __init__(self):
        self.commands = []
        self.last_error = ""

    def set_scan_enabled(self, enabled):
        self.commands.append(bool(enabled))
        return True

    def connected(self):
        return True

    def read_code(self, timeout=None):
        return None

    def close(self):
        pass


def test_confirmed_print_reenables_shared_scanner_with_lon():
    app, window = _window()
    window.shared_scanner.close()
    scanner_spy = _ScannerCommandSpy()
    window.shared_scanner = scanner_spy
    window._scan_enabled = False
    assert window._enable_scanner_after_print(StationId.B, "cal-NG-B") is True
    assert window._scan_enabled is True
    assert scanner_spy.commands == [True]
    assert "关闭" in window.scanner_toggle.text()
    window.close(); app.processEvents()


def test_replica_tabs_and_main_station_structure():
    app, window = _window()
    assert [window.tabs.tabText(i) for i in range(4)] == ["测试", "设置", "查询", "手动"]
    assert window.findChild(QWidget, "scannerStatus") is not None
    assert window.findChild(QWidget, "shared_scanner_indicator") is not None
    assert window.findChild(QPushButton, "scanner_toggle") is not None
    phases = tuple(card.controller.phase for card in window.cards)
    toggle = window.findChild(QPushButton, "scanner_toggle")
    window.shared_scanner.close()
    scanner_spy = _ScannerCommandSpy()
    window.shared_scanner = scanner_spy
    QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
    assert window._scan_enabled is False and "开启" in toggle.text()
    assert scanner_spy.commands == [False]
    assert tuple(card.controller.phase for card in window.cards) == phases
    QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
    assert window._scan_enabled is True and "关闭" in toggle.text()
    assert scanner_spy.commands == [False, True]
    assert [window.tabs.tabBar().tabText(i) for i in range(4)] == ["测试/Main/Principale", "设置/Setup/Coup Monté", "查询/Query/Requête", "手动/Manual/Manuelle"]
    for station in ("A", "B"):
        assert window.findChild(QPushButton, f"single_dual_{station}") is not None
        assert window.findChild(QWidget, f"stepCodeFrame_{station}") is not None
        stepcode = window.findChild(QWidget, f"stepCodeValue_{station}")
        assert stepcode is not None
        for name in ("main_code", "total_today", "ok_today", "ateq_no", "part_no", "staff", "reprint", "next_action"):
            assert window.findChild(QWidget, f"{name}_{station}") is not None
        card = window.cards[0 if station == "A" else 1]
        assert card.total_today.width() <= 80
        assert card.ok_today.width() <= 80
        assert card.ateq_no.width() <= 70
        assert card.code_input.width() > card.total_today.width()
        assert window.findChild(QWidget, f"scanner_indicator_{station}") is None
        assert window.findChild(QWidget, f"watchdog_{station}") is None
        table = window.findChild(QTableWidget, f"{station}List")
        assert table.rowCount() == 30 and table.columnCount() == 10
        assert table.horizontalHeaderItem(2).text() == "QR Code"
    window.cards[1].set_stepcode(6)
    app.processEvents()
    assert window.findChild(QWidget, "stepCodeValue_B").text() == "6"
    window.close(); app.processEvents()


def test_stepcode_four_edges_dispatch_single_and_dual_stages():
    app, window = _window()
    card = window.cards[1]
    card.controller.scan("B-DUAL", "PART", "OP", test_mode="dual")
    calls = []

    def first():
        calls.append("first")
        card.controller.phase = Phase.WAIT_2

    def second():
        calls.append("second")
        card.controller.phase = Phase.COMPLETE

    card.first = first
    card.second = second
    window._handle_b_stepcode(4)
    window._handle_b_stepcode(4)  # same level is not a second test
    window._handle_b_stepcode(5)
    window._handle_b_stepcode(6)
    window._handle_b_stepcode(65525)
    window._handle_b_stepcode(4)  # distinct second test cycle
    assert calls == ["first", "second"]
    assert card.stepcode_value.text() == "4"
    window.close(); app.processEvents()


def test_stepcode_four_restores_pending_calibration_after_restart():
    app, window = _window()
    card = window.cards[1]
    calibration = window.calibration[StationId.B]
    calibration.validation_started = True
    calibration.phase = CalibrationPhase.WAIT_NG
    card.controller.phase = Phase.IDLE
    card.controller.record = None
    card.payload = SimpleNamespace(product_id="PART-B")
    cal_payload = SimpleNamespace(
        product_id="PART-B", serial_no="C001", barcode_text="CAL-QR-B",
        customer_model="CUSTOMER-B", ateq_program=1,
        template_path="Cal_NG_B.btw")
    window._barcode_engine = lambda: SimpleNamespace(
        generate_calibration=lambda product_id, station: cal_payload)
    calls = []
    card.first = lambda: calls.append("first")

    window._handle_b_stepcode(4)

    assert calls == ["first"]
    assert card.controller.phase is Phase.READY
    assert card.controller.record.test_mode == "single"
    assert card.controller.record.serial_no == "C001"
    window.close(); app.processEvents()


def test_stepcode_four_reserves_normal_cycle_without_pretest_scan():
    app, window = _window()
    card = window.cards[1]
    calibration = window.calibration[StationId.B]
    calibration.due = False
    calibration.locked = False
    calibration.validation_started = False
    card.payload = SimpleNamespace(
        product_id="PART-B", serial_no="0007", barcode_text="QR-B-0007",
        customer_model="CUSTOMER-B", ateq_program=1,
        template_path="PART-B-B.btw")
    next_payload = SimpleNamespace(
        product_id="PART-B", serial_no="0008", barcode_text="QR-B-0008",
        customer_model="CUSTOMER-B", ateq_program=1,
        template_path="PART-B-B.btw")
    window._advance_serial = lambda station, product, **kwargs: None
    window._payload_for = lambda station, product: next_payload
    card.mode_button.setChecked(False)
    calls = []
    card.first = lambda: calls.append("first")

    window._handle_b_stepcode(4)

    assert calls == ["first"]
    assert card.controller.phase is Phase.READY
    assert card.controller.record.test_mode == "single"
    assert card.controller.record.code_2d == "QR-B-0007"
    assert card.payload.barcode_text == "QR-B-0008"
    window.close(); app.processEvents()


def test_normal_printed_label_scan_acknowledges_and_releases_station():
    app, window = _window()
    card = window.cards[1]
    window.cards[0].payload = None
    calibration = window.calibration[StationId.B]
    calibration.due = False
    calibration.locked = False
    card.controller.scan("CAL-QR-B", "PART-B", "Operator", test_mode="single")
    card.controller.phase = Phase.LABELING
    card._label_ack_pending = True

    window.route_shared_scanner_code("CAL-QR-B")

    assert card.controller.phase is Phase.IDLE
    assert card.controller.record is None
    byte, bit = __import__("app.plc", fromlist=["POINTS"]).POINTS["scan_ok"][StationId.B]
    assert card.plc.read_bit(byte, bit)
    window.close(); app.processEvents()


def test_initial_cycle_scan_does_not_pulse_plc_scan_ok():
    app, window = _window()
    card = window.cards[1]
    window.cards[0].payload = None
    calibration = window.calibration[StationId.B]
    calibration.due = False
    calibration.locked = False

    assert card.scan("INITIAL-CODE-B", "PART-B") is True
    byte, bit = __import__("app.plc", fromlist=["POINTS"]).POINTS["scan_ok"][StationId.B]
    assert card.plc.read_bit(byte, bit) is False
    window.close(); app.processEvents()


def test_live_initial_scan_defers_serial_reservation_to_stepcode():
    app, window = _window()
    card = window.cards[1]
    window.cards[0].payload = None
    window.live_mode = True
    calibration = window.calibration[StationId.B]
    calibration.due = False
    calibration.locked = False
    calibration.validation_started = False
    card.payload = SimpleNamespace(
        product_id="PART-B", serial_no="0007", barcode_text="QR-LIVE-0007",
        customer_model="CUSTOMER-B", ateq_program=1,
        template_path="PART-B-B.btw")
    card.controller.scan_selection = lambda selection: None
    calls = []
    window._advance_serial = lambda *args, **kwargs: calls.append((args, kwargs))

    assert card.scan("QR-LIVE-0007", "PART-B") is True
    assert calls == []
    window.close(); app.processEvents()


def test_stepcode_skips_serial_already_committed_before_restart():
    app, window = _window()
    card = window.cards[1]
    old_payload = SimpleNamespace(
        product_id="PART-B", serial_no="0005", barcode_text="QR-0005",
        customer_model="CUSTOMER-B", ateq_program=1,
        template_path="PART-B-B.btw")
    next_payload = SimpleNamespace(
        product_id="PART-B", serial_no="0006", barcode_text="QR-0006",
        customer_model="CUSTOMER-B", ateq_program=1,
        template_path="PART-B-B.btw")
    card.payload = old_payload
    window.repository.records["old-cycle"] = TraceRecord(
        StationId.B, "0005", "QR-0005", "PART-B", "Operator")
    window._payload_for = lambda station, product: next_payload

    resolved = window._next_unused_production_payload(card)

    assert resolved.serial_no == "0006"
    assert card.payload.serial_no == "0006"
    window.close(); app.processEvents()


def test_wrong_post_print_codes_are_ignored_until_exact_label():
    app, window = _window()
    card = window.cards[1]
    window.cards[0].payload = None
    calibration = window.calibration[StationId.B]
    calibration.due = False
    calibration.locked = False
    card.controller.scan("PRINTED-LABEL-B", "PART-B", "Operator", test_mode="single")
    card.controller.phase = Phase.LABELING
    card._label_ack_pending = True

    window.route_shared_scanner_code("UNRELATED-CODE")
    assert card._label_ack_pending is True
    assert card.controller.phase is Phase.LABELING
    assert card._error_key is None
    assert "继续扫码" in window.scanner_status.text()

    # A repeated irrelevant frame is also non-terminal and must not turn into
    # the old duplicate-scan rejection.
    window.route_shared_scanner_code("UNRELATED-CODE")
    assert card._label_ack_pending is True
    assert card._error_key is None

    window.route_shared_scanner_code("PRINTED-LABEL-B")
    assert card._label_ack_pending is False
    assert card.controller.phase is Phase.IDLE
    window.close(); app.processEvents()


def test_reset_cancels_post_print_label_scan_wait():
    app, window = _window()
    card = window.cards[1]
    window.cards[0].payload = None
    calibration = window.calibration[StationId.B]
    calibration.due = False
    calibration.locked = False
    card.controller.scan("PRINTED-LABEL-B", "PART-B", "Operator", test_mode="single")
    # A successful print leaves the controller in COMPLETE while the UI-only
    # acknowledgement latch waits for the physical label scan.
    card.controller.phase = Phase.COMPLETE
    card._label_ack_pending = True

    card.reset()

    assert card._label_ack_pending is False
    assert card.controller.phase is Phase.IDLE
    assert card.controller.record is None
    window.close(); app.processEvents()


def test_reset_resolves_live_recovery_fault_directly_without_admin_login():
    app, window = _window()
    card = window.cards[1]
    card.controller.scan("FAULT-QR-B", "PART-B", "Operator", test_mode="single")
    card.controller._safe_fault(RuntimeError("ATEQ sample missed"), "第一次测试失败")
    assert card.controller.recovery_required is True

    card.reset()

    assert card.controller.phase is Phase.IDLE
    assert card.controller.record is None
    assert card.controller.recovery_required is False
    assert card._error_key is None
    window.close(); app.processEvents()


def test_reset_retries_ateq_program_and_clears_blocked_display():
    app, window = _window()
    card = window.cards[1]
    card.ateq_no.setText("BLO")

    card.reset()

    assert card.ateq_no.text() != "BLO"
    assert card.ateq_no.text() != "BLOCKED"
    window.close(); app.processEvents()


def test_normal_single_ok_auto_prints_label_after_test():
    app, window = _window()
    card = window.cards[1]
    calibration = window.calibration[StationId.B]
    calibration.due = False
    calibration.locked = False
    calibration.validation_started = False
    card.controller.scan("QR-B-AUTO", "PART-B", "Operator", test_mode="single")
    card.controller.record.first = Measurement(200.0, 1.0, Result.OK, b"normal-ok")
    card.controller.phase = Phase.LABELING
    calls = []

    def fake_label():
        calls.append(card.controller.record.cycle_id)
        card.controller.phase = Phase.COMPLETE
        return True

    card.controller.label = fake_label
    window._enable_scanner_after_print = lambda *_args: True
    card._finish_test()

    assert calls == [card.controller.record.cycle_id]
    assert card._label_ack_pending is True
    assert card.controller.phase is Phase.COMPLETE
    window.close(); app.processEvents()


def test_calibration_labels_do_not_wait_for_scan_and_ok_releases_station():
    app, window = _window()
    card = window.cards[1]
    calibration = window.calibration[StationId.B]
    calibration.due = True
    calibration.locked = True
    calibration.validation_started = True
    calibration.phase = CalibrationPhase.WAIT_NG
    calibration.sample_demand = "NG"
    card.payload = SimpleNamespace(product_id="PART-B")
    card.controller.scan("CAL-QR-B-NG", "PART-B", "Operator", test_mode="single")
    card.controller.phase = Phase.COMPLETE

    generated = iter((
        SimpleNamespace(
            product_id="PART-B", serial_no="C002", barcode_text="CAL-QR-B-OK",
            customer_model="CUSTOMER-B", ateq_program=1,
            template_path="Cal_OK_B.btw"),
    ))
    window._barcode_engine = lambda: SimpleNamespace(
        generate_calibration=lambda *_args: next(generated),
        advance_calibration_serial=lambda *_args: "C003")
    print_results = []
    window.printer.print_calibration = lambda _station, result, **_kwargs: (
        print_results.append(result) or SimpleNamespace(
            accepted=True, job_id=f"cal-{result}-B"))
    scanner_reenable = []
    window._enable_scanner_after_print = lambda station, job_id="": (
        scanner_reenable.append((station, job_id)) or True)

    window.calibration_sample("NG", StationId.B)
    assert calibration.phase is CalibrationPhase.WAIT_OK
    assert card.controller.phase is Phase.COMPLETE
    assert card._label_ack_pending is False
    byte, bit = __import__("app.plc", fromlist=["POINTS"]).POINTS["scan_ok"][StationId.B]
    assert card.scan("CAL-QR-B-NG") is not True
    assert not card.plc.read_bit(byte, bit)

    assert window._begin_ok_validation_cycle(card)
    assert card.controller.phase is Phase.READY
    card.controller.phase = Phase.LABELING  # single-test OK result is ready to print
    window.calibration_sample("OK", StationId.B)

    assert print_results == ["NG", "OK"]
    assert len(scanner_reenable) == 2  # retain the existing post-print LON behavior
    assert card._label_ack_pending is False
    assert card.controller.phase is Phase.IDLE
    assert card.controller.record is None
    assert calibration.due is False
    assert calibration.validation_started is False
    window.close(); app.processEvents()


def test_admin_calibration_cancel_buttons_restart_independent_timers(tmp_path, monkeypatch):
    app, window = _window()
    window._calibration_state_path = tmp_path / "calibration-state.json"
    window._calibration_signature = None
    buttons = {}
    for station, card in zip(StationId, window.cards):
        calibration = window.calibration[station]
        calibration.due = True
        calibration.locked = True
        calibration.validation_started = False
        calibration.phase = CalibrationPhase.WAIT_NG
        calibration.sample_demand = "NG"
        calibration.remaining_seconds = 0
        button = window.findChild(QPushButton, f"cancel_calibration_{station.value}")
        assert button is not None
        buttons[station] = button
        card.refresh()
        assert not button.isVisible()

    window.username.setText("admin")
    window.password.setText("simulate-admin")
    window.login()
    app.processEvents()
    assert all(button.isVisible() and button.isEnabled() for button in buttons.values())

    reasons = iter(("cancel B calibration", "cancel A calibration"))
    monkeypatch.setattr(QInputDialog, "getText",
                        lambda *_args, **_kwargs: (next(reasons), True))
    QTest.mouseClick(buttons[StationId.B], Qt.MouseButton.LeftButton)
    assert window.calibration[StationId.B].due is False
    assert window.calibration[StationId.B].remaining_seconds == window.calibration[StationId.B].period_seconds
    assert window.calibration[StationId.A].due is True
    assert not buttons[StationId.B].isEnabled()

    QTest.mouseClick(buttons[StationId.A], Qt.MouseButton.LeftButton)
    assert all(not window.calibration[s].due for s in StationId)
    state = json.loads(window._calibration_state_path.read_text(encoding="utf-8"))
    assert state["A"]["audit_events"][-1]["reason"] == "cancel A calibration"
    assert state["B"]["audit_events"][-1]["reason"] == "cancel B calibration"
    assert state["A"]["audit_events"][-1]["station"] == "A"
    assert state["B"]["audit_events"][-1]["station"] == "B"
    window.close(); app.processEvents()


def test_replica_setup_query_manual_bindings_and_language():
    app, window = _window()
    setup = window.findChild(QTableWidget, "setupParameterTable")
    assert setup.rowCount() == 40 and setup.columnCount() == 9
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
