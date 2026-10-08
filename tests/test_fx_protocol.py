"""FX 编程口协议测试（2026-10-08 FX3GA-40MT 真机实测修正后的帧）。

实测环境：B 站 COM3，9600 7E1。
- 读命令 '0'：STX '0' 地址(4HEX) 字节数(2HEX) ETX LRC
- 应答：STX 数据(2HEX/字节) ETX LRC
- LRC = 命令..ETX 的和低 8 位（不含 STX）
- 强制 '7'/'8'；地址按字节交换输出
"""
import pytest

from app.fx_protocol import (STX, ETX, ACK, NAK, ENQ,
                             lrc, bit_byte_address, word_byte_address,
                             force_address, swap_bytes_hex, swap_hex_bytes,
                             words_hex, build_read_request, build_read_bits_request,
                             build_read_words_request, build_write_words_request,
                             build_force_request, parse_read_response, extract_bits,
                             parse_read_bits_response, parse_read_words_response,
                             parse_ack, FxProtocolError)


def test_lrc_plain_sum_excludes_stx():
    # 真机 M0..M7 请求捕获：02 30 30 31 30 30 30 31 03 35 35
    body = b"0" + b"0100" + b"01" + bytes([ETX])
    assert lrc(body) == "55"
    assert lrc(b"0" + b"0100" + b"01" + bytes([ETX])) == "55"


def test_read_request_m0_real_capture():
    # 与真机捕获完全一致：0230303130303031033535
    assert build_read_bits_request(0, 8) == bytes.fromhex("0230303130303031033535")


def test_bit_and_word_byte_address():
    assert bit_byte_address(0) == 0x0100
    assert bit_byte_address(7) == 0x0100
    assert bit_byte_address(8) == 0x0101
    assert bit_byte_address(890) == 0x016F
    assert bit_byte_address(891) == 0x016F
    assert word_byte_address(0) == 0x1000
    assert word_byte_address(900) == 0x1708  # 0x1000 + 900*2
    with pytest.raises(ValueError):
        bit_byte_address(-1)
    with pytest.raises(ValueError):
        word_byte_address(0x2000)


def test_force_address_table_and_swap():
    assert force_address(0) == 0x0800
    assert force_address(3) == 0x0803
    assert force_address(890) == 0x0B7A
    assert force_address(1023) == 0x0BFF
    assert force_address(8000) == 0x0F00
    assert force_address(8013) == 0x0F0D
    for bad in (1024, 7999, 8256, -1):
        with pytest.raises(ValueError):
            force_address(bad)
    assert swap_bytes_hex(0x0800) == "0008"
    assert swap_bytes_hex(0x0B7A) == "7A0B"
    assert swap_bytes_hex(0x1708) == "0817"
    assert swap_hex_bytes("7A0B") == 0x0B7A
    assert swap_hex_bytes("0008") == 0x0800


def test_force_frames():
    # 强制 M0 ON：02 37 '0008' 03 02
    assert build_force_request(0, True) == bytes.fromhex("023730303038033032")
    # 强制 M890 ON：地址 0B7A -> 交换 7A0B
    assert build_force_request(890, True) == bytes.fromhex("023737413042033234")
    # 强制 M8000 OFF：地址 0F00 -> 交换 000F
    assert build_force_request(8000, False) == bytes.fromhex("023830303046033131")


def test_read_words_request_and_parse():
    # D900 读 1 字：地址 1708，字节数 02
    assert build_read_words_request(900, 1) == bytes.fromhex("0230313730383032033635")
    # 应答数据 "D204" = 0x04D2（低字节在前）
    raw = bytes([STX]) + b"D204" + bytes([ETX])
    raw += lrc(raw[1:]).encode()
    assert parse_read_words_response(raw, 1) == [0x04D2]


def test_write_words_request():
    frame = build_write_words_request(900, [0x04D2])
    assert frame.startswith(bytes([STX]) + b"1" + b"1708" + b"02")
    assert b"D204" in frame
    assert frame[-3] == ETX
    assert words_hex([0x04D2]) == "D204"
    assert words_hex([0x1234, 0xABCD]) == "3412CDAB"
    with pytest.raises(ValueError):
        build_write_words_request(0, [0x10000])
    with pytest.raises(ValueError):
        build_write_words_request(0, [])


def test_read_response_validation():
    assert parse_read_response(bytes.fromhex("023030033633"), 1) == b"\x00"
    with pytest.raises(FxProtocolError):
        parse_read_response(bytes([NAK]), 1)
    with pytest.raises(FxProtocolError):
        parse_read_response(bytes.fromhex("023030033630"), 1)  # 坏校验
    with pytest.raises(FxProtocolError):
        parse_read_response(bytes.fromhex("0230303033"), 2)  # 长度不符


def test_extract_bits_lsb_first_and_offset():
    data = bytes([0b10101010, 0b00000011])
    assert extract_bits(data, 0, 8) == [False, True, False, True, False, True, False, True]
    assert extract_bits(data, 2, 3) == [False, True, False]
    assert extract_bits(data, 6, 4) == [False, True, True, True]
    with pytest.raises(FxProtocolError):
        extract_bits(b"\x00", 7, 2)


def test_read_bits_response_real_capture():
    # 真机 M8..M23 读应答捕获样例：数据 "00"（无位 ON）
    assert parse_read_bits_response(bytes.fromhex("023030033633"), 0, 8) == [False] * 8


def test_parse_ack():
    assert parse_ack(bytes([ACK])) is True
    with pytest.raises(FxProtocolError):
        parse_ack(bytes([NAK]))
    with pytest.raises(FxProtocolError):
        parse_ack(b"")


def test_enq_constants():
    assert ENQ == 0x05
    assert ACK == 0x06
    assert NAK == 0x15
