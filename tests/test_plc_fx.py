"""FxSerialPlc 适配器测试：用内存 FX 设备模拟器验证读/写/健康/安全停止。"""
import pytest

from app.fx_protocol import (STX, ETX, ACK, checksum, build_batch_read_bits,
                             build_batch_write_bits, build_batch_read_words,
                             build_batch_write_words)
from app.plc import FxSerialPlc, FakeFxPlc


class FakeFxPort:
    """串口替身：解析请求帧，读写共享的 M/D 内存。"""

    def __init__(self, bits: dict[int, bool], words: dict[int, int], **kwargs):
        self.bits = bits
        self.words = words
        self.is_open = True
        self.written: list[bytes] = []

    def close(self):
        self.is_open = False

    def reset_input_buffer(self):
        pass

    def flush(self):
        pass

    def write(self, data: bytes):
        self.written.append(data)
        return len(data)

    def read(self, n: int) -> bytes:
        request = self.written[-1]
        if request[0] == 0x05:                      # ENQ 探活
            return bytes([ACK])
        command = request[1:3]
        body = request[1:-3]
        if command == b"BR":
            start = int(request[3:9], 16)
            count = int(request[9:11], 16)
            data = "".join("1" if self.bits.get(start + i, False) else "0"
                           for i in range(count)).encode()
            frame = bytes([STX]) + data + bytes([ETX])
            return frame + checksum(frame).encode()
        if command == b"BW":
            start = int(request[3:9], 16)
            count = int(request[9:11], 16)
            payload = request[11:11 + count]
            for i in range(count):
                self.bits[start + i] = payload[i:i + 1] == b"1"
            return bytes([ACK])
        if command == b"WR":
            start = int(request[3:9], 16)
            count = int(request[9:11], 16)
            data = b"".join(f"{self.words.get(start + i, 0):04X}".encode()
                            for i in range(count))
            frame = bytes([STX]) + data + bytes([ETX])
            return frame + checksum(frame).encode()
        if command == b"WW":
            start = int(request[3:9], 16)
            count = int(request[9:11], 16)
            payload = request[11:11 + count * 4]
            for i in range(count):
                self.words[start + i] = int(payload[i * 4:(i + 1) * 4], 16)
            return bytes([ACK])
        raise AssertionError(f"未知 FX 命令: {request!r}")


@pytest.fixture
def fx_env():
    bits: dict[int, bool] = {}
    words: dict[int, int] = {}
    created = {}

    def factory(**kwargs):
        created["port"] = FakeFxPort(bits, words, **kwargs)
        return created["port"]

    plc = FxSerialPlc("COMTEST", serial_factory=factory)
    plc.connect()
    return plc, bits, words, created


def test_read_bit_maps_m_device(fx_env):
    plc, bits, _, _ = fx_env
    bits[890] = True          # M890（右屏蔽气缸）
    assert plc.read_bit(111, 2) is True       # 111*8+2 = 890
    assert plc.read_bit(0, 0) is False        # M0（左复位）默认 False


def test_write_bit_sets_and_clears(fx_env):
    plc, bits, _, _ = fx_env
    plc.enable_writes(True)
    plc.write_bit(1, 0, True)                 # M8 = 左合格?
    assert bits[8] is True
    assert plc.read_bit(1, 0) is True
    plc.write_bit(1, 0, False)
    assert bits[8] is False


def test_write_gated_by_default(fx_env):
    plc, bits, _, _ = fx_env
    with pytest.raises(PermissionError):
        plc.write_bit(0, 0, True)


def test_read_write_word_d900(fx_env):
    plc, _, words, _ = fx_env
    plc.enable_writes(True)
    plc.write_word(900, 1234)                 # D900 = 实时重量
    assert words[900] == 1234
    assert plc.read_word(900) == 1234


def test_health_ping(fx_env):
    plc, _, _, _ = fx_env
    assert plc.health() is True


def test_safe_stop_disables_writes(fx_env):
    plc, _, _, _ = fx_env
    plc.enable_writes(True)
    plc.safe_stop("测试安全停止")
    assert plc._writes_enabled is False
    with pytest.raises(PermissionError):
        plc.write_bit(0, 0, True)


def test_outputs_energized(fx_env):
    plc, bits, _, _ = fx_env
    assert plc.outputs_energized() is False
    bits[4] = True                            # M4 = 右合格
    assert plc.outputs_energized() is True


def test_fake_fx_plc_standalone():
    fake = FakeFxPlc()
    fake.write_bit(0, 1, True)
    assert fake.read_bit(0, 1) is True
    fake.write_word(900, 500)
    assert fake.read_word(900) == 500
    assert fake.outputs_energized() is True
