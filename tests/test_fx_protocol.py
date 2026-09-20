"""FX 编程口（计算机链接 Format 1）协议帧构造/解析测试。

协议要点（FX-232AW / FX 计算机链接格式1）：
- 帧以 STX(0x02) 开始、ETX(0x03) 结束，和校验 = STX..ETX 全部字节之和的
  补码低 8 位，两位大写十六进制 ASCII。
- 位批量读 BR：STX 'BR' 起始元件(6位16进制) 点数(2位16进制) ETX 和
- 位批量写 BW：STX 'BW' 起始元件 点数 数据('0'/'1'*点数) ETX 和，应答 ACK(0x06)
- 字批量读 WR：STX 'WR' 起始元件 字数 数据(4位16进制/字) ETX 和
- 字批量写 WW：同 WR 结构带数据，应答 ACK
- M/D 元件地址 = 十进制元件号的十六进制表示（M890 -> "00037A"）
- 出错应答 NAK(0x15) + 2 位错误码
"""
import pytest

from app.fx_protocol import (STX, ETX, ACK, NAK, ENQ,
                             checksum, build_batch_read_bits, build_batch_write_bits,
                             build_batch_read_words, build_batch_write_words,
                             parse_read_bits_response, parse_read_words_response,
                             parse_write_ack, device_address_hex, FxProtocolError)


def test_checksum_twos_complement():
    frame = b"\x02BR00000001\x03"
    total = sum(frame) + int(checksum(frame), 16)
    assert total % 256 == 0
    assert checksum(frame) == "E6"


def test_checksum_known_vector():
    # 手工验算：0x02+0x42+0x52+0x30*4+0x03 = 0x159，补码低 8 位 = 0x100-0x59 = 0xA7。
    assert checksum(b"\x02\x42\x52\x30\x30\x30\x30\x03") == "A7"


def test_device_address_hex():
    assert device_address_hex(0) == "000000"
    assert device_address_hex(890) == "00037A"
    assert device_address_hex(900) == "000384"
    with pytest.raises(ValueError):
        device_address_hex(-1)
    with pytest.raises(ValueError):
        device_address_hex(0x1000000)


def test_build_batch_read_bits_structure():
    frame = build_batch_read_bits(0, 1)
    assert frame.startswith(bytes([STX]) + b"BR")
    assert b"000000" in frame          # 起始元件 M0
    assert frame.endswith(b"01" + bytes([ETX]) + checksum(frame[:-2]).encode())
    assert len(frame) == 1 + 2 + 6 + 2 + 1 + 2


def test_build_batch_read_bits_m890():
    frame = build_batch_read_bits(890, 2)
    assert b"00037A" in frame
    assert frame[9:11] == b"02"


def test_build_batch_write_bits():
    frame = build_batch_write_bits(1, [True, False, True])
    assert frame.startswith(bytes([STX]) + b"BW00000103")
    assert frame[-6:-3] == b"101"
    assert frame[-3] == ETX


def test_build_batch_read_words():
    frame = build_batch_read_words(900, 1)
    assert frame.startswith(bytes([STX]) + b"WR")
    assert frame[3:9] == b"000384"
    assert frame[9:11] == b"01"
    assert frame[-3] == ETX


def test_build_batch_write_words():
    frame = build_batch_write_words(900, [0x1234])
    assert frame.startswith(bytes([STX]) + b"WW00038401")
    assert b"1234" in frame
    assert frame[-3] == ETX


def test_write_words_rejects_out_of_range():
    with pytest.raises(ValueError):
        build_batch_write_words(900, [0x10000])
    with pytest.raises(ValueError):
        build_batch_write_words(900, [-1])
    with pytest.raises(ValueError):
        build_batch_read_bits(0, 0)      # 点数为 0 非法
    with pytest.raises(ValueError):
        build_batch_read_bits(0, 0x100)  # 点数超过 2 位十六进制


def test_parse_read_bits_response():
    frame = bytes([STX]) + b"101" + bytes([ETX])
    frame += checksum(frame).encode()
    assert parse_read_bits_response(frame) == [True, False, True]


def test_parse_read_words_response():
    payload = b"0000" + b"FFFF"
    frame = bytes([STX]) + payload + bytes([ETX])
    frame += checksum(frame).encode()
    assert parse_read_words_response(frame) == [0, 0xFFFF]


def test_parse_response_rejects_bad_checksum():
    frame = bytearray(bytes([STX]) + b"101" + bytes([ETX]) + b"00")
    with pytest.raises(FxProtocolError):
        parse_read_bits_response(bytes(frame))


def test_parse_write_ack():
    assert parse_write_ack(bytes([ACK])) is True
    nak = bytes([NAK]) + b"02"
    with pytest.raises(FxProtocolError) as excinfo:
        parse_write_ack(nak)
    assert "02" in str(excinfo.value)


def test_enq_ping_frame():
    # ENQ 探活：单字节 0x05，应答 ACK。
    assert ENQ == 0x05
    assert ACK == 0x06
