"""Tests for the remote ATEQ leak-limit (Test FAIL) configuration sequence."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from app.ateq import AteqRequest, SerialAteq, modbus_crc16


class ScriptedSerial:
    """Captures writes; answers each write with a programmed response."""

    def __init__(self, instrument_value_milli, little_endian_store=True, pin_value=False):
        self.writes = []
        self.value = instrument_value_milli
        self.pin = pin_value
        self.little_endian_store = little_endian_store
        self.responses = []  # queued raw responses for read requests

    def is_open(self):
        return True

    def reset_input_buffer(self):
        pass

    def write(self, data):
        self.writes.append(data)
        function = data[1]
        if function == 0x06:
            self.responses.append(data)  # echo
        elif function == 0x10:
            # decode written halves in the requested byte order
            body = data[7:-2]
            half0 = int.from_bytes(body[0:2], "little" if self.little_endian_store else "big")
            half1 = int.from_bytes(body[2:4], "little" if self.little_endian_store else "big")
            if not self.pin:
                if self.little_endian_store:
                    # instrument stores low byte first: meaningful value = halves raw
                    self.value = half0 | (half1 << 16)
                else:
                    self.value = (half0 << 16) | half1
            payload = data[:6]  # fn10 回显 = 地址+数量，共 6 字节
            self.responses.append(payload + modbus_crc16(payload).to_bytes(2, "little"))
        elif function == 0x03:
            count = int.from_bytes(data[4:6], "big")
            if count == 2:
                value = self.value
                if self.little_endian_store:
                    halves = (value & 0xFFFF, (value >> 16) & 0xFFFF)
                    body = b"".join(h.to_bytes(2, "little") for h in halves)
                else:
                    body = value.to_bytes(4, "big")
                payload = bytes([data[0], 3, count * 2]) + body
            else:  # unit register
                payload = bytes([data[0], 3, 2]) + (51000).to_bytes(2, "little")
            self.responses.append(payload + modbus_crc16(payload).to_bytes(2, "little"))
        else:
            raise AssertionError(f"unexpected function {function:#x}")

    def flush(self):
        pass

    def read(self, count):
        return self.responses.pop(0)[:count]


def _adapter(serial):
    ateq = SerialAteq("COMX", "B", slave=1, cycle_timeout_s=2,
                      serial_factory=lambda **_: serial)
    ateq.program = "1"
    return ateq


def test_set_test_fail_limit_little_endian_verified():
    serial = ScriptedSerial(9500)
    ateq = _adapter(serial)
    assert ateq.set_test_fail_limit(1004) == 1004
    assert serial.value == 1004
    # 0x6000 program-select write present with program-1 value 0
    select = serial.writes[0]
    assert select[1] == 6 and select[2:4] == b"\x60\x00" and select[4:6] == b"\x00\x00"
    # test-fail write targeted 0x603C via fn 0x10
    fail_write = serial.writes[1]
    assert fail_write[1] == 0x10 and fail_write[2:4] == b"\x60\x3c"
    # unit read at 0x207F present
    unit_read = serial.writes[-1]
    assert unit_read[1] == 3 and unit_read[2:4] == b"\x20\x7f"


def test_set_test_fail_limit_mismatch_raises():
    serial = ScriptedSerial(9999, pin_value=True)  # 仪器不生效 -> 读回不一致
    ateq = _adapter(serial)
    with pytest.raises(RuntimeError, match="读回"):
        ateq.set_test_fail_limit(1004)


def test_start_coil_never_written_for_limit_change():
    serial = ScriptedSerial(1004)
    ateq = _adapter(serial)
    ateq.set_test_fail_limit(1004)
    for frame in serial.writes:
        assert not (frame[1] == 5 and frame[2:4] == b"\x00\x01"), "不得写启动线圈"
