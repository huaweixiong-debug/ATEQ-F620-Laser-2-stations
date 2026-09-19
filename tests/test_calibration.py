"""NG/OK 样件验证与时效倒计时：与 Morocco 项目行为一致。"""
from datetime import datetime, timezone

import pytest

from app.calibration import Calibration, CalibrationPhase
from app.models import StationId


def make_cal() -> Calibration:
    return Calibration(required_samples=1, station=StationId.A,
                       initial_due=True, period_seconds=2 * 60 * 60)


def test_initial_due_and_lock():
    cal = make_cal()
    assert cal.due and cal.locked
    assert cal.phase is CalibrationPhase.WAIT_NG
    assert cal.sample_demand == "NG"


def test_ng_then_ok_sequence():
    cal = make_cal()
    cal.begin_validation()
    assert cal.phase is CalibrationPhase.WAIT_NG
    assert cal.sample("NG") is CalibrationPhase.WAIT_OK
    assert cal.sample_demand == "OK"
    assert cal.sample("OK") is CalibrationPhase.COMPLETE
    assert cal.clear_pending is True
    assert cal.ok_count == 1 and cal.ng_count == 1


def test_wrong_order_rejected():
    cal = make_cal()
    cal.begin_validation()
    with pytest.raises(ValueError, match="顺序错误"):
        cal.sample("OK")
    cal.sample("NG")
    with pytest.raises(ValueError, match="顺序错误"):
        cal.sample("NG")


def test_begin_validation_requires_due():
    cal = Calibration(required_samples=1, period_seconds=3600)
    assert not cal.due
    with pytest.raises(RuntimeError, match="校准周期"):
        cal.begin_validation()


def test_clear_after_resume_restarts_countdown():
    cal = make_cal()
    cal.begin_validation()
    cal.sample("NG")
    cal.sample("OK")
    cal.clear_after_resume()
    assert not cal.due and not cal.locked
    assert cal.remaining_seconds == cal.period_seconds
    assert cal.indicators == (False, False, False)


def test_tick_expiry_raises_due():
    cal = Calibration(required_samples=1, period_seconds=3600)
    # 倒计时只在验证完成后（clear_after_resume）启动，初始为 0 不计时。
    assert cal.tick(elapsed_seconds=3600) is False
    cal.mark_due()
    cal.begin_validation()
    cal.sample("NG"); cal.sample("OK"); cal.clear_after_resume()
    assert cal.tick(elapsed_seconds=60) is False
    assert cal.tick(elapsed_seconds=3600) is True
    assert cal.due and cal.locked


def test_admin_cancel_resets_period():
    cal = make_cal()
    cal.begin_validation()
    cal.sample("NG")
    with pytest.raises(PermissionError):
        cal.cancel("operator", "试一下")
    with pytest.raises(PermissionError):
        cal.cancel("admin", "  ")
    cal.cancel("admin", "换型暂停")
    assert not cal.due and not cal.locked
    assert cal.phase is CalibrationPhase.COMPLETE
    assert cal.audit_events and cal.audit_events[-1]["reason"] == "换型暂停"


def test_indicators_visibility():
    cal = make_cal()
    cal.begin_validation()
    due, ng, ok = cal.indicators
    assert (due, ng, ok) == (True, False, False)
    cal.sample("NG")
    assert cal.indicators == (True, True, False)
    cal.sample("OK")
    # 验证完成后三灯保持，等待下一个生产周期清除。
    assert cal.indicators == (True, True, True)
    cal.clear_after_resume()
    assert cal.indicators == (False, False, False)
