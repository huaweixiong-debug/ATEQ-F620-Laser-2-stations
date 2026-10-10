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
    # 最后一列（周期号）不拉伸占满页面宽度。
    assert card.table.horizontalHeader().stretchLastSection() is False


def test_staff_change_does_not_trigger_ateq_sync(window, monkeypatch):
    card = window.cards[0]
    calls = []
    monkeypatch.setattr(card, "_product_changed", lambda *a: calls.append(a))
    card.staff.addItem("李四")
    card.staff.setCurrentText("李四")
    assert calls == []
    assert card.ateq_no.text() != "ERR"


def test_model_change_mid_cycle_defers_instead_of_err(window):
    from app.ateq import SerialAteq

    card = window.cards[0]
    card.part_no.setCurrentText(PART)
    card.controller.ateq = SerialAteq("COMX", "A", serial_factory=lambda **kw: None)
    card.controller.phase = Phase.READY
    card._product_changed()
    assert card.model_config is not None
    assert card.ateq_no.text() == "1"      # 型号配置的程序号，不显示 ERR
    assert card._error_key is None


def test_mode_button_defaults_to_dual_and_caption_follows_state(window):
    card = window.cards[0]
    assert card.mode_button.isChecked() is True          # 默认双测
    assert "双测" in card.mode_button.text()
    card.mode_button.setChecked(False)
    assert "单测" in card.mode_button.text()
    card.mode_button.setChecked(True)
    assert "双测" in card.mode_button.text()
    window._apply_language("English")
    assert "Dual Test" in card.mode_button.text()
    window._apply_language("中文")
    assert "双测" in card.mode_button.text()


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
    # 主表格第 0 行：打码列显示 √，周期号列，当日序号列。
    assert card.table.item(0, 7).text() == "√"
    assert card.table.item(0, 9).text() == card.controller.record.cycle_id
    expected_seq = datetime.now().strftime("%Y%m%d") + "A0001"
    assert card.table.item(0, 10).text() == expected_seq
    # 打码时同一序号写入记录（供激光文件第五行使用）。
    assert card.controller.record.daily_sequence == expected_seq
    # 查询页能看到本条记录。
    window.refresh_query()
    table = window.query_tables[window.station]
    assert table.item(0, 7).text() == "√"
    assert table.item(0, 10).text() == expected_seq


def test_daily_sequence_resets_per_day_and_model():
    from datetime import timedelta
    from app.ui_replica import StationPanel

    base = datetime(2026, 10, 8, 1, 0, tzinfo=timezone.utc)
    records = [
        TraceRecord(StationId.A, part_no="M1", cycle_id="c1", created_at=base),
        TraceRecord(StationId.A, part_no="M2", cycle_id="c2",
                    created_at=base + timedelta(minutes=1)),
        TraceRecord(StationId.A, part_no="M1", cycle_id="c3",
                    created_at=base + timedelta(minutes=5)),
        TraceRecord(StationId.A, part_no="M1", cycle_id="c4",
                    created_at=base + timedelta(days=1)),
    ]
    mapping = StationPanel._daily_sequence_map(records)
    assert mapping == {"c1": 1, "c2": 1, "c3": 2, "c4": 1}
    assert StationPanel._daily_sequence_text(records[2], mapping["c3"]) == (
        base.astimezone().strftime("%Y%m%d") + "A0002")


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


def test_start_validation_freezes_button_mode(window):
    card = window.cards[0]
    card.part_no.setCurrentText(PART)
    card.mode_button.setChecked(False)
    window.mark_calibration_due(window.station)
    assert window.start_calibration(window.station) is True
    calibration = window.calibration[window.station]
    assert calibration.test_mode == "single"
    assert card.controller.record.test_mode == "single"
    card.refresh()
    assert not card.mode_button.isEnabled()
    # 验证期间按钮即使被程序改动，refresh 也恢复为冻结模式。
    card.mode_button.setChecked(True)
    card.refresh()
    assert card.mode_button.isChecked() is False


def test_calibration_mode_persisted_and_restored(window):
    card = window.cards[0]
    card.part_no.setCurrentText(PART)
    card.mode_button.setChecked(False)
    window.mark_calibration_due(window.station)
    window.start_calibration(window.station)
    window._persist_calibration()
    calibration = window.calibration[window.station]
    calibration.test_mode = "dual"
    window._restore_calibration()
    assert calibration.test_mode == "single"


def test_calibration_restore_old_snapshot_defaults_dual(window):
    import json

    calibration = window.calibration[window.station]
    calibration.test_mode = "single"
    window._calibration_state_path.write_text(json.dumps({window.station.value: {
        "due": True, "locked": True, "validation_started": True,
        "phase": "WAIT_OK", "ng_count": 1, "ok_count": 0,
        "remaining_seconds": 0.0, "period_seconds": 7200,
        "clear_pending": False, "sample_demand": "OK", "audit_events": [],
    }}), encoding="utf-8")
    window._restore_calibration()
    assert calibration.phase is CalibrationPhase.WAIT_OK
    assert calibration.test_mode == "dual"


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
    # 样件按冻结模式运行：双测样件打码与双测生产一致，守卫只拦截单测打码。
    card, repository = _live_like_window(window, tmp_path)
    card.mode_button.setChecked(False)
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
    assert selection.test_mode == "dual"
    assert selection.product_id == PART
    assert selection.station is StationId.A
    record = card.controller.record
    assert record.sample_cycle is True
    assert record.part_no == PART
    assert record.test_mode == "dual"
    assert card.controller.phase is Phase.READY
    assert write_calls == []
    assert connect_calls == []
    assert isinstance(card.controller.ateq, FakeAteq)


class _PlcSpy:
    def __init__(self):
        self.bits = {}
        self.writes = []

    def read_bit(self, byte, bit):
        return bool(self.bits.get((byte, bit), False))

    def write_bit(self, byte, bit, value):
        self.writes.append((byte, bit, bool(value)))
        self.bits[(byte, bit)] = bool(value)


def _guard_window(window, tmp_path):
    import dataclasses as dc

    card = window.cards[0]
    points = dc.replace(card.point_map, addresses={
        **card.point_map.addresses,
        "pressure_alarm": (110, 6),
        "pressure_trip": (110, 5),
    })
    card.point_map = points
    window.point_map = points
    window.live_mode = True
    window.live_trace_path = tmp_path / "live_trace.log"   # 不污染真实日志
    card._pressure_trip_seconds = 0.0
    card._pressure_window_seconds = 0.4
    card._pressure_on_seconds = 0.0
    return card


def test_positive_hold_guard_trips_and_terminates(window, tmp_path):
    card = _guard_window(window, tmp_path)
    spy = _PlcSpy()
    card.plc = spy
    card.controller.phase = Phase.TEST_2
    spy.bits[(110, 6)] = False            # 压力开关=0 → 异常
    with pytest.raises(RuntimeError, match="压力开关异常"):
        card._positive_hold_guard()
    assert spy.writes == [(110, 5, True), (110, 5, False)]   # M885 脉冲 2 秒
    assert spy.read_bit(110, 5) is False
    trace_text = (tmp_path / "live_trace.log").read_text(encoding="utf-8")
    assert "PRESSURE_SWITCH_ABNORMAL" in trace_text


def test_positive_hold_guard_normal_keeps_running(window, tmp_path):
    card = _guard_window(window, tmp_path)
    spy = _PlcSpy()
    card.plc = spy
    card.controller.phase = Phase.TEST_2
    spy.bits[(110, 6)] = True             # 压力开关=1 → 正常
    card._positive_hold_guard()           # 不抛异常
    assert spy.writes == []
    trace_text = (tmp_path / "live_trace.log").read_text(encoding="utf-8")
    assert "PRESSURE_SWITCH_OK" in trace_text


def test_positive_hold_guard_only_during_second_test(window, tmp_path):
    card = _guard_window(window, tmp_path)
    spy = _PlcSpy()
    card.plc = spy
    card.controller.phase = Phase.TEST_1
    spy.bits[(110, 6)] = False
    card._positive_hold_guard()
    assert spy.writes == []


class _DelayedSwitch:
    """前 N 次读取返回 OFF，之后返回 ON（模拟保压建立过程）。"""

    def __init__(self, off_reads=2):
        self.off_reads = off_reads
        self.reads = 0
        self.writes = []

    def read_bit(self, byte, bit):
        self.reads += 1
        return self.reads > self.off_reads

    def write_bit(self, byte, bit, value):
        self.writes.append((byte, bit, bool(value)))


def test_positive_hold_guard_waits_for_on_duration(window, tmp_path):
    card = _guard_window(window, tmp_path)
    card._pressure_window_seconds = 2.0
    card._pressure_on_seconds = 0.2
    spy = _DelayedSwitch(off_reads=2)
    card.plc = spy
    card.controller.phase = Phase.TEST_2
    card._positive_hold_guard()           # 窗口内 ON 持续达标 → 不抛异常
    assert spy.writes == []
    trace_text = (tmp_path / "live_trace.log").read_text(encoding="utf-8")
    assert "PRESSURE_SWITCH_OK" in trace_text


def test_positive_hold_guard_ng_when_on_never_reaches_required(window, tmp_path):
    card = _guard_window(window, tmp_path)
    card._pressure_window_seconds = 0.5
    card._pressure_on_seconds = 1.0       # 窗口短于 1 秒 → 不可能达标
    spy = _PlcSpy()
    card.plc = spy
    card.controller.phase = Phase.TEST_2
    spy.bits[(110, 6)] = True             # 即使一直 ON 也不足 1 秒 → NG
    with pytest.raises(RuntimeError, match="压力开关异常"):
        card._positive_hold_guard()
    assert spy.writes == [(110, 5, True), (110, 5, False)]


class _ReconnectStub:
    def __init__(self):
        self.connect_calls = 0
        self.writes = False

    def connect(self):
        self.connect_calls += 1

    def enable_writes(self, approved):
        self.writes = bool(approved)


def test_reconnect_plc_after_recovery(window):
    card = window.cards[0]
    stub = _ReconnectStub()
    original = card.plc
    card.plc = stub
    try:
        card.reconnect_plc()
    finally:
        card.plc = original
    assert stub.connect_calls == 1
    assert stub.writes is True


def test_reconnect_plc_failure_is_traced_not_raised(window):
    class _Boom:
        def connect(self):
            raise RuntimeError("no link")

    card = window.cards[0]
    original = card.plc
    card.plc = _Boom()
    try:
        card.reconnect_plc()   # 重连失败不得抛出
    finally:
        card.plc = original


def test_live_clear_laser_start_bit(window):
    window.plc.write_bit(20, 0, True)      # 模拟点位表 laser_start = M20.0
    window._clear_laser_start_bit()
    assert window.plc.read_bit(20, 0) is False


def test_live_clear_laser_start_failure_fails_closed(window):
    window.plc.connected = False
    with pytest.raises(RuntimeError, match="清零打码位"):
        window._clear_laser_start_bit()


def test_pressure_alarm_poll_updates_label(window):
    import dataclasses as dc

    card = window.cards[0]
    points = dc.replace(card.point_map, addresses={
        **card.point_map.addresses, "pressure_alarm": (110, 6)})
    card.point_map = points
    window.point_map = points
    assert window.pressure_alarm_timer.interval() == 2000
    assert card.pressure_alarm_label.isHidden() is True
    # 报警仅在正压 StepCode=5 期间生效
    window._last_live_stepcode = 5
    card.controller.phase = Phase.TEST_2
    window.plc.write_bit(110, 6, False)   # 开关=0 → 异常 → 显示报警
    window._poll_pressure_alarm()
    assert card.pressure_alarm_label.isHidden() is False
    assert "压力开关报警" in card.pressure_alarm_label.text()
    assert card.indicators["pressure"].property("state") == "ng"
    window.plc.write_bit(110, 6, True)    # 开关=1 → 正常 → 隐藏
    window._poll_pressure_alarm()
    assert card.pressure_alarm_label.isHidden() is True
    assert card.indicators["pressure"].property("state") == "ok"


def test_pressure_alarm_hidden_outside_step5(window):
    import dataclasses as dc

    card = window.cards[0]
    points = dc.replace(card.point_map, addresses={
        **card.point_map.addresses, "pressure_alarm": (110, 6)})
    card.point_map = points
    window.point_map = points
    card.controller.phase = Phase.TEST_2
    window.plc.write_bit(110, 6, False)   # 开关异常但不在 step5 → 不显示
    window._last_live_stepcode = 4
    window._poll_pressure_alarm()
    assert card.pressure_alarm_label.isHidden() is True
    window._last_live_stepcode = 5        # 进入 step5 → 显示
    window._poll_pressure_alarm()
    assert card.pressure_alarm_label.isHidden() is False
    card._apply_stepcode_display("6")     # 离开 step5 → 立即熄灭
    assert card.pressure_alarm_label.isHidden() is True


def _reset_window(window, tmp_path):
    import dataclasses as dc

    card = window.cards[0]
    points = dc.replace(card.point_map, addresses={
        **card.point_map.addresses, "start": (0, 0)})
    card.point_map = points
    window.point_map = points
    window.live_mode = True
    window.live_trace_path = tmp_path / "live_trace.log"
    window._last_reset_bit = False
    window._plc_reset_pending = False
    card.controller.recovery_required = True
    card.controller.phase = Phase.FAULT
    card._error_key = "second_error"
    return card


def test_plc_reset_edge_clears_alarm_and_unfinished_cycle(window, tmp_path):
    card = _reset_window(window, tmp_path)
    window.plc.write_bit(0, 0, True)          # PLC 复位上升沿
    window._poll_plc_reset()
    assert card.controller.recovery_required is False
    assert card.controller.phase is Phase.IDLE
    assert card._error_key is None
    trace_text = (tmp_path / "live_trace.log").read_text(encoding="utf-8")
    assert "PLC_RESET_APPLIED" in trace_text


def test_plc_reset_ignores_level_at_startup(window, tmp_path):
    card = _reset_window(window, tmp_path)
    window._last_reset_bit = None
    window.plc.write_bit(0, 0, True)          # 启动时已为高（残留/按住）
    window._poll_plc_reset()
    assert card.controller.recovery_required is True   # 不动作
    assert window._last_reset_bit is True


def test_plc_reset_defers_during_active_test(window, tmp_path):
    card = _reset_window(window, tmp_path)
    card._test_worker_running = True
    window.plc.write_bit(0, 0, True)
    window._poll_plc_reset()
    assert window._plc_reset_pending is True
    assert card.controller.recovery_required is True   # 未立即动状态
    window._apply_plc_reset()                          # worker 结束后落地
    assert card.controller.recovery_required is False
    assert window._plc_reset_pending is False


class _ResettablePlc:
    """断开的 PLC：connect() 前读取抛错，连接后按位读取。"""

    def __init__(self):
        self.connected = False
        self.bits = {}
        self.connect_calls = 0
        self.writes_approved = None

    def connect(self):
        self.connect_calls += 1
        self.connected = True

    def enable_writes(self, approved):
        self.writes_approved = bool(approved)

    def read_bit(self, byte, bit):
        if not self.connected:
            raise RuntimeError("FX PLC 未连接，先调用 connect()")
        return bool(self.bits.get((byte, bit), False))

    def write_bit(self, byte, bit, value):
        self.bits[(byte, bit)] = bool(value)


def test_plc_reset_survives_disconnected_plc(window, tmp_path):
    card = _reset_window(window, tmp_path)
    plc = _ResettablePlc()                 # 故障后适配器处于断开状态
    card.plc = plc
    window._last_reset_bit = False
    window._last_reset_reconnect = 0.0
    window._reset_read_failed_last = False
    window._poll_plc_reset()               # 读失败 → 自动重连
    assert plc.connect_calls >= 1
    assert plc.connected is True
    # 断线期间错过的复位沿：恢复后读到高电平也按复位请求处理
    plc.bits[(0, 0)] = True
    window._poll_plc_reset()
    assert card.controller.recovery_required is False
    assert card.controller.phase is Phase.IDLE
    assert card._error_key is None


def test_plc_reset_continuous_high_does_not_repeat(window, tmp_path):
    card = _reset_window(window, tmp_path)
    spy = _PlcSpy()
    card.plc = spy
    window._last_reset_bit = True          # 已处于高电平（持续按住）
    window._reset_read_failed_last = False
    spy.bits[(0, 0)] = True
    window._poll_plc_reset()
    assert card.controller.recovery_required is True   # 不重复触发


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
