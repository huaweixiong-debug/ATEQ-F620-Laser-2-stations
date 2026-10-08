"""FxSerialPlc 适配器测试：用内存 FX 设备模拟器验证读/写/健康/安全停止。

模拟器实现 2026-10-08 真机实测修正后的编程口协议（'0' 读 / '1' 写 / '7''8' 强制）。
"""
import pytest

from app.fx_protocol import (STX, ETX, ACK, lrc, swap_bytes_hex, swap_hex_bytes)
from app.plc import FxSerialPlc, FakeFxPlc


class FakeFxPort:
    """串口替身：解析请求帧，读写共享的 M/D 内存。"""

    def __init__(self, bits: dict[int, bool], words: dict[int, int], **kwargs):
        self.bits = bits
        self.words = words
        self.is_open = True
        self.written: list[bytes] = []
        self._reply = b""

    def close(self):
        self.is_open = False

    def reset_input_buffer(self):
        self._reply = b""

    def flush(self):
        pass

    def write(self, data: bytes):
        self.written.append(data)
        self._reply = self._handle(data)
        return len(data)

    def read(self, n: int) -> bytes:
        chunk, self._reply = self._reply[:n], self._reply[n:]
        return chunk

    def _reply_frame(self, data_hex: str) -> bytes:
        frame = bytes([STX]) + data_hex.encode("ascii") + bytes([ETX])
        return frame + lrc(frame[1:]).encode("ascii")

    def _handle(self, request: bytes) -> bytes:
        if request[0] == 0x05:                       # ENQ 探活
            return bytes([ACK])
        assert request[0] == STX, f"帧头非 STX: {request!r}"
        command = request[1:2]
        if command in (b"7", b"8"):                  # 强制位
            swapped = swap_hex_bytes(request[2:6].decode("ascii"))
            if 0x0800 <= swapped <= 0x0BFF:
                device = swapped - 0x0800
            elif 0x0F00 <= swapped <= 0x0FFF:
                device = 8000 + (swapped - 0x0F00)
            else:
                raise AssertionError(f"强制地址超范围: {swapped:04X}")
            self.bits[device] = command == b"7"
            return bytes([ACK])
        address = int(request[2:6], 16)
        nbytes = int(request[6:8], 16)
        if command == b"0":                          # 读
            if address >= 0x1000:                    # D 区
                assert address % 2 == 0
                first = (address - 0x1000) // 2
                data_hex = "".join(swap_bytes_hex(self.words.get(first + i, 0))
                                   for i in range(nbytes // 2))
            else:                                    # M 区位字节
                first = (address - 0x0100) * 8
                data_hex = ""
                for i in range(nbytes):
                    value = 0
                    for bit in range(8):
                        if self.bits.get(first + i * 8 + bit, False):
                            value |= 1 << bit
                    data_hex += f"{value:02X}"
            return self._reply_frame(data_hex)
        if command == b"1":                          # 写
            payload = request[8:8 + nbytes * 2].decode("ascii")
            if address >= 0x1000:
                first = (address - 0x1000) // 2
                for i in range(nbytes // 2):
                    self.words[first + i] = swap_hex_bytes(payload[i * 4:(i + 1) * 4])
            else:
                self.bits[(address - 0x0100) * 8] = payload != "00"
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


def test_read_mixed_byte_bits(fx_env):
    plc, bits, _, _ = fx_env
    bits[3] = True
    bits[4] = True
    bits[6] = True
    assert plc.read_bit(0, 3) is True
    assert plc.read_bit(0, 4) is True
    assert plc.read_bit(0, 5) is False
    assert plc.read_bit(0, 6) is True


def test_write_bit_sets_and_clears(fx_env):
    plc, bits, _, _ = fx_env
    plc.enable_writes(True)
    plc.write_bit(1, 0, True)                 # M8
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
