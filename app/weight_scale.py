"""称重 Modbus RTU 适配器 + 重量转发服务（240429 专配）。

老链路：NI OPC Servers "modbus" 串口通道（COM6）读 40002 -> LabVIEW
共享变量 "40002" -> 写 PLC D0900（实时重量 = 不合格品箱上的产品重量）。
新链路：WeightScale 直读 COM6（Modbus RTU fn03），WeightService 周期
把重量写进 PLC 字寄存器（默认 D900）。

寄存器约定沿用 4xxxx 台账写法：40002 -> 协议地址 1（=40002-40001）。
"""
from __future__ import annotations
from threading import Lock, RLock, Thread
import time

from .ateq import modbus_crc16


def _register_address(register: int) -> int:
    """4xxxx 台账地址 -> 0 基协议地址。"""
    register = int(register)
    if not 40001 <= register <= 465535:
        raise ValueError(f"称重寄存器必须为 4xxxx 台账地址: {register}")
    return register - 40001


def modbus_read_request(slave: int, address: int, count: int) -> tuple[bytes, int]:
    """fn03 读保持寄存器请求帧及其期望响应长度。"""
    payload = bytes([int(slave), 0x03]) + int(address).to_bytes(2, "big") + int(count).to_bytes(2, "big")
    return payload + modbus_crc16(payload).to_bytes(2, "little"), 5 + count * 2


def parse_read_holding(response: bytes, *, count: int, slave: int) -> list[int]:
    if len(response) != 5 + count * 2:
        raise ValueError(f"称重响应长度 {len(response)} != {5 + count * 2}")
    if response[0] != slave or response[1] != 0x03:
        raise ValueError("称重响应从站/功能码不匹配")
    if response[2] != count * 2:
        raise ValueError("称重响应字节数不匹配")
    if modbus_crc16(response[:-2]).to_bytes(2, "little") != response[-2:]:
        raise ValueError("称重响应 CRC 错误")
    return [int.from_bytes(response[3 + i * 2:5 + i * 2], "big") for i in range(count)]


class WeightScale:
    """线程安全称重 Modbus RTU 客户端（pyserial，可注入 serial_factory）。"""

    def __init__(self, port: str, *, slave: int = 1, register: int = 40002,
                 count: int = 1, baudrate: int = 9600, parity: str = "N",
                 bytesize: int = 8, stopbits: int = 1, timeout_s: float = 1.0,
                 serial_factory=None) -> None:
        self.port = port
        self.slave, self.count = int(slave), int(count)
        self.address = _register_address(register)
        self.baudrate, self.parity = int(baudrate), str(parity).upper()
        self.bytesize, self.stopbits = int(bytesize), int(stopbits)
        self.timeout_s = float(timeout_s)
        self.connected = False
        self._serial = None
        self._serial_factory = serial_factory
        self._lock = RLock()  # read_registers 持锁调 connect()，必须可重入

    def connect(self) -> None:
        with self._lock:
            if self._serial is not None and getattr(self._serial, "is_open", True):
                self.connected = True
                return
            if self._serial_factory is None:
                try:
                    import serial
                except ImportError as exc:
                    raise RuntimeError("pyserial 未安装，无法连接称重") from exc
                self._serial = serial.Serial(
                    port=self.port, baudrate=self.baudrate, bytesize=self.bytesize,
                    parity=self.parity, stopbits=self.stopbits, timeout=self.timeout_s,
                    write_timeout=self.timeout_s)
            else:
                self._serial = self._serial_factory(
                    port=self.port, baudrate=self.baudrate, bytesize=self.bytesize,
                    parity=self.parity, stopbits=self.stopbits, timeout=self.timeout_s,
                    write_timeout=self.timeout_s)
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
            self.read_raw()
            return True
        except Exception:
            self.close()
            return False

    def read_raw(self) -> int:
        """读单个原始寄存器值（无符号 16 位）。"""
        return self.read_registers(self.count)[0]

    def read_registers(self, count: int | None = None) -> list[int]:
        count = self.count if count is None else int(count)
        with self._lock:
            self.connect()
            request, expected = modbus_read_request(self.slave, self.address, count)
            try:
                reset = getattr(self._serial, "reset_input_buffer", None)
                if reset is not None:
                    reset()
                self._serial.write(request)
                self._serial.flush()
                response = self._serial.read(expected)
            except Exception:
                self.close()
                raise
        return parse_read_holding(response, count=count, slave=self.slave)


class WeightService:
    """称重 -> PLC 字寄存器转发（老程序把 40002 写 D900 的等价实现）。

    - 写走 PLC 适配器的 write_word，受 enable_writes 门禁约束
      （writes_enabled=False 时 read_and_write_once 直接抛 PermissionError）。
    - 读/写失败仅记录计数，不中断线程：称重掉线不能影响检测主流程。
    """

    def __init__(self, scale: WeightScale, plc, *, plc_register: int,
                 poll_s: float = 0.5, writes_enabled: bool = False) -> None:
        self.scale = scale
        self.plc = plc
        self.plc_register = int(plc_register)
        self.poll_s = max(0.01, float(poll_s))
        self.writes_enabled = bool(writes_enabled)
        self.last_value: int | None = None
        self.error_count = 0
        self.last_error = ""
        self._thread: Thread | None = None
        self._run = False

    def enable_writes(self, approved: bool) -> None:
        self.writes_enabled = bool(approved)

    def read_and_write_once(self) -> int:
        value = self.scale.read_raw()
        if not self.writes_enabled:
            raise PermissionError("称重写 PLC 被 capability policy 拒绝")
        self.plc.write_word(self.plc_register, value)
        self.last_value = value
        return value

    def _loop(self) -> None:
        while self._run:
            try:
                self.read_and_write_once()
            except Exception as exc:
                self.error_count += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(self.poll_s)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._run = True
        self._thread = Thread(target=self._loop, name="weight-service", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._run = False
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
