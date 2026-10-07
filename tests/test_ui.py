"""UI 离屏走查：单工位、无扫码控件、完整打码周期、样件验证桥接。"""
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLineEdit

from app.ateq import FakeAteq
from app.calibration import CalibrationPhase
from app.model_settings import ModelConfig, ModelSettingsService, PersonnelService
from app.models import Phase, Result, StationId

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


def test_tc26_stepcode_dedup_and_exact_stage_dispatch(window):
    card = window.cards[0]
    _release_calibration(window)
    card.part_no.setCurrentText(PART)
    calls = []
    card.first = lambda: calls.append("first")
    card.second = lambda: calls.append("second")
    assert window._prepare_stepcode_production_cycle(card) is True
    assert card.controller.phase is Phase.READY
    window._handle_live_stepcode(window.station, 4)
    assert calls == ["first"]
    # Repeated 4 edge (same level) must not dispatch again.
    window._handle_live_stepcode(window.station, 4)
    assert calls == ["first"]
    # Non-4 step code is ignored but re-arms the edge detector.
    window._handle_live_stepcode(window.station, 3)
    assert calls == ["first"]
    # Next 4 edge dispatches the second test for a WAIT_2 cycle.
    card.controller.phase = Phase.WAIT_2
    window._handle_live_stepcode(window.station, 4)
    assert calls == ["first", "second"]
    window._handle_live_stepcode(window.station, 4)
    assert calls == ["first", "second"]


def _save_date_scheme(window, scheme="YYMMDD"):
    window.model_settings.save(ModelConfig(part_no=PART, customer_no=PART,
                                           date_scheme=scheme, ateq_program="1"))


def test_tc27_prepare_stepcode_cycle_freezes_date_scheme(window):
    card = window.cards[0]
    _save_date_scheme(window)
    card.part_no.setCurrentText(PART)
    assert window._prepare_stepcode_production_cycle(card) is True
    assert card.controller.record.date_scheme == "YYMMDD"
    assert card.controller.record.sample_cycle is False


def test_tc27_restore_pending_calibration_freezes_date_scheme(window):
    card = window.cards[0]
    _save_date_scheme(window)
    window.calibration[window.station].begin_validation()
    assert window._restore_pending_calibration_cycle(card) is True
    assert card.controller.record.date_scheme == "YYMMDD"
    assert card.controller.record.sample_cycle is True


def test_tc27_begin_ok_validation_cycle_freezes_date_scheme(window):
    card = window.cards[0]
    _save_date_scheme(window)
    calibration = window.calibration[window.station]
    calibration.begin_validation()
    calibration.sample("NG")
    assert window._begin_ok_validation_cycle(card) is True
    assert card.controller.record.date_scheme == "YYMMDD"
    assert card.controller.record.sample_cycle is True


def test_tc27_start_calibration_freezes_date_scheme(window):
    card = window.cards[0]
    _save_date_scheme(window)
    card.part_no.setCurrentText(PART)
    card.staff.setCurrentText("张三")
    assert window.start_calibration(window.station) is True
    assert card.controller.record.date_scheme == "YYMMDD"
    assert card.controller.record.sample_cycle is True


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
