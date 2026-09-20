"""三菱 FX 编程口计算机链接协议（Format 1）帧构造与解析。

240429 箱体气密封机的 FX PLC 接在 COM3（编程口，SC-09 电平转换）。
老 LabVIEW 方案经 NI OPC Servers 中转；本模块让 Python 直连，去掉中间件。

帧结构（STX 0x02 开始、ETX 0x03 结束）：
- 和校验：STX..ETX 全部字节之和的补码低 8 位，两位大写十六进制 ASCII。
- 位批量读 ``BR``：STX 'BR' 起始元件(6位HEX) 点数(2位HEX) ETX 和
  应答：STX '0'/'1'*点数 ETX 和
- 位批量写 ``BW``：STX 'BW' 起始元件 点数 '0'/'1'*点数 ETX 和；应答 ACK(0x06)
- 字批量读 ``WR``：STX 'WR' 起始元件 字数(2位HEX) ETX 和
  应答：STX 4位HEX*字数 ETX 和
- 字批量写 ``WW``：STX 'WW' 起始元件 字数 数据(4位HEX*字数) ETX 和；应答 ACK
- 错误应答：NAK(0x15) + 2 位错误码
- 探活：ENQ(0x05) -> ACK(0x06)

M/D 元件地址 = 十进制元件号的十六进制表示（M890 -> "00037A"，D900 -> "000384"）。
X/Y 为八进制编号，本机点位表只使用 M/D，不做八进制换算。
"""
from __future__ import annotations
from threading import RLock

STX = 0x02
ETX = 0x03
ENQ = 0x05
ACK = 0x06
NAK = 0x15


class FxProtocolError(RuntimeError):
    pass


def checksum(frame: bytes) -> str:
    """STX..ETX 字节和的补码低 8 位，两位大写 HEX。frame 含 STX/ETX。"""
    if len(frame) < 2 or frame[0] != STX or frame[-1] != ETX:
        raise FxProtocolError("和校验只对完整帧（STX..ETX）计算")
    return f"{(-(sum(frame))) & 0xFF:02X}"


def device_address_hex(device: int) -> str:
    """M/D 十进制元件号 -> 6 位大写十六进制地址字段。"""
    device = int(device)
    if not 0 <= device <= 0xFFFFFF:
        raise ValueError(f"元件号超范围: {device}")
    return f"{device:06X}"


def _frame(command: bytes, device: int, body: bytes) -> bytes:
    core = bytes([STX]) + command + device_address_hex(device).encode("ascii") + body + bytes([ETX])
    return core + checksum(core).encode("ascii")


def _validate_count(count: int, *, what: str) -> int:
    count = int(count)
    if not 1 <= count <= 0xFF:
        raise ValueError(f"{what}点数必须是 1..255")
    return count


def build_batch_read_bits(device: int, count: int) -> bytes:
    count = _validate_count(count, what="位读")
    return _frame(b"BR", device, f"{count:02X}".encode("ascii"))


def build_batch_write_bits(device: int, values: list[bool]) -> bytes:
    count = _validate_count(len(values), what="位写")
    data = "".join("1" if v else "0" for v in values).encode("ascii")
    return _frame(b"BW", device, f"{count:02X}".encode("ascii") + data)


def build_batch_read_words(device: int, count: int) -> bytes:
    count = _validate_count(count, what="字读")
    return _frame(b"WR", device, f"{count:02X}".encode("ascii"))


def build_batch_write_words(device: int, words: list[int]) -> bytes:
    count = _validate_count(len(words), what="字写")
    data = b"".join(f"{int(w):04X}".encode("ascii") for w in words)
    for w in words:
        if not 0 <= int(w) <= 0xFFFF:
            raise ValueError(f"字数据超 16 位范围: {w}")
    return _frame(b"WW", device, f"{count:02X}".encode("ascii") + data)


def _split_frame(raw: bytes, data_len: int) -> bytes:
    """校验响应帧结构并返回数据段。"""
    if len(raw) < 5:
        raise FxProtocolError(f"FX 应答过短: {raw!r}")
    if raw[0] == NAK:
        raise FxProtocolError(f"FX NAK 错误码: {raw[1:3].decode('ascii', 'ignore')}")
    if raw[0] != STX or raw[-3] != ETX:
        raise FxProtocolError(f"FX 应答帧结构错误: {raw!r}")
    if raw[-2:].decode("ascii", "ignore").upper() != checksum(bytes(raw[:-2])):
        raise FxProtocolError("FX 应答和校验错误")
    data = raw[1:-3]
    if len(data) != data_len:
        raise FxProtocolError(f"FX 应答数据长度 {len(data)} != 期望 {data_len}")
    return data


def parse_read_bits_response(raw: bytes) -> list[bool]:
    data = _split_frame(raw, len(raw) - 4)
    if any(c not in b"01" for c in data):
        raise FxProtocolError(f"FX 位应答含非法字符: {data!r}")
    return [c == 0x31 for c in data]


def parse_read_words_response(raw: bytes) -> list[int]:
    data = _split_frame(raw, len(raw) - 4)
    if len(data) % 4:
        raise FxProtocolError(f"FX 字应答长度非 4 的倍数: {data!r}")
    try:
        return [int(data[i:i + 4], 16) for i in range(0, len(data), 4)]
    except ValueError as exc:
        raise FxProtocolError(f"FX 字应答含非法 HEX: {data!r}") from exc


def parse_write_ack(raw: bytes) -> bool:
    if len(raw) == 1 and raw[0] == ACK:
        return True
    if raw and raw[0] == NAK:
        raise FxProtocolError(f"FX NAK 错误码: {raw[1:3].decode('ascii', 'ignore')}")
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

    def _exchange(self, request: bytes, response_len: int | None) -> bytes:
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
            if response_len is None:
                return b""
            response = self._serial.read(response_len)
            if len(response) != response_len:
                raise FxProtocolError(
                    f"FX {self.port} 应答长度 {len(response)} != {response_len}")
            return response

    def ping(self) -> bool:
        """ENQ 探活：PLC 正常时回 ACK 单字节。"""
        response = self._exchange(bytes([ENQ]), 1)
        if response == bytes([ACK]):
            return True
        if response and response[0] == NAK:
            return False
        raise FxProtocolError(f"FX 探活应答异常: {response!r}")

    def read_bits(self, device: int, count: int) -> list[bool]:
        request = build_batch_read_bits(device, count)
        response = self._exchange(request, 1 + count + 3)
        return parse_read_bits_response(response)

    def write_bits(self, device: int, values: list[bool]) -> None:
        request = build_batch_write_bits(device, values)
        response = self._exchange(request, 1)
        parse_write_ack(response)

    def read_words(self, device: int, count: int) -> list[int]:
        request = build_batch_read_words(device, count)
        response = self._exchange(request, 1 + count * 4 + 3)
        return parse_read_words_response(response)

    def write_words(self, device: int, values: list[int]) -> None:
        request = build_batch_write_words(device, values)
        response = self._exchange(request, 1)
        parse_write_ack(response)
