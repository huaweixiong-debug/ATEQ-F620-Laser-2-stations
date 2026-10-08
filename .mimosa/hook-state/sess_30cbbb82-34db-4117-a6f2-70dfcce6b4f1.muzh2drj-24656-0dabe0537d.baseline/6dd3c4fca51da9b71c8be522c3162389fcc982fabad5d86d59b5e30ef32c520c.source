"""ATEQ request correlation, simulator and fail-closed serial adapter."""
from __future__ import annotations
from .models import Measurement, Result
from datetime import datetime, timezone
from dataclasses import dataclass
from threading import RLock
import time
from typing import Any

@dataclass(frozen=True)
class AteqRequest:
    station: str
    cycle_id: str
    program: str
    sequence: int
    timestamp: str

@dataclass(frozen=True)
class AteqResponse:
    request: AteqRequest
    measurement: Measurement
    raw_frame: bytes = b""

def modbus_crc16(payload: bytes) -> int:
    crc = 0xFFFF
    for byte in payload:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc

class FakeAteq:
    def __init__(self, result: Result = Result.OK, station: str = "SIM") -> None:
        self.result = result
        self.station = station
        self.connected = True
        self.program = ""
        self.requests: list[AteqRequest] = []
        self.start_count = 0

    def select_program(self, program: str) -> None:
        if not self.connected:
            raise ConnectionError("ATEQ simulator disconnected")
        self.program = program

    def start_test(self) -> None:
        """Mirror the real adapter's start command in SIMULATE mode."""
        if not self.connected:
            raise ConnectionError("ATEQ simulator disconnected")
        self.start_count += 1

    def run(self, request: AteqRequest | str = "", sequence: int = 1) -> AteqResponse:
        if not self.connected:
            raise ConnectionError("ATEQ simulator disconnected")
        if isinstance(request, str):
            request = AteqRequest(self.station, request, self.program, sequence, datetime.now(timezone.utc).isoformat())
        self.requests.append(request)
        # A simulator frame is deliberately marked; controller rejects empty,
        # cycle-id-only or mismatched raw payloads before DB advancement.
        frame = b"SIMFRAME:" + request.cycle_id.encode()
        measurement = Measurement(pressure=10.0, leakage=0.02, result=self.result, raw_frame=frame)
        return AteqResponse(request, measurement, frame)


class SerialAteq:
    """Thread-safe ATEQ F620 Modbus RTU adapter for the validated 0x30 path."""
    REALTIME_ADDRESS = 0x0030
    REALTIME_COUNT = 13
    PROGRAM_WRITE_ADDRESS = 0x0200
    # F620/G6 Modbus coil used by the ATEQ service to start a test.
    START_COIL = 0x0001
    FIFO_RESET_COIL = 0x0002
    PROGRAM_EDIT_ADDRESS = 0x6000
    TEST_FAIL_WRITE_ADDRESS = 0x603C
    TEST_FAIL_READ_ADDRESS = 0x203C
    TEST_FAIL_UNIT_ADDRESS = 0x207F
    LEAK_UNIT_CODE_ML_MIN = 51000
    # Field F620 B returned 0x8020 for the deliberately injected NG sample
    # on 2026-09-18.  It is a terminal NG result variant, not an alarm.
    FIELD_NG_STATUS = 0x8020
    UNIT_CODES = {
        0: "cm3/s", 1000: "cm3/min", 2000: "cm3/h", 3000: "mm3/s",
        6000: "Pa", 7000: "Pa(HR)", 8000: "Pa/s", 9000: "Pa/s(HR)",
        11000: "Bar", 12000: "kPa", 13000: "PSI", 14000: "mBar",
        15000: "MPa", 46000: "in3/s", 47000: "in3/min", 48000: "in3/h",
        50000: "mL/s", 51000: "mL/min", 52000: "mL/h",
    }

    def __init__(self, port: str, station: str, baudrate: int = 9600,
                 slave: int = 255, timeout_s: float = 1.0,
                 cycle_timeout_s: float = 120.0, serial_factory=None) -> None:
        if not 1 <= int(slave) <= 255:
            raise ValueError("ATEQ 从站地址必须为 1-255")
        self.port, self.station, self.baudrate = port, station, baudrate
        self.slave, self.timeout_s, self.cycle_timeout_s = int(slave), timeout_s, cycle_timeout_s
        # The PLC starts the physical tester; this adapter only observes it.
        self.external_start = True
        self.program = ""
        self.connected = False
        self._serial = None
        self._serial_factory = serial_factory
        self._lock = RLock()
        self._last_fifo: int | None = None
        self.stepcode_callback = None
        self._last_reported_stepcode: int | None = None

    def connect(self) -> None:
        with self._lock:
            if self._serial is not None and getattr(self._serial, "is_open", True):
                self.connected = True
                return
            if self._serial_factory is None:
                try:
                    import serial
                except ImportError as exc:
                    raise RuntimeError("pyserial 未安装，无法连接 ATEQ") from exc
                factory = serial.Serial
                kwargs = dict(port=self.port, baudrate=self.baudrate, bytesize=8,
                              parity=serial.PARITY_EVEN, stopbits=serial.STOPBITS_ONE,
                              timeout=self.timeout_s, write_timeout=self.timeout_s)
            else:
                factory = self._serial_factory
                kwargs = dict(port=self.port, baudrate=self.baudrate, bytesize=8,
                              parity="E", stopbits=1, timeout=self.timeout_s,
                              write_timeout=self.timeout_s)
            self._serial = factory(**kwargs)
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
        return bool(self.connected and self._serial is not None and getattr(self._serial, "is_open", True))

    @staticmethod
    def _frame(payload: bytes) -> bytes:
        return payload + modbus_crc16(payload).to_bytes(2, "little")

    def _exchange(self, payload: bytes, expected_length: int) -> bytes:
        with self._lock:
            self.connect()
            request = self._frame(payload)
            try:
                reset = getattr(self._serial, "reset_input_buffer", None)
                if reset is not None:
                    reset()
                self._serial.write(request)
                self._serial.flush()
                response = self._serial.read(expected_length)
            except Exception:
                self.close()
                raise
            if len(response) == 5 and response[0] == self.slave and response[1] == (payload[1] | 0x80):
                # Modbus 异常帧：地址+异常功能码+异常码+CRC
                if modbus_crc16(response[:-2]).to_bytes(2, "little") != response[-2:]:
                    raise RuntimeError("ATEQ Modbus 异常帧 CRC 错误")
                raise RuntimeError(f"{self._modbus_exception_text(response[2])}")
            if len(response) != expected_length:
                raise TimeoutError(f"ATEQ {self.station}/{self.port} 响应长度 {len(response)} != {expected_length}")
            if response[0] != self.slave or response[1] != payload[1]:
                raise RuntimeError("ATEQ 响应从站或功能码不匹配")
            if modbus_crc16(response[:-2]).to_bytes(2, "little") != response[-2:]:
                raise RuntimeError("ATEQ 响应 CRC 错误")
            return response

    @staticmethod
    def _modbus_exception_text(code: int) -> str:
        names = {
            1: "非法功能（仪器不支持该功能码）",
            2: "非法数据地址（寄存器不存在或不可写）",
            3: "非法数据值",
            4: "从站设备故障",
            6: "从站忙",
        }
        return (f"Modbus 异常响应 code={code}: "
                + names.get(code, "未知异常"))

    def read_registers(self, address: int, count: int) -> tuple[list[int], bytes]:
        payload = bytes([self.slave, 0x03]) + address.to_bytes(2, "big") + count.to_bytes(2, "big")
        response = self._exchange(payload, 5 + count * 2)
        if response[2] != count * 2:
            raise RuntimeError("ATEQ 寄存器字节数不匹配")
        return [int.from_bytes(response[3 + i * 2:5 + i * 2], "big") for i in range(count)], response

    @staticmethod
    def _swap16(value: int) -> int:
        return ((value & 0xFF) << 8) | ((value >> 8) & 0xFF)

    @staticmethod
    def _signed32_from_words(low_word: int, high_word: int) -> int:
        raw = ((low_word & 0xFF) << 8) | (low_word >> 8)
        raw |= (((high_word & 0xFF) << 8) | (high_word >> 8)) << 16
        return raw - 0x100000000 if raw & 0x80000000 else raw

    @classmethod
    def _unit_from_words(cls, low_word: int, high_word: int) -> str:
        value = cls._signed32_from_words(low_word, high_word)
        return cls.UNIT_CODES.get(value, f"CODE_{value}")

    def current_program(self) -> int:
        registers, _ = self.read_registers(self.REALTIME_ADDRESS, self.REALTIME_COUNT)
        return self._swap16(registers[0]) + 1

    def probe_slave(self, candidates=(1, 255)) -> int:
        """Read-only discovery used only during commissioning."""
        failures: list[str] = []
        original = self.slave
        for candidate in candidates:
            self.slave = int(candidate)
            try:
                self.read_registers(self.REALTIME_ADDRESS, 1)
                return self.slave
            except Exception as exc:
                failures.append(f"{candidate}:{type(exc).__name__}")
                self.close()
        self.slave = original
        raise ConnectionError(f"ATEQ 从站 1/255 均无响应: {', '.join(failures)}")

    def select_program(self, program: str) -> None:
        try:
            number = int(str(program).strip())
        except ValueError as exc:
            raise ValueError("ATEQ 程序号必须为 1-255 整数") from exc
        if not 1 <= number <= 255:
            raise ValueError("ATEQ 程序号必须为 1-255 整数")
        register_value = self._swap16(number - 1)
        payload = bytes([self.slave, 0x06]) + self.PROGRAM_WRITE_ADDRESS.to_bytes(2, "big") + register_value.to_bytes(2, "big")
        response = self._exchange(payload, 8)
        if response[:-2] != payload:
            raise RuntimeError("ATEQ 程序写入回显不一致")
        actual = self.current_program()
        if actual != number:
            raise RuntimeError(f"ATEQ 程序读回不一致: 期望 {number}, 实际 {actual}")
        self.program = str(number)

    def write_coil(self, address: int, value: bool) -> bytes:
        """Write one Modbus coil and validate the echoed request."""
        if not 0 <= int(address) <= 0xFFFF:
            raise ValueError("ATEQ 线圈地址无效")
        coil_value = b"\xff\x00" if value else b"\x00\x00"
        payload = bytes([self.slave, 0x05]) + int(address).to_bytes(2, "big") + coil_value
        response = self._exchange(payload, 8)
        if response[:-2] != payload:
            raise RuntimeError("ATEQ 线圈写入回显不一致")
        return response

    def start_test(self) -> None:
        """Start one ATEQ cycle; the subsequent run() monitors StepCode.

        手册要求主站必须把命令位清零，从站才能检测到下一次上升沿：
        先写启动线圈 OFF 再 ON，保证每次都有 0→1 边沿；同时先复位
        结果 FIFO（线圈 0x0002 瞬动），避免残留结果影响新周期。
        """
        self.write_coil(self.FIFO_RESET_COIL, True)
        self.write_coil(self.FIFO_RESET_COIL, False)
        # 部分 F620 固件的 FIFO 复位走寄存器命令区（@02 写 FFFF），两种都做。
        try:
            self.write_register(0x0002, 0xFFFF)
        except Exception:
            pass
        self.write_coil(self.START_COIL, False)
        time.sleep(0.1)
        self.write_coil(self.START_COIL, True)

    def write_register(self, address: int, value: int) -> None:
        """Write one holding register (fn 06) and validate the echoed request."""
        if not 0 <= int(address) <= 0xFFFF or not 0 <= int(value) <= 0xFFFF:
            raise ValueError("寄存器地址或值无效")
        payload = bytes([self.slave, 0x06]) + int(address).to_bytes(2, "big") + int(value).to_bytes(2, "big")
        response = self._exchange(payload, 8)
        if response[:-2] != payload:
            raise RuntimeError("写单寄存器回显不一致")

    def write_registers(self, address: int, values: list[int], little_endian: bool) -> None:
        """Write holding registers (fn 0x10) with the requested word byte order.

        仪器实时块已确认是低字节在先的字节序；写配置寄存器时提供与读一致的
        little_endian 编码和标准大端编码两种，由读回校验决定成败。
        """
        values = [int(v) & 0xFFFF for v in values]
        data = b"".join(
            (v.to_bytes(2, "little") if little_endian else v.to_bytes(2, "big"))
            for v in values)
        payload = (bytes([self.slave, 0x10]) + int(address).to_bytes(2, "big")
                   + len(values).to_bytes(2, "big") + bytes([len(data)]) + data)
        response = self._exchange(payload, 8)
        if (len(response) != 8 or response[0] != self.slave or response[1] != 0x10
                or response[2:6] != payload[2:6]):
            raise RuntimeError("写多寄存器回显不一致")

    def read_program_parameter(self, address: int, count: int = 2) -> int:
        """Select the program for edition, then read one parameter (long).

        手册：0x60xx 参数区读写前必须先写 0x6000 选择待编辑程序。
        """
        with self._lock:
            self.write_register(self.PROGRAM_EDIT_ADDRESS,
                                self._swap16((int(self.program or 1) - 1) & 0xFFFF))
            time.sleep(0.3)
            registers, _ = self.read_registers(address, count)
            if count >= 2:
                return self._signed32_from_words(registers[0], registers[1])
            return self._swap16(registers[0])

    def write_program_parameter(self, address: int, value_milli: int,
                                little_endian: bool = True) -> int:
        """Write one parameter (long, ×1000) with readback verification."""
        value_milli = int(value_milli)
        if not 0 <= value_milli <= 0x7FFFFFFF:
            raise ValueError("参数值无效")
        with self._lock:
            self.write_register(self.PROGRAM_EDIT_ADDRESS,
                                self._swap16((int(self.program or 1) - 1) & 0xFFFF))
            time.sleep(0.3)
            self.write_registers(address,
                                 [value_milli & 0xFFFF, (value_milli >> 16) & 0xFFFF],
                                 little_endian=little_endian)
            time.sleep(0.3)
            registers, _ = self.read_registers(address - 0x4000, 2)
            decoded = self._signed32_from_words(registers[0], registers[1])
            if decoded != value_milli:
                raise RuntimeError(
                    f"参数读回不一致 @0x{address:04X}: 写入 {value_milli}, 读回 {decoded}")
            return decoded

    def set_test_fail_limit(self, value_milli: int) -> int:
        """Set the leak limit (Test FAIL) of the selected program, then verify.

        现场规程（2026-09-12）：
        1. 写 0x6000 = 程序号-1，选择待编辑程序；
        2. 写 0x603C 两个寄存器 = 泄漏上限×1000；
        3. 读回 0x203C 两个寄存器，必须与写入值一致；
        4. 读回 0x207F 单位必须为 51000（mL/min）。
        任何读回不一致都抛错，调用方不得提示修改成功。
        绝不写启动测试线圈 0x0001。
        """
        value_milli = int(value_milli)
        if not 0 <= value_milli <= 0x7FFFFFFF:
            raise ValueError("泄漏上限值无效")
        program_no = int(self.program or 1)
        with self._lock:
            # 选择待编辑程序：优先 fn06 单寄存器写，被拒则回退 fn16。
            try:
                self.write_register(self.PROGRAM_EDIT_ADDRESS,
                                    self._swap16((program_no - 1) & 0xFFFF))
            except RuntimeError:
                self.write_registers(self.PROGRAM_EDIT_ADDRESS,
                                     [(program_no - 1) & 0xFFFF], little_endian=True)
            time.sleep(0.3)  # 参数写入后仪器需要处理间隔，紧跟着读写会掉线
            last_error: Exception | None = None
            for little_endian in (True, False):
                try:
                    self.write_registers(self.TEST_FAIL_WRITE_ADDRESS,
                                         [value_milli & 0xFFFF,
                                          (value_milli >> 16) & 0xFFFF],
                                         little_endian=little_endian)
                except Exception as exc:
                    last_error = exc
                    continue
                time.sleep(0.3)
                registers, _ = self.read_registers(self.TEST_FAIL_READ_ADDRESS, 2)
                decoded = self._signed32_from_words(registers[0], registers[1])
                if decoded != value_milli:
                    last_error = RuntimeError(
                        f"Test FAIL 读回不一致: 写入 {value_milli}, 读回 {decoded}")
                    continue
                time.sleep(0.3)
                unit_regs, _ = self.read_registers(self.TEST_FAIL_UNIT_ADDRESS, 1)
                unit = self._swap16(unit_regs[0])
                if unit != self.LEAK_UNIT_CODE_ML_MIN:
                    last_error = RuntimeError(
                        f"泄漏单位读回不一致: 期望 51000(mL/min), 读回 {unit}")
                    continue
                return decoded
            raise RuntimeError(f"泄漏上限写入未通过读回校验: {last_error}")

    def run(self, request: AteqRequest) -> AteqResponse:
        self._validate(request)
        deadline = time.monotonic() + self.cycle_timeout_s
        active_steps = {4, 5, 6}
        # F620 field traces show idle/completed StepCode 65535, while some
        # firmware/configurations report 65525. Accept either only after this
        # run has observed an active 4/5/6 step; an idle 65535 is not a result.
        completed_steps = {65525, 65535}
        started = False
        last_frame = b""
        step6_registers = None
        step_timeline: list[str] = []
        t0 = time.monotonic()
        while time.monotonic() < deadline:
            registers, raw = self.read_registers(self.REALTIME_ADDRESS, self.REALTIME_COUNT)
            last_frame = raw
            step_code = self._swap16(registers[4])
            if step_code != self._last_reported_stepcode:
                self._last_reported_stepcode = step_code
                callback = self.stepcode_callback
                if callback is not None:
                    try:
                        callback(step_code)
                    except Exception:
                        # A display observer must never interrupt the test read.
                        pass
            # 诊断时间线：记录 StepCode/状态/FIFO 的变化过程，超时时随异常
            # 抛出，用于远程判断仪器在等待期间的真实行为。
            entry = f"{time.monotonic() - t0:.1f}s step={step_code} status=0x{self._swap16(registers[3]):04X} fifo={self._swap16(registers[1])}"
            if not step_timeline or step_timeline[-1].split(" ", 1)[1] != entry.split(" ", 1)[1]:
                step_timeline.append(entry)
            # The confirmed field sequence is 4/5/6 followed by terminal 65525.
            if step_code in active_steps:
                started = True
            if step_code == 6:
                # 压力取 StepCode=6 结束时的值：保留最后一帧仍处于测试步
                # 的寄存器快照；65525 结束帧里压力寄存器已被仪器改写。
                step6_registers = list(registers)
            status = self._swap16(registers[3])
            fifo = self._swap16(registers[1])
            terminal = started and step_code in completed_steps
            # A result is valid only after this command has produced a real
            # 4/5/6 step (started-flag prevents accepting a stale terminal
            # frame from a previous cycle).  r1 是 FIFO 中的结果个数：每个
            # 周期结束都是 1，不能用它区分新旧结果。
            if terminal:
                if status & 0x0008:
                    raise RuntimeError("ATEQ 仪器报警")
                if status & 0x0010:
                    raise RuntimeError("ATEQ 压力错误")
                if status & 0x0001:
                    result = Result.OK
                elif status & 0x0006 or status == self.FIELD_NG_STATUS:
                    result = Result.NG
                else:
                    raise RuntimeError(f"ATEQ 周期结束但结果位不明确: 0x{status:04X}")
                if step6_registers is None:
                    raise RuntimeError("ATEQ 周期结束但未在 StepCode=6 期间采到压力值")
                pressure = self._signed32_from_words(step6_registers[5], step6_registers[6]) / 1000.0
                pressure_unit = self._unit_from_words(step6_registers[7], step6_registers[8])
                # 泄漏值取 StepCode 从 6 变为 65525 之后的结束帧寄存器。
                leakage = self._signed32_from_words(registers[9], registers[10]) / 1000.0
                leakage_unit = self._unit_from_words(registers[11], registers[12])
                measurement = Measurement(pressure, leakage, result, raw,
                                          pressure_unit, leakage_unit)
                return AteqResponse(request, measurement, raw)
            time.sleep(0.05)
        raise TimeoutError(
            f"ATEQ {self.station} 未在 {self.cycle_timeout_s:g} 秒内完成新周期; "
            f"timeline: {' | '.join(step_timeline[-8:])}")

    def _validate(self, request: AteqRequest) -> None:
        if request.station != self.station or request.program != self.program:
            raise ValueError("ATEQ 请求身份不一致")
        if request.sequence < 1 or not request.cycle_id:
            raise ValueError("ATEQ 请求序列或周期无效")
