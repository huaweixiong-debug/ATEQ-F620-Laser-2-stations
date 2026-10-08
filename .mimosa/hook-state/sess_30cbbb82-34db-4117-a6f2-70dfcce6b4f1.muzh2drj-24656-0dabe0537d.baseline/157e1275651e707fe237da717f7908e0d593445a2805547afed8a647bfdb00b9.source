from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from threading import Event

import pytest

from app.ateq import AteqRequest, SerialAteq
from app.barcode_rules import BarcodeInputError, BarcodeRuleEngine, route_shared_scan, scanner_code_matches
from app.barprint import write_field_files
from app.config import Settings
from app.models import Measurement, Result, StationId, TraceRecord
from app.plc import FakePlc, POINTS, signal_scan_and_wait_clear
from app.repository import PyMySQLRepository
from app.production import ProductionCoordinator
from app.models import Phase


DATE_INI = """[年方案2]
年=25,26,27,28,29,30
[月方案1]
月=01,02,03,04,05,06,07,08,09,10,11,12
[日方案2]
日=1-31
"""


def test_confirmed_plc_point_map_matches_station_table():
    expected = {
        "door_disable": {StationId.A: (0, 6), StationId.B: (0, 7)},
        "reset": {StationId.A: (0, 3), StationId.B: (0, 2)},
        "ng_sample": {StationId.A: (1, 2), StationId.B: (1, 4)},
        "ok_sample": {StationId.A: (1, 3), StationId.B: (1, 5)},
        "calibration": {StationId.A: (1, 0), StationId.B: (1, 1)},
        "start": {StationId.A: (16, 0), StationId.B: (16, 1)},
        "manual": {StationId.A: (2, 0), StationId.B: (2, 1)},
        "scan_ok": {StationId.A: (0, 1), StationId.B: (0, 0)},
        "block": {StationId.A: (4, 2), StationId.B: (3, 2)},
        "stamp": {StationId.A: (4, 6), StationId.B: (3, 6)},
        "clamp": {StationId.A: (4, 4), StationId.B: (3, 4)},
        "transfer": {StationId.A: (4, 0), StationId.B: (3, 0)},
        "pressure": {StationId.A: (0, 5), StationId.B: (0, 4)},
    }
    for signal, points in expected.items():
        assert POINTS[signal] == points
    assert POINTS["calibration_due"] == expected["calibration"]


def make_engine(tmp_path: Path) -> BarcodeRuleEngine:
    serial_a, serial_b = tmp_path / "A.txt", tmp_path / "B.txt"
    serial_a.write_text("0082", encoding="utf-8")
    serial_b.write_text("0056", encoding="utf-8")
    template_a, template_b = tmp_path / "E122015400-A.btw", tmp_path / "E122015400-B.btw"
    template_a.write_bytes(b"A"); template_b.write_bytes(b"B")
    products = tmp_path / "products.ini"
    products.write_text(f"""[E122015400]
条码规则=客户型号+日期+工位号+流水号
客户型号=E122015400
日期=年方案2+月方案1+日方案2
工位号A=8
工位号B=9
流水号A={serial_a}
流水号B={serial_b}
ATEQ程序号=7
打印模板={tmp_path / 'E122015400.btw'}
""", encoding="utf-8")
    date = tmp_path / "dates.ini"; date.write_text(DATE_INI, encoding="utf-8")
    return BarcodeRuleEngine(products, date, tmp_path / "out")


def test_fixed_qrcode_regressions_and_atomic_txt(tmp_path):
    engine = make_engine(tmp_path)
    when = datetime(2025, 12, 30, 8, 0, 0)
    a = engine.generate("E122015400", StationId.A, when)
    b = engine.generate("E122015400", StationId.B, when)
    assert a.barcode_text == "E12201540025123080082"
    assert b.barcode_text == "E12201540025123090056"
    engine.publish(a)
    assert a.output_txt_path.read_bytes() == a.barcode_text.encode("utf-8")
    assert a.product_txt_path.read_bytes() == b"E122015400"
    assert not list(a.output_txt_path.parent.glob("*.tmp"))


def test_shared_scanner_routes_exactly_one_station():
    expected = {StationId.A: "A-CODE", StationId.B: "B-CODE"}
    assert route_shared_scan("A-CODE", expected) is StationId.A
    assert route_shared_scan("B-CODE", expected) is StationId.B
    with pytest.raises(BarcodeInputError): route_shared_scan("NONE", expected)
    with pytest.raises(BarcodeInputError): route_shared_scan("A-CODE", expected, {StationId.A})
    with pytest.raises(BarcodeInputError): route_shared_scan("SAME", {StationId.A: "SAME", StationId.B: "SAME"})


def test_scanner_ignores_only_trailing_fields_after_complete_qr():
    expected = {StationId.A: "H77A1301003AA#DPPH8#2026091850002",
                StationId.B: "H77A1301003AA#DPPH8#2026091860002"}
    assert scanner_code_matches(
        "H77A1301003AA#DPPH8#2026091860002#EXTRA#IGNORED", expected[StationId.B])
    assert route_shared_scan(
        "H77A1301003AA#DPPH8#2026091860002#EXTRA", expected) is StationId.B
    assert not scanner_code_matches(
        "H77A1301003AA#DPPH8#2026091860003#EXTRA", expected[StationId.B])


def test_printer_fields_use_customer_not_part_and_no_newline(tmp_path):
    template = tmp_path / "P-A.btw"; template.write_bytes(b"template")
    record = TraceRecord(StationId.A, "0001", "QR", "PART", "OP", customer_no="CUSTOMER")
    paths = write_field_files(record, tmp_path, template)
    assert paths["customer"].read_bytes() == b"CUSTOMER"
    assert paths["part_no"].read_bytes() == b"PART"
    assert not paths["customer"].read_bytes().endswith((b"\r", b"\n"))


class AutoClearPlc(FakePlc):
    def __init__(self):
        super().__init__(); self.reads = 0

    def read_bit(self, byte, bit):
        value = super().read_bit(byte, bit)
        if value:
            self.reads += 1
            if self.reads >= 2:
                super().write_bit(byte, bit, False)
                return False
        return value


def test_plc_scan_handshake_waits_for_plc_clear_and_never_clears_itself():
    plc = AutoClearPlc()
    signal_scan_and_wait_clear(plc, StationId.B, timeout_s=0.2, poll_s=0.001)
    byte, bit = POINTS["scan_ok"][StationId.B]
    assert not plc.read_bit(byte, bit) and plc.reads >= 2
    with pytest.raises(TimeoutError): signal_scan_and_wait_clear(FakePlc(), StationId.A, timeout_s=0.01, poll_s=0.001)


class QueueSerial:
    def __init__(self, responses):
        self.responses = list(responses); self.writes = []; self.is_open = True
    def reset_input_buffer(self): pass
    def write(self, value): self.writes.append(value)
    def flush(self): pass
    def read(self, _count): return self.responses.pop(0)
    def close(self): self.is_open = False


def response(slave, function, body):
    return SerialAteq._frame(bytes([slave, function]) + body)


def realtime(slave, registers):
    body = bytes([len(registers) * 2]) + b"".join(value.to_bytes(2, "big") for value in registers)
    return response(slave, 3, body)


def test_ateq_program_is_n_minus_one_and_read_back_verified():
    slave = 255
    write_payload = bytes([slave, 6, 2, 0, 2, 0])  # program 3 -> value 2 -> byte swapped
    registers = [0x0200] + [0] * 12
    port = QueueSerial([SerialAteq._frame(write_payload), realtime(slave, registers)])
    adapter = SerialAteq("COM_TEST", "A", slave=slave, serial_factory=lambda **_: port)
    adapter.select_program("3")
    assert adapter.program == "3" and port.writes[0][:-2] == write_payload


def test_ateq_start_uses_modbus_start_coil():
    slave = 1
    start_payload = bytes([slave, 5, 0, 1, 0xFF, 0x00])
    port = QueueSerial([
        SerialAteq._frame(bytes([slave, 5, 0, 2, 0xFF, 0x00])),
        SerialAteq._frame(bytes([slave, 5, 0, 2, 0x00, 0x00])),
        SerialAteq._frame(bytes([slave, 6, 0, 2, 0xFF, 0xFF])),
        SerialAteq._frame(bytes([slave, 5, 0, 1, 0x00, 0x00])),
        SerialAteq._frame(start_payload),
    ])
    adapter = SerialAteq("COM_TEST", "B", slave=slave,
                         serial_factory=lambda **_: port)
    adapter.start_test()
    # 手册要求命令位用后清零：FIFO 复位线圈瞬动 + 启动线圈先 OFF 再 ON
    assert port.writes[0][:-2] == bytes([slave, 5, 0, 2, 0xFF, 0x00])
    assert port.writes[1][:-2] == bytes([slave, 5, 0, 2, 0x00, 0x00])
    # FIFO 复位兼容寄存器写（@02 = FFFF），失败被忽略
    assert port.writes[2][:-2] == bytes([slave, 6, 0, 2, 0xFF, 0xFF])
    assert port.writes[3][:-2] == bytes([slave, 5, 0, 1, 0x00, 0x00])
    assert port.writes[4][:-2] == start_payload


def test_ateq_monitors_stepcode_sequence_until_65525():
    slave = 1
    started = [0, 0, 0, 0, 0x0400] + [0] * 8  # StepCode 4
    middle = [0, 0, 0, 0, 0x0500] + [0] * 8   # StepCode 5
    testing = [0, 0, 0, 0, 0x0600] + [0] * 8
    # Field trace: StepCode 65535 is 0xFFFF on the wire; pre-start idle
    # 65535 cannot terminate this transaction because no active step preceded it.
    ended = [0, 0, 0, 0x2100, 0xFFFF] + [0] * 8
    port = QueueSerial([realtime(slave, value) for value in (started, middle, testing, ended)])
    adapter = SerialAteq("COM_TEST", "A", slave=slave, cycle_timeout_s=0.2,
                         serial_factory=lambda **_: port)
    adapter.program = "3"
    request = AteqRequest("A", "A-cycle", "3", 1, "now")
    result = adapter.run(request)
    assert result.measurement.result is Result.OK and result.request is request
    assert all(request[1] == 0x03 for request in port.writes)


class FakeCursor:
    def __init__(self): self.sql = []; self.lastrowid = 41; self.rowcount = 1
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def execute(self, sql, params=()): self.sql.append((sql, params))


class FakeConnection:
    def __init__(self): self.cursor_instance = FakeCursor()
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def cursor(self): return self.cursor_instance
    def commit(self): pass
    def rollback(self): pass


def test_mysql_repository_writes_only_station_table(monkeypatch):
    repo = PyMySQLRepository(host="127.0.0.1", user="u", password="p", database="test")
    repo._schema_verified = True
    connection = FakeConnection()
    monkeypatch.setattr(repo, "_connect", lambda: connection)
    record = TraceRecord(StationId.B, "0001", "QR-B", "PART", "OP",
                         first=Measurement(1, 2, Result.NG, b"raw"), cycle_id="B-cycle",
                         test_mode="dual")
    repo.insert_stage1(record)
    assert "`info_B`" in connection.cursor_instance.sql[0][0]
    assert "`info_A`" not in connection.cursor_instance.sql[0][0]
    assert repo.row_id("B-cycle") == 41


class DummyController:
    def __init__(self):
        self.phase = Phase.IDLE
        self.selection = None
        self.error = ""
        self.first_calls = 0
        self.second_calls = 0
    def scan_selection(self, selection): self.selection = selection; self.phase = Phase.READY
    def reset(self): self.phase = Phase.IDLE
    def fault(self, reason): self.phase = Phase.FAULT; self.error = reason
    def test_first(self):
        self.first_calls += 1
        self.phase = Phase.WAIT_2 if self.selection.test_mode == "dual" else Phase.LABELING
        return "first"
    def test_second(self):
        self.second_calls += 1
        self.phase = Phase.LABELING
        return "second"


class BlockingController(DummyController):
    def __init__(self):
        super().__init__()
        self.started = Event()
        self.release = Event()

    def test_first(self):
        self.started.set()
        if not self.release.wait(1.0):
            raise TimeoutError("测试控制器未释放")
        return super().test_first()


class LoggingHandshakePlc(FakePlc):
    """Clear only scan-OK bits; keep PLC-owned M16 start levels observable."""
    def __init__(self):
        super().__init__()
        self.writes = []
        self._scan_reads = {}

    def write_bit(self, byte, bit, value):
        self.writes.append((byte, bit, value))
        super().write_bit(byte, bit, value)

    def read_bit(self, byte, bit):
        value = super().read_bit(byte, bit)
        if (byte, bit) in POINTS["scan_ok"].values() and value:
            key = (byte, bit)
            self._scan_reads[key] = self._scan_reads.get(key, 0) + 1
            if self._scan_reads[key] >= 2:
                super().write_bit(byte, bit, False)
                return False
        return value


def test_production_coordinator_keeps_station_models_independent(tmp_path):
    engine = make_engine(tmp_path)
    controllers = {station: DummyController() for station in StationId}
    coordinator = ProductionCoordinator(engine, controllers, AutoClearPlc(), handshake_timeout_s=0.2)
    a = coordinator.configure_station(StationId.A, "E122015400", "OP-A", "single")
    b = coordinator.configure_station(StationId.B, "E122015400", "OP-B", "dual")
    assert a.barcode_text != b.barcode_text
    assert coordinator.handle_scan(b.barcode_text) is StationId.B
    assert controllers[StationId.A].selection is None
    selection = controllers[StationId.B].selection
    assert selection.person == "OP-B" and selection.test_mode == "dual" and selection.serial_no == "0056"


def test_plc_start_is_read_only_rising_edge_and_dispatches_a_b_independently(tmp_path):
    engine = make_engine(tmp_path)
    controllers = {station: DummyController() for station in StationId}
    plc = LoggingHandshakePlc()
    coordinator = ProductionCoordinator(engine, controllers, plc, handshake_timeout_s=0.2)
    try:
        coordinator.configure_station(StationId.A, "E122015400", "OP-A", "dual")
        coordinator.configure_station(StationId.B, "E122015400", "OP-B", "single")
        coordinator.handle_scan(coordinator.payloads[StationId.A].barcode_text)
        coordinator.handle_scan(coordinator.payloads[StationId.B].barcode_text)

        # Initial sampling primes the detector; a high level at startup is
        # intentionally not treated as a command.
        assert coordinator.poll_plc_start_edges() == ()
        plc.write_bit(*POINTS["start"][StationId.A], True)
        plc.write_bit(*POINTS["start"][StationId.B], True)
        start_writes_after_plc_command = [write for write in plc.writes if write[0] == 16]
        assert set(coordinator.poll_plc_start_edges()) == {StationId.A, StationId.B}
        assert coordinator.wait_for_plc_start(StationId.A) == "first"
        assert coordinator.wait_for_plc_start(StationId.B) == "first"
        assert controllers[StationId.A].first_calls == 1
        assert controllers[StationId.B].first_calls == 1
        assert controllers[StationId.A].phase is Phase.WAIT_2
        assert controllers[StationId.B].phase is Phase.LABELING

        # A high level is not retriggered, and the coordinator never writes
        # the PLC-owned M16 start bits.
        assert coordinator.poll_plc_start_edges() == ()
        assert [write for write in plc.writes if write[0] == 16] == start_writes_after_plc_command

        # A's next false -> true edge starts its second test.  B remains
        # complete and is not affected by A's second edge.
        plc.write_bit(*POINTS["start"][StationId.A], False)
        coordinator.poll_plc_start_edges()
        plc.write_bit(*POINTS["start"][StationId.A], True)
        assert coordinator.poll_plc_start_edges() == (StationId.A,)
        assert coordinator.wait_for_plc_start(StationId.A) == "second"
        assert controllers[StationId.A].second_calls == 1
        assert controllers[StationId.B].second_calls == 0
    finally:
        coordinator.close()


def test_plc_start_workers_do_not_serialize_ateq_waits(tmp_path):
    engine = make_engine(tmp_path)
    controllers = {StationId.A: BlockingController(), StationId.B: DummyController()}
    plc = LoggingHandshakePlc()
    coordinator = ProductionCoordinator(engine, controllers, plc, handshake_timeout_s=0.2)
    try:
        coordinator.configure_station(StationId.A, "E122015400", "OP-A", "single")
        coordinator.configure_station(StationId.B, "E122015400", "OP-B", "single")
        coordinator.handle_scan(coordinator.payloads[StationId.A].barcode_text)
        coordinator.handle_scan(coordinator.payloads[StationId.B].barcode_text)
        assert coordinator.poll_plc_start_edges() == ()
        plc.write_bit(*POINTS["start"][StationId.A], True)
        plc.write_bit(*POINTS["start"][StationId.B], True)
        assert set(coordinator.poll_plc_start_edges()) == {StationId.A, StationId.B}
        assert controllers[StationId.A].started.wait(1.0)
        # B can finish while A is still blocked in its independent worker.
        assert coordinator.wait_for_plc_start(StationId.B, timeout=0.5) == "first"
        assert controllers[StationId.B].first_calls == 1
        assert controllers[StationId.A].first_calls == 0
        controllers[StationId.A].release.set()
        assert coordinator.wait_for_plc_start(StationId.A, timeout=0.5) == "first"
    finally:
        controllers[StationId.A].release.set()
        coordinator.close()


def test_advance_serial_increments_and_resets_daily(tmp_path):
    from datetime import datetime as _dt
    engine = make_engine(tmp_path)
    day1 = _dt(2025, 12, 30, 8, 0, 0)
    # 周期消耗当前流水号后推进：0056 -> 0057
    assert engine.advance_serial("E122015400", StationId.B, day1) == "0057"
    assert (tmp_path / "B.txt").read_text(encoding="utf-8") == "0057"
    # 同日继续递增
    assert engine.advance_serial("E122015400", StationId.B, day1) == "0058"
    # 跨日归零：新一天第一个周期从 0001 开始
    day2 = _dt(2025, 12, 31, 7, 59, 0)
    assert engine.advance_serial("E122015400", StationId.B, day2) == "0001"
    assert (tmp_path / "B.txt").read_text(encoding="utf-8") == "0001"
    # A 工位独立计数
    assert engine.advance_serial("E122015400", StationId.A, day1) == "0083"


def test_advance_serial_never_rewinds_behind_frozen_cycle(tmp_path):
    from datetime import datetime as _dt
    engine = make_engine(tmp_path)
    day = _dt(2025, 12, 30, 8, 0, 0)
    # A stale counter on disk must not turn a frozen 0057 cycle into another
    # 0057 reservation.  The next payload must be 0058 or later.
    assert engine.advance_serial(
        "E122015400", StationId.B, day, consumed_serial="0057") == "0058"
    assert (tmp_path / "B.txt").read_text(encoding="utf-8") == "0058"
    # Once the file is ahead, a late/stale payload cannot move it backwards.
    assert engine.advance_serial(
        "E122015400", StationId.B, day, consumed_serial="0057") == "0059"


def test_calibration_serial_c_prefix_independent_and_daily(tmp_path):
    from datetime import datetime as _dt
    engine = make_engine(tmp_path)
    day1 = _dt(2025, 12, 30, 8, 0, 0)
    # 周期创建只取号不递增：重试复用同一号
    assert engine.current_calibration_serial("E122015400", StationId.B, day1) == "C001"
    assert engine.current_calibration_serial("E122015400", StationId.B, day1) == "C001"
    # 样件验证通过后递增：C001 -> C002
    assert engine.advance_calibration_serial("E122015400", StationId.B, day1) == "C002"
    assert engine.current_calibration_serial("E122015400", StationId.B, day1) == "C002"
    # 校准计数独立于正常测试件计数
    assert (tmp_path / "B.txt").read_text(encoding="utf-8") == "0056"
    # 跨日归零
    assert engine.current_calibration_serial(
        "E122015400", StationId.B, _dt(2025, 12, 31, 7, 0, 0)) == "C001"
    payload = engine.generate_calibration("E122015400", StationId.B,
                                          _dt(2025, 12, 31, 8, 0, 0))
    assert payload.serial_no == "C001"
    assert payload.barcode_text.endswith("C001")
    assert (tmp_path / "out" / "二维码B.txt").read_text(encoding="utf-8") == payload.barcode_text
