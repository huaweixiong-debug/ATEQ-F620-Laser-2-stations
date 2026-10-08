"""三菱 FX 编程口协议（FX3GA-40MT 现场实测修正版）。

2026-10-08 在 B 站（Xiezhong-W02-001，FX3GA-40MT，COM3，9600 7E1）实测：
- ENQ(0x05) -> ACK(0x06) 探活；
- 读命令 '0'：STX '0' 地址(4位HEX) 字节数(2位HEX) ETX LRC
  应答：STX 数据(每字节2位HEX) ETX LRC；
- 写命令 '1'：STX '1' 地址(4位HEX) 字节数(2位HEX) 数据... ETX LRC -> ACK/NAK；
- 强制位命令 '7'(ON)/'8'(OFF)：STX CMD 地址(4位HEX，按 16^1 16^0 16^3 16^2
  交换输出) ETX LRC -> ACK/NAK；
- LRC = 从命令字节到 ETX（含）的字节和低 8 位，两位大写 HEX（不含 STX）。
地址表（FX 编程口内部地址表）：
- M 读/写字节地址 = 0x0100 + M//8，字节内 M%8 为位号（LSB 优先）；
- D 读/写字节地址 = 0x1000 + D*2，16 位数据在线上低字节在前；
- 强制位地址：M0..M1023 -> 0x0800+M；M8000..M8255 -> 0x0F00+(M-8000)。

旧实现（BR/WR 命令 + 补码校验 + 6 位地址）在真机全部被 NAK，已由本版本替换。
"""
from __future__ import annotations
from threading import RLock

STX = 0x02
ETX = 0x03
ENQ = 0x05
ACK = 0x06
NAK = 0x15

READ_CMD = b"0"
WRITE_CMD = b"1"
FORCE_ON_CMD = b"7"
FORCE_OFF_CMD = b"8"

_M_BYTE_BASE = 0x0100
_D_BYTE_BASE = 0x1000
_M_FORCE_BASE = 0x0800
_M_FORCE_MAX = 1023
_M8000_FORCE_BASE = 0x0F00
_M8000_FORCE_MAX = 8255


class FxProtocolError(RuntimeError):
    pass


def lrc(data: bytes) -> str:
    """从命令字节到 ETX（含）的字节和低 8 位，两位大写 HEX（不含 STX）。"""
    if not data:
        raise FxProtocolError("LRC 数据不能为空")
    return f"{sum(data) & 0xFF:02X}"


def bit_byte_address(device: int) -> int:
    """M 元件号 -> 读/写字节地址（0x0100 + M//8）。"""
    device = int(device)
    if device < 0:
        raise ValueError(f"M 元件号不能为负: {device}")
    return _M_BYTE_BASE + device // 8


def word_byte_address(device: int) -> int:
    """D 元件号 -> 读/写字节地址（0x1000 + D*2）。"""
    device = int(device)
    if not 0 <= device <= 0x1FFF:
        raise ValueError(f"D 元件号超范围: {device}")
    return _D_BYTE_BASE + device * 2


def force_address(device: int) -> int:
    """M 元件号 -> 强制命令位地址（字节交换后写入 4 位 HEX）。"""
    device = int(device)
    if 0 <= device <= _M_FORCE_MAX:
        return _M_FORCE_BASE + device
    if 8000 <= device <= _M8000_FORCE_MAX:
        return _M8000_FORCE_BASE + (device - 8000)
    raise ValueError(f"强制地址不支持 M{device}（仅 M0-M1023 / M8000-M8255）")


def swap_bytes_hex(value: int) -> str:
    """16 位值 -> 4 位 HEX，按 16^1 16^0 16^3 16^2（字节交换）输出。"""
    text = f"{int(value) & 0xFFFF:04X}"
    return text[2] + text[3] + text[0] + text[1]


def words_hex(words: list[int]) -> str:
    """16 位字列表 -> 线上 HEX 数据（每个字低字节在前）。"""
    parts = []
    for word in words:
        word = int(word)
        if not 0 <= word <= 0xFFFF:
            raise ValueError(f"字数据超 16 位范围: {word}")
        parts.append(swap_bytes_hex(word))
    return "".join(parts)


def _frame(command: bytes, address: int, body: bytes) -> bytes:
    core = bytes([STX]) + command + f"{address:04X}".encode("ascii") + body + bytes([ETX])
    return core + lrc(core[1:]).encode("ascii")


def build_read_request(byte_address: int, nbytes: int) -> bytes:
    nbytes = int(nbytes)
    if not 1 <= nbytes <= 0xFF:
        raise ValueError(f"读取字节数必须是 1..255: {nbytes}")
    return _frame(READ_CMD, byte_address, f"{nbytes:02X}".encode("ascii"))


def build_read_bits_request(device: int, count: int) -> bytes:
    """读取 M 位区间的请求帧；返回覆盖区间的最小字节块。"""
    count = int(count)
    if not 1 <= count <= 0xFF * 8:
        raise ValueError(f"位读取数量必须是 1..2040: {count}")
    first = int(device)
    last = first + count - 1
    nbytes = (last // 8) - (first // 8) + 1
    return build_read_request(bit_byte_address(first), nbytes)


def build_read_words_request(device: int, count: int) -> bytes:
    count = int(count)
    if not 1 <= count <= 0x7F:
        raise ValueError(f"字读取数量必须是 1..127: {count}")
    return build_read_request(word_byte_address(device), count * 2)


def build_write_request(byte_address: int, data_hex: str) -> bytes:
    data = data_hex.encode("ascii")
    if len(data) % 2 or not 2 <= len(data) <= 0x1FE:
        raise ValueError(f"写入数据 HEX 长度非法: {len(data)}")
    return _frame(WRITE_CMD, byte_address, f"{len(data) // 2:02X}".encode("ascii") + data)


def build_write_words_request(device: int, words: list[int]) -> bytes:
    if not 1 <= len(words) <= 0x7F:
        raise ValueError(f"字写入数量必须是 1..127: {len(words)}")
    return build_write_request(word_byte_address(device), words_hex(words))


def build_force_request(device: int, value: bool) -> bytes:
    command = FORCE_ON_CMD if value else FORCE_OFF_CMD
    address_text = swap_bytes_hex(force_address(device)).encode("ascii")
    core = bytes([STX]) + command + address_text + bytes([ETX])
    return core + lrc(core[1:]).encode("ascii")


def swap_hex_bytes(text: str) -> int:
    """4 位 HEX（字节交换输出）-> 16 位整数。"""
    if len(text) != 4:
        raise FxProtocolError(f"强制地址长度非法: {text!r}")
    try:
        return int(text[2] + text[3] + text[0] + text[1], 16)
    except ValueError as exc:
        raise FxProtocolError(f"强制地址含非法 HEX: {text!r}") from exc


def parse_read_response(raw: bytes, nbytes: int) -> bytes:
    """校验读应答帧并返回数据字节。"""
    if raw and raw[0] == NAK:
        raise FxProtocolError(f"FX NAK 应答: {raw!r}")
    expected = 2 * int(nbytes) + 4
    if len(raw) != expected:
        raise FxProtocolError(f"FX 读应答长度 {len(raw)} != {expected}: {raw!r}")
    if raw[0] != STX or raw[-3] != ETX:
        raise FxProtocolError(f"FX 读应答帧结构错误: {raw!r}")
    try:
        body = raw[1:-2]  # 数据 + ETX
        checksum = raw[-2:].decode("ascii").upper()
    except UnicodeDecodeError as exc:
        raise FxProtocolError(f"FX 读应答校验段非法: {raw!r}") from exc
    if checksum != lrc(body):
        raise FxProtocolError(f"FX 读应答和校验错误: {raw!r}")
    try:
        return bytes.fromhex(raw[1:-3].decode("ascii"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise FxProtocolError(f"FX 读应答数据非法: {raw!r}") from exc


def extract_bits(data: bytes, first_bit: int, count: int) -> list[bool]:
    """从字节数据提取 M 位（字节内 LSB 优先）。"""
    if first_bit not in range(8):
        raise ValueError(f"起始位号必须是 0..7: {first_bit}")
    needed = first_bit + int(count)
    if len(data) * 8 < needed:
        raise FxProtocolError(f"位数据不足: {len(data)} 字节 < {needed} 位")
    bits: list[bool] = []
    for index in range(needed):
        byte = data[index // 8]
        bits.append(bool(byte & (1 << (index % 8))))
    return bits[first_bit:]


def parse_read_bits_response(raw: bytes, first_bit: int, count: int) -> list[bool]:
    nbytes = (first_bit + int(count) + 7) // 8
    return extract_bits(parse_read_response(raw, nbytes), first_bit, count)


def parse_read_words_response(raw: bytes, count: int) -> list[int]:
    data = parse_read_response(raw, int(count) * 2)
    return [int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2)]


def parse_ack(raw: bytes) -> bool:
    if len(raw) == 1 and raw[0] == ACK:
        return True
    if raw and raw[0] == NAK:
        raise FxProtocolError(f"FX NAK 应答: {raw!r}")
    raise FxProtocolError(f"FX 写应答异常: {raw!r}")


class FxSerialClient:
    """线程安全 FX 编程口串口客户端（pyserial，可注入 fake 工厂做测试）。"""

    def __init__(self, port: str, *, baudrate: int = 9600, parity: str = "E",
                 bytesize: int = 7, stopbits: int = 1, timeout_s: float = 1.0,
                 serial_factory=None) -> None:
        self.port = port
        self.baudrate, self.parity = int(baudrate), str(parity).upper()
        self.bytesize, self.stopbits = int(bytesize), int(stopbits)
        self.timeout_s = float(timeout_s)
        self.connected = False
        self._serial = None
        self._serial_factory = serial_factory
        self._lock = RLock()

    def connect(self) -> None:
        with self._lock:
            if self._serial is not None and getattr(self._serial, "is_open", True):
                self.connected = True
                return
            if self._serial_factory is None:
                try:
                    import serial
                except ImportError as exc:
                    raise RuntimeError("pyserial 未安装，无法连接 FX PLC") from exc
                self._serial = serial.Serial(
                    port=self.port, baudrate=self.baudrate,
                    bytesize=self.bytesize, parity=self.parity, stopbits=self.stopbits,
                    timeout=self.timeout_s, write_timeout=self.timeout_s)
            else:
                self._serial = self._serial_factory(
                    port=self.port, baudrate=self.baudrate,
                    bytesize=self.bytesize, parity=self.parity, stopbits=self.stopbits,
                    timeout=self.timeout_s, write_timeout=self.timeout_s)
            self.connected = True

    def close(self) -> None:
        with self._lock:
            handle, self._serial = self._serial, None
            self.connected = False
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    pass

    def health(self) -> bool:
        try:
            return self.ping()
        except Exception:
            self.close()
            return False

    def _exchange(self, request: bytes, response_len: int) -> bytes:
        """发送请求；先读 1 字节（STX/ACK/NAK），再补齐剩余应答。"""
        with self._lock:
            self.connect()
            try:
                reset = getattr(self._serial, "reset_input_buffer", None)
                if reset is not None:
                    reset()
                self._serial.write(request)
                self._serial.flush()
            except Exception:
                self.close()
                raise
            first = self._serial.read(1)
            if len(first) != 1:
                raise FxProtocolError(f"FX {self.port} 无应答")
            if first[0] in (ACK, NAK):
                return first
            remaining = int(response_len) - 1
            if remaining <= 0:
                return first
            rest = self._serial.read(remaining)
            if len(rest) != remaining:
                raise FxProtocolError(
                    f"FX {self.port} 应答长度 {len(first) + len(rest)} != {response_len}")
            return first + rest

    def ping(self) -> bool:
        """ENQ 探活：PLC 正常时回 ACK 单字节。"""
        response = self._exchange(bytes([ENQ]), 1)
        if response == bytes([ACK]):
            return True
        if response and response[0] == NAK:
            return False
        raise FxProtocolError(f"FX 探活应答异常: {response!r}")

    def read_bits(self, device: int, count: int) -> list[bool]:
        first = int(device)
        request = build_read_bits_request(first, count)
        first_bit = first % 8
        nbytes = (first_bit + int(count) + 7) // 8
        response = self._exchange(request, 2 * nbytes + 4)
        return parse_read_bits_response(response, first_bit, count)

    def write_bits(self, device: int, values: list[bool]) -> None:
        for offset, value in enumerate(values):
            request = build_force_request(int(device) + offset, bool(value))
            parse_ack(self._exchange(request, 1))

    def read_words(self, device: int, count: int) -> list[int]:
        count = int(count)
        request = build_read_words_request(device, count)
        response = self._exchange(request, 2 * count * 2 + 4)
        return parse_read_words_response(response, count)

    def write_words(self, device: int, values: list[int]) -> None:
        request = build_write_words_request(device, values)
        parse_ack(self._exchange(request, 1))
