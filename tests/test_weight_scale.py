"""称重适配器测试：COM6 Modbus RTU 读 40002，周期写 PLC D900。

老链路：NI OPC Servers modbus 串口通道读 40002 -> LabVIEW 写 D900(实时重量)。
新链路：WeightScale 直读 COM6 -> WeightService 写 PLC 字寄存器。
"""
import time

import pytest

from app.weight_scale import WeightScale, WeightService, modbus_read_request, parse_read_holding
from app.plc import FakeFxPlc


class FakeModbusSlave:
    """最小 Modbus RTU 从站替身：fn03 读保持寄存器。"""

    def __init__(self, registers: dict[int, int]):
        self.registers = registers
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
        from app.ateq import modbus_crc16
        request = self.written[-1]
        assert request[1] == 0x03
        address = int.from_bytes(request[2:4], "big")
        count = int.from_bytes(request[4:6], "big")
        payload = bytes([request[0]]) + b"\x03" + bytes([count * 2])
        for i in range(count):
            payload += self.registers.get(address + i, 0).to_bytes(2, "big")
        payload += modbus_crc16(payload).to_bytes(2, "little")
        return payload


def test_modbus_read_request_frame():
    payload, expected_len = modbus_read_request(slave=1, address=1, count=1)
    # 40002 -> 协议地址 1
    assert payload[0] == 1 and payload[1] == 0x03
    assert int.from_bytes(payload[2:4], "big") == 1
    assert expected_len == 7  # addr+fn+bc+2data+2crc


def test_parse_read_holding():
    from app.ateq import modbus_crc16
    response = bytes([1, 0x03, 2]) + (88).to_bytes(2, "big")
    response += modbus_crc16(response).to_bytes(2, "little")
    assert parse_read_holding(response, count=1, slave=1) == [88]


def test_weight_scale_read():
    registers = {1: 1234}
    scale = WeightScale("COMTEST", slave=1, register=40002,
                        serial_factory=lambda **kw: FakeModbusSlave(registers))
    scale.connect()
    assert scale.read_raw() == 1234
    scale.close()


def test_weight_service_writes_plc_word():
    registers = {1: 555}
    scale = WeightScale("COMTEST", slave=1, register=40002,
                        serial_factory=lambda **kw: FakeModbusSlave(registers))
    plc = FakeFxPlc()
    service = WeightService(scale, plc, plc_register=900,
                            poll_s=0.01, writes_enabled=True)
    service.read_and_write_once()
    assert plc.read_word(900) == 555


def test_weight_service_disabled_writes():
    registers = {1: 555}
    scale = WeightScale("COMTEST", slave=1, register=40002,
                        serial_factory=lambda **kw: FakeModbusSlave(registers))
    plc = FakeFxPlc()
    service = WeightService(scale, plc, plc_register=900, poll_s=0.01,
                            writes_enabled=False)
    with pytest.raises(PermissionError):
        service.read_and_write_once()


def test_weight_service_background_thread():
    registers = {1: 77}
    scale = WeightScale("COMTEST", slave=1, register=40002,
                        serial_factory=lambda **kw: FakeModbusSlave(registers))
    plc = FakeFxPlc()
    service = WeightService(scale, plc, plc_register=900,
                            poll_s=0.01, writes_enabled=True)
    service.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and plc.read_word(900) != 77:
        time.sleep(0.02)
    service.stop()
    assert plc.read_word(900) == 77
