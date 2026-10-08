"""UI 离屏走查：单工位、无扫码控件、完整打码周期、样件验证桥接。"""
import time
from datetime import datetime, timezone

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLineEdit

from app.ateq import FakeAteq
from app.calibration import CalibrationPhase
from app.model_settings import ModelConfig, ModelSettingsService, PersonnelService
from app.models import Measurement, Phase, RecoveryRecord, Result, StationId, TraceRecord
from app.repository import PyMySQLRepository

from tests.helpers import make_security

PART = "E118015100"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def window(qapp, tmp_path):
    from app.ui_replica import MainWindow
    w = MainWindow()
    # 型号/人员服务指向临时目录，避免依赖 D:\data。
    security = make_security()
    w.model_settings = ModelSettingsService(security, tmp_path / "日期设置.ini")
    w.model_settings.save(ModelConfig(part_no=PART, customer_no=PART,
                                      date_scheme="YYYYMMDD", ateq_program="1"))
    w.personnel = PersonnelService(security, tmp_path / "作业员列表.txt")
    w.personnel.write_all(["张三"])
    w._refresh_runtime_choices()
    yield w
    w.close()
    w.deleteLater()


def _wait_phase(card, phases, timeout_s=3.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if card.controller.phase in phases:
            return True
        time.sleep(0.02)
    return False


def test_single_station_and_no_scan_widgets(window):
    assert len(window.cards) == 1
    card = window.cards[0]
    assert card.station is window.station is StationId.A
    assert not hasattr(card, "code_input")
    assert not hasattr(card, "scan")
    assert window.findChild(QLineEdit, "scanner_input") is None
    assert hasattr(window, "laser_status")
    assert "激光" in window.laser_status.text() or "SIMULATE" in window.laser_status.text()


def test_full_marking_cycle_via_ui(window):
    card = window.cards[0]
    card.part_no.setCurrentText(PART)
    card.staff.setCurrentText("张三")
    assert window._prepare_stepcode_production_cycle(card) is True
    assert card.controller.phase is Phase.READY
    assert card.controller.record.sample_cycle is False
    assert card.controller.record.part_no == PART
    assert card.controller.record.person == "张三"
    card.controller.test_first()
    card.controller.test_second()
    assert card.controller.phase is Phase.MARKING
    card.mark()
    assert card.controller.phase is Phase.COMPLETE
    assert card.controller.record.marked is True
    card.refresh()
    # 主表格第 0 行：打码列显示 √，末列为周期号。
    assert card.table.item(0, 7).text() == "√"
    assert card.table.item(0, 9).text() == card.controller.record.cycle_id
    # 查询页能看到本条记录。
    window.refresh_query()
    table = window.query_tables[window.station]
    assert table.item(0, 7).text() == "√"


def _release_calibration(window):
    """模拟校准倒计时运行中（非到期、未锁定），允许生产周期启动。"""
    cal = window.calibration[window.station]
    cal.due = cal.locked = False
    cal.remaining_seconds = float(cal.period_seconds)


def test_stepcode_edge_opens_next_cycle_after_complete(window):
    card = window.cards[0]
    _release_calibration(window)
    card.part_no.setCurrentText(PART)
    assert window._prepare_stepcode_production_cycle(card) is True
    first_cycle = card.controller.record.cycle_id
    card.controller.test_first()
    card.controller.test_second()
    card.mark()
    assert card.controller.phase is Phase.COMPLETE
    # 下一个 StepCode=4 上升沿：归档完成周期并冻结新周期，随后异步一测启动。
    window._handle_live_stepcode(window.station, 4)
    assert card.controller.record.cycle_id != first_cycle
    assert _wait_phase(card, (Phase.TEST_1, Phase.WAIT_2, Phase.FAULT))


def test_stepcode_edge_ignores_stale_level(window):
    card = window.cards[0]
    _release_calibration(window)
    window._handle_live_stepcode(window.station, 4)
    assert _wait_phase(card, (Phase.TEST_1, Phase.WAIT_2, Phase.FAULT))
    # 同一电平重复上报不派发新动作。
    window._handle_live_stepcode(window.station, 4)
    assert card.controller.phase is not Phase.READY


def _save_model_date_scheme(window, date_scheme):
    window.model_settings.save(ModelConfig(part_no=PART, customer_no=PART,
                                           date_scheme=date_scheme, ateq_program="1"))
    window._refresh_runtime_choices()
    window.cards[0].part_no.setCurrentText(PART)


def test_production_date_scheme_falls_back_to_station_setting(window):
    import dataclasses as dc

    card = window.cards[0]
    _save_model_date_scheme(window, "")
    window.settings = dc.replace(window.settings, laser_date_scheme="YYMMDD")
    assert window._prepare_stepcode_production_cycle(card) is True
    assert card.controller.record.date_scheme == "YYMMDD"


def test_production_date_scheme_falls_back_to_default(window):
    import dataclasses as dc

    card = window.cards[0]
    _save_model_date_scheme(window, "")
    window.settings = dc.replace(window.settings, laser_date_scheme="   ")
    assert window._prepare_stepcode_production_cycle(card) is True
    assert card.controller.record.date_scheme == "YYYYMMDD"


def test_production_date_scheme_keeps_unknown_nonblank(window):
    card = window.cards[0]
    _save_model_date_scheme(window, "年方案9")
    assert window._prepare_stepcode_production_cycle(card) is True
    assert card.controller.record.date_scheme == "年方案9"


def test_calibration_date_scheme_uses_same_resolver(window):
    import dataclasses as dc

    _save_model_date_scheme(window, "")
    window.settings = dc.replace(window.settings, laser_date_scheme="YYMMDD")
    assert window.start_calibration(window.station) is True
    assert window.cards[0].controller.record.date_scheme == "YYMMDD"


def test_calibration_ng_ok_validation(window):
    card = window.cards[0]
    card.part_no.setCurrentText(PART)
    card.staff.setCurrentText("张三")
    window.start_calibration(window.station)
    calibration = window.calibration[window.station]
    assert calibration.phase is CalibrationPhase.WAIT_NG
    assert card.controller.record.sample_cycle is True
    # NG 样件：单测 NG → 完成 → 记录验证结果。
    card.controller.ateq.result = Result.NG
    card.controller.test_first()
    assert card.controller.phase is Phase.COMPLETE
    window.on_calibration_sample("NG", window.station)
    assert calibration.phase is CalibrationPhase.WAIT_OK
    # OK 样件：桥接复位 NG 周期并开 OK 样件周期。
    assert window._begin_ok_validation_cycle(card) is True
    assert card.controller.record.sample_cycle is True
    card.controller.ateq.result = Result.OK
    card.controller.test_first()
    # 样件默认不打码：单测 OK 直接完成。
    assert card.controller.phase is Phase.COMPLETE
    window.on_calibration_sample("OK", window.station)
    # 现场规则：OK 验证完成即清灯并重启倒计时（无需扫码确认）。
    assert calibration.phase is CalibrationPhase.COMPLETE
    assert calibration.clear_pending is False
    assert calibration.remaining_seconds == calibration.period_seconds
    # 工位已释放，可开新的生产周期；三灯保持熄灭。
    assert window._prepare_stepcode_production_cycle(card) is True
    assert calibration.indicators == (False, False, False)


def _live_like_window(window, tmp_path):
    repository = PyMySQLRepository(host="127.0.0.1", user="u", password="p",
                                   database="test")
    card = window.cards[0]
    window.live_mode = True
    window.live_trace_path = tmp_path / "live_trace.log"
    card.controller.repository = repository
    card.repository = repository
    window.repository = repository
    return card, repository


def test_live_single_mode_production_blocked(window, tmp_path):
    card, repository = _live_like_window(window, tmp_path)
    card.mode_button.setChecked(False)
    calls = {"start": [], "select": [], "run": [], "write": [], "refresh": 0}
    card.controller.start_cycle = lambda selection, **kw: calls["start"].append((selection, kw))
    card.controller.ateq.select_program = lambda program: calls["select"].append(program)
    card.controller.ateq.run = lambda request: calls["run"].append(request)
    card.plc.write_bit = lambda byte, bit, value: calls["write"].append((byte, bit, value))
    original_refresh = card.refresh

    def refresh():
        calls["refresh"] += 1
        original_refresh()

    card.refresh = refresh
    assert window._prepare_stepcode_production_cycle(card) is False
    assert calls["start"] == [] and calls["select"] == [] and calls["run"] == []
    assert calls["write"] == []
    assert calls["refresh"] >= 1
    assert card._error_key == "single_mode_unsupported"
    assert card.controller.phase is Phase.IDLE
    assert card.controller.record is None
    trace = window.live_trace_path.read_text(encoding="utf-8")
    assert "PRODUCTION_CYCLE_BLOCKED" in trace
    assert "reason=single_mode_unsupported" in trace


def test_live_dual_mode_production_still_starts(window, tmp_path):
    card, repository = _live_like_window(window, tmp_path)
    card.mode_button.setChecked(True)
    captured = []
    original_start = card.controller.start_cycle

    def start_cycle(selection, **kwargs):
        captured.append((selection, kwargs))
        return original_start(selection, **kwargs)

    card.controller.start_cycle = start_cycle
    assert window._prepare_stepcode_production_cycle(card) is True
    assert len(captured) == 1
    selection, _ = captured[0]
    assert selection.test_mode == "dual"
    assert card.controller.phase is Phase.READY


def test_simulate_single_mode_production_still_starts(window):
    card = window.cards[0]
    card.mode_button.setChecked(False)
    captured = []
    original_start = card.controller.start_cycle

    def start_cycle(selection, **kwargs):
        captured.append((selection, kwargs))
        return original_start(selection, **kwargs)

    card.controller.start_cycle = start_cycle
    assert window._prepare_stepcode_production_cycle(card) is True
    assert captured[0][0].test_mode == "single"
    assert card.controller.phase is Phase.READY


def test_live_calibration_site_guard_blocks_mark_samples(window, tmp_path):
    card, repository = _live_like_window(window, tmp_path)
    card.mode_button.setChecked(True)
    card.controller.mark_samples = True
    called = []
    card.controller.start_cycle = lambda selection, **kw: called.append(selection)
    with pytest.raises(RuntimeError, match="Dual Test"):
        window.start_calibration(window.station)
    calibration = window.calibration[window.station]
    assert calibration.validation_started is False
    assert called == []


def test_single_mode_unsupported_catalog_all_languages():
    from app.ui_theme import UiTextCatalog

    for language in UiTextCatalog.LANGUAGES:
        text = UiTextCatalog.message(language, "single_mode_unsupported")
        assert text
        assert "single_mode_unsupported" not in text
        assert "Dual Test" in text


def test_live_default_calibration_sample_still_starts(window, tmp_path, monkeypatch):
    card, repository = _live_like_window(window, tmp_path)
    assert card.controller.mark_samples is False
    card.mode_button.setChecked(True)
    card.part_no.setCurrentText(PART)
    card.staff.setCurrentText("张三")
    card.refresh()
    assert card.start_validation_button.isEnabled()
    start_calls = []
    original_start = card.controller.start_cycle

    def start_cycle(selection, **kwargs):
        start_calls.append((selection, kwargs))
        return original_start(selection, **kwargs)

    card.controller.start_cycle = start_cycle
    write_calls = []
    card.plc.write_bit = lambda byte, bit, value: write_calls.append((byte, bit, value))
    connect_calls = []
    monkeypatch.setattr(PyMySQLRepository, "_connect",
                        lambda self: connect_calls.append(True))
    card._indicator_action("start_validation")
    calibration = window.calibration[window.station]
    assert card._error_key is None
    assert calibration.validation_started is True
    assert calibration.phase is CalibrationPhase.WAIT_NG
    assert len(start_calls) == 1
    selection, kwargs = start_calls[0]
    assert kwargs.get("sample") is True
    assert selection.test_mode == "single"
    assert selection.product_id == PART
    assert selection.station is StationId.A
    record = card.controller.record
    assert record.sample_cycle is True
    assert record.part_no == PART
    assert record.test_mode == "single"
    assert card.controller.phase is Phase.READY
    assert write_calls == []
    assert connect_calls == []
    assert isinstance(card.controller.ateq, FakeAteq)


def test_pressure_alarm_poll_updates_label(window):
    import dataclasses as dc

    card = window.cards[0]
    points = dc.replace(card.point_map, addresses={
        **card.point_map.addresses, "pressure_alarm": (110, 6)})
    card.point_map = points
    window.point_map = points
    assert window.pressure_alarm_timer.interval() == 2000
    assert card.pressure_alarm_label.isHidden() is True
    window.plc.write_bit(110, 6, True)
    window._poll_pressure_alarm()
    assert card.pressure_alarm_label.isHidden() is False
    assert "压力开关报警" in card.pressure_alarm_label.text()
    assert card.indicators["pressure"].property("state") == "ng"
    window.plc.write_bit(110, 6, False)
    window._poll_pressure_alarm()
    assert card.pressure_alarm_label.isHidden() is True
    assert card.indicators["pressure"].property("state") == "ok"


def test_journal_dir_uses_env_override(window, tmp_path):
    assert window.journal_dir == tmp_path / "journal"


def test_pending_journal_starts_in_recovery(qapp, tmp_path):
    from app.journal import CycleJournal
    from app.ui_replica import MainWindow

    journal_path = tmp_path / "journal" / f"{StationId.A.value}.json"
    record = TraceRecord(station=StationId.A, part_no=PART, person="张三",
                         cycle_id="A-20261001120000-abcdef",
                         created_at=datetime.now(timezone.utc))
    record.first = Measurement(1.0, 0.1, Result.OK, b"F", "kPa", "ml/min")
    state = RecoveryRecord(StationId.A, record.cycle_id, Phase.READY, record)
    CycleJournal(journal_path).write_record(state)
    w = MainWindow()
    try:
        card = w.cards[0]
        assert card.controller.phase is Phase.FAULT
        assert card.controller.recovery_required is True
    finally:
        w.close()
        w.deleteLater()


def test_live_mainwindow_requires_preflight_token(qapp, tmp_path, monkeypatch):
    import app.ui_replica as ui_replica

    opened = []

    class BombAteq:
        def __init__(self, *args, **kwargs):
            opened.append(True)
            raise AssertionError("device opened without preflight token")

    monkeypatch.setattr(ui_replica, "SerialAteq", BombAteq)
    with pytest.raises(RuntimeError, match="LIVE_BLOCKED"):
        ui_replica.MainWindow(live=True, config_path=tmp_path / "missing.toml")
    assert opened == []


def test_manual_output_permission_and_point_map(window, monkeypatch):
    # UI 实例的 security 默认 operator：手动输出被拒绝。
    with pytest.raises(PermissionError):
        window.command_manual_target(window.station, "clamp", True)
    # 管理员登录后：确认对话框改为自动确认，写入并回读（sim 点位 clamp=M4.4）。
    assert window.security.login("admin", "simulate-admin") is True
    monkeypatch.setattr(window, "confirmation_callback", lambda *a, **k: True)
    assert window.command_manual_target(window.station, "clamp", True) is True
    assert window.plc.read_bit(4, 4) is True
    with pytest.raises(Exception):
        window.point_map.address("scan_ok")  # 扫码点位已不存在
