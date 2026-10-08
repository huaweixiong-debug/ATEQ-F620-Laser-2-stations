"""Regression tests: ATEQ value capture rules and label time format.

采集规则（现场确认 2026-09-12）：
- 压力 = StepCode=6 结束时的寄存器值（不是 65525 结束帧的值）。
- 泄漏 = StepCode 从 6 变为 65525 之后的结束帧寄存器值。
- 时间记录 = 年月日-HH:MM:SS。
"""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from datetime import datetime

from app.ateq import AteqRequest, SerialAteq, modbus_crc16
from app.barprint import _measurement_line
from app.models import Measurement, Result


def _swap(value):
    return ((value & 0xFF) << 8) | ((value >> 8) & 0xFF)


def _enc32(value32):
    """Encode a 32-bit value as the two register words the parser expects."""
    return (_swap(value32 & 0xFFFF), _swap((value32 >> 16) & 0xFFFF))


def _registers(*, step, status, fifo, pressure_raw, leak_raw, p_unit, l_unit):
    p_lo, p_hi = _enc32(pressure_raw)
    l_lo, l_hi = _enc32(leak_raw)
    pu_lo, pu_hi = _enc32(p_unit)
    lu_lo, lu_hi = _enc32(l_unit)
    return [
        0x0000,           # r0 program
        _swap(fifo),      # r1 FIFO (parser applies _swap16)
        0x0000,           # r2
        _swap(status),    # r3 status (parser applies _swap16)
        _swap(step),      # r4 step (parser applies _swap16)
        p_lo, p_hi,       # r5/r6
        pu_lo, pu_hi,     # r7/r8
        l_lo, l_hi,       # r9/r10
        lu_lo, lu_hi,     # r11/r12
    ]


class FrameSerial:
    """Fake serial that replays raw Modbus frames built from register sets."""

    def __init__(self, frames):
        self.frames = frames
        self.writes = 0

    @staticmethod
    def is_open():
        return True

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.writes += 1

    def flush(self):
        pass

    def read(self, count):
        frame = self.frames[min(self.writes - 1, len(self.frames) - 1)]
        return frame[:count]


def _build_frame(registers, slave=1):
    payload = bytes([slave, 0x03, len(registers) * 2]) + b"".join(
        value.to_bytes(2, "big") for value in registers)
    return payload + modbus_crc16(payload).to_bytes(2, "little")


def test_pressure_from_step6_and_leak_from_terminal_frame():
    # 压力: step-6 期间 0x0000C350 (=50000 -> 50.000)，结束时仍是该值；
    # 65525 帧压力寄存器清零，泄漏寄存器给出最终值 0x00000BB8 (=3000 -> 3.000)。
    step6 = _registers(step=6, status=0x0000, fifo=0x0007,
                       pressure_raw=0x0000C350, leak_raw=0x00000000,
                       p_unit=11000, l_unit=12000)
    terminal = _registers(step=65525, status=0x0026, fifo=0x0008,
                          pressure_raw=0x00000000, leak_raw=0x00000BB8,
                          p_unit=11000, l_unit=12000)
    frames = [_build_frame(step6), _build_frame(step6), _build_frame(step6), _build_frame(terminal)]
    ateq = SerialAteq("COMX", "B", slave=1, cycle_timeout_s=5,
                      serial_factory=lambda **kwargs: FrameSerial(frames))
    ateq.connect()
    request = AteqRequest("B", "cycle-x", "1", 99, "2026-09-12T00:00:00+00:00")
    ateq.program = "1"
    stepcodes = []
    ateq.stepcode_callback = stepcodes.append
    response = ateq.run(request)
    assert stepcodes == [6, 65525]
    assert response.measurement.pressure == 50.0
    assert response.measurement.leakage == 3.0
    assert response.measurement.result is Result.NG  # status bit 0x0006
    assert response.measurement.raw_frame == response.raw_frame


def test_pressure_missing_step6_fails_closed():
    # 仪器快速跳过 StepCode=6（4 -> 65525）：禁止用结束帧的压力值落库。
    step4 = _registers(step=4, status=0x0000, fifo=0x0000,
                       pressure_raw=0x00000000, leak_raw=0x00000000,
                       p_unit=11000, l_unit=12000)
    terminal_only = _registers(step=65525, status=0x0026, fifo=0x0009,
                               pressure_raw=0x0000C350, leak_raw=0x00000BB8,
                               p_unit=11000, l_unit=12000)
    frames = [_build_frame(step4), _build_frame(terminal_only), _build_frame(terminal_only)]
    ateq = SerialAteq("COMX", "B", slave=1, cycle_timeout_s=2,
                      serial_factory=lambda **kwargs: FrameSerial(frames))
    ateq.connect()
    ateq.program = "1"
    request = AteqRequest("B", "cycle-y", "1", 100, "2026-09-12T00:00:00+00:00")
    try:
        ateq.run(request)
    except RuntimeError as exc:
        assert "StepCode=6" in str(exc)
    else:
        raise AssertionError("expected fail-closed without a step-6 snapshot")


def test_field_status_8020_is_decoded_as_ng():
    """The field F620 NG sample uses terminal status 0x8020."""
    step6 = _registers(step=6, status=0x0000, fifo=0x0007,
                       pressure_raw=0x0000C350, leak_raw=0,
                       p_unit=11000, l_unit=12000)
    terminal = _registers(step=65525, status=0x8020, fifo=0x0008,
                          pressure_raw=0, leak_raw=0x00000BB8,
                          p_unit=11000, l_unit=12000)
    frames = [_build_frame(step6), _build_frame(step6), _build_frame(terminal)]
    ateq = SerialAteq("COMX", "B", slave=1, cycle_timeout_s=5,
                      serial_factory=lambda **kwargs: FrameSerial(frames))
    ateq.connect()
    ateq.program = "1"
    request = AteqRequest("B", "cycle-field-ng", "1", 101,
                          "2026-09-18T00:00:00+00:00")
    response = ateq.run(request)
    assert response.measurement.result is Result.NG


def test_measurement_line_contains_date_time():
    measurement = Measurement(250.87, 9.64, Result.OK, b"", "Kpa", "ml/min")
    line = _measurement_line(measurement, when=datetime(2026, 9, 12, 1, 10, 8))
    assert line.startswith("2026-09-12-01:10:08 ")
    assert "250.870Kpa" in line
    assert "9.640ml/min" in line


def test_calibration_print_writes_measurement_files(tmp_path, monkeypatch):
    import app.barprint as bp

    template = tmp_path / "Cal_NG_B.btw"
    template.write_bytes(b"fake")
    (tmp_path / "二维码B.txt").write_text("QR-B", encoding="utf-8")
    exe = tmp_path / "bartend.exe"
    exe.write_bytes(b"fake")

    printer = bp.BarTenderCmdPrinter(exe, tmp_path, tmp_path / "label_data.txt")
    monkeypatch.setattr(bp.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})())
    measurement = Measurement(50.0, 3.0, Result.NG, b"", "Kpa", "ml/min")
    receipt = printer.print_calibration(__import__("app.models", fromlist=["StationId"]).StationId.B,
                                        "NG", measurement=measurement,
                                        when=datetime(2026, 9, 12, 1, 10, 8))
    assert receipt.accepted
    for name in ("正压值B.txt", "负压值B.txt"):
        content = (tmp_path / name).read_text(encoding="utf-8")
        assert content.startswith("2026-09-12-01:10:08 "), (name, content)
        assert "50.000Kpa" in content and "3.000ml/min" in content
    assert (tmp_path / "二维码.txt").read_text(encoding="utf-8") == "QR-B"
    assert (tmp_path / "打印路径B.txt").read_text(encoding="utf-8").endswith("Cal_NG_B.btw")
