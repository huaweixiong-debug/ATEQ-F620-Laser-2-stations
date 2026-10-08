"""ATEQ 适配器：正压保压判定钩子（step5_check）在 StepCode=5 时触发一次。"""
import pytest

from app.ateq import AteqRequest, SerialAteq
from app.models import Result


class _StubAteq(SerialAteq):
    """不接串口：按脚本依次返回 StepCode；状态位恒为 OK。"""

    def __init__(self, steps):
        super().__init__("COMX", "A")
        self._steps = list(steps)

    def read_registers(self, address, count):
        step = self._steps.pop(0) if len(self._steps) > 1 else self._steps[0]
        registers = [0] * 13
        registers[3] = self._swap16(0x0001)   # 结果 OK
        registers[4] = self._swap16(step)
        return registers, b"STUBFRAME"


def _request():
    return AteqRequest("A", "A-1", "", 1, "2026-10-08T00:00:00+00:00")


def test_step5_check_fires_once_and_can_abort():
    ateq = _StubAteq([4, 5, 5, 5, 6, 65535])
    calls = []

    def check():
        calls.append(True)
        raise RuntimeError("正压保压阶段压力开关异常（M110.6=0），终止测试")

    ateq.step5_check = check
    with pytest.raises(RuntimeError, match="压力开关异常"):
        ateq.run(_request())
    assert len(calls) == 1


def test_step5_check_runs_once_and_normal_run_completes():
    ateq = _StubAteq([4, 5, 5, 5, 6, 65535])
    calls = []
    ateq.step5_check = lambda: calls.append(True)
    response = ateq.run(_request())
    assert len(calls) == 1
    assert response.measurement.result is Result.OK


def test_step5_check_not_called_without_step5():
    ateq = _StubAteq([4, 6, 65535])
    calls = []
    ateq.step5_check = lambda: calls.append(True)
    response = ateq.run(_request())
    assert calls == []
    assert response.measurement.result is Result.OK
