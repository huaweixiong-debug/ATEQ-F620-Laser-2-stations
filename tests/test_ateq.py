"""ATEQ 适配器：正压保压守护用的中止钩子（abort_check）。"""
import pytest

from app.ateq import AteqRequest, SerialAteq


class _StubAteq(SerialAteq):
    """不接串口：read_registers 返回"测试进行中"（StepCode=4）的固定帧。"""

    def __init__(self):
        super().__init__("COMX", "A")
        self.calls = 0

    def read_registers(self, address, count):
        self.calls += 1
        registers = [0] * 13
        registers[4] = 0x0400  # swap16 -> 4（测试进行中）
        return registers, b"STUBFRAME"


def test_abort_check_stops_monitoring():
    ateq = _StubAteq()

    def abort():
        raise RuntimeError("正压保压阶段压力开关异常（M110.6=0），终止测试")

    ateq.abort_check = abort
    request = AteqRequest("A", "A-1", "", 1, "2026-10-08T00:00:00+00:00")
    with pytest.raises(RuntimeError, match="压力开关异常"):
        ateq.run(request)
    assert ateq.calls >= 1


def test_abort_check_is_optional():
    ateq = _StubAteq()
    ateq.abort_check = None
    ateq.cycle_timeout_s = 0.05
    request = AteqRequest("A", "A-1", "", 1, "2026-10-08T00:00:00+00:00")
    with pytest.raises(TimeoutError):
        ateq.run(request)
