import pytest

from app.repository import FakeRepository, PyMySQLRepository
from app.models import Measurement, Result, StationId, TraceRecord

from tests.helpers import make_selection, make_security

V2_DDL = """CREATE TABLE `info_A` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `cycle_id` VARCHAR(80) NOT NULL,
  `Time` DATETIME(6) NOT NULL,
  `Part No.` VARCHAR(80) NOT NULL,
  `Result` VARCHAR(16) NOT NULL,
  `Person` VARCHAR(80) NOT NULL,
  `marked` TINYINT(1) NOT NULL DEFAULT 0,
  `Mark Time` DATETIME(6) NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_info_A_cycle_id` (`cycle_id`)
) ENGINE=InnoDB"""

V1_DDL = """CREATE TABLE `info_A` (
  `id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  `cycle_id` VARCHAR(80) NOT NULL,
  `Time` DATETIME(6) NOT NULL,
  `Serial No.` VARCHAR(80) NOT NULL,
  `2D Code` VARCHAR(255) NOT NULL,
  `Result` VARCHAR(16) NOT NULL,
  `Person` VARCHAR(80) NOT NULL,
  `labeled` TINYINT(1) NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uq_info_A_cycle_id` (`cycle_id`)
) ENGINE=InnoDB"""


def make_repo() -> FakeRepository:
    from app.config import Settings
    return FakeRepository(Settings())


def make_record(mode="dual", first=Result.OK, second=Result.OK) -> TraceRecord:
    from datetime import datetime, timezone
    record = TraceRecord(station=StationId.A, part_no="P1", person="OP",
                         cycle_id="A-1", test_mode=mode,
                         created_at=datetime.now(timezone.utc))
    record.first = Measurement(1.0, 0.1, first, b"F")
    if mode == "dual":
        record.second = Measurement(2.0, 0.2, second, b"F")
    return record


def test_fake_insert_get_mark_query():
    repo = make_repo()
    record = make_record()
    repo.insert_stage1(record)
    assert repo.row_id("A-1") is not None
    fetched = repo.get("A-1")
    assert fetched.cycle_id == "A-1"
    assert fetched is not record  # 返回深拷贝，打码内容以库内记录为准
    repo.mark_marked("A-1")
    assert repo.get("A-1").marked is True
    assert repo.query("P1")
    assert repo.query("OP")
    assert not repo.query("不存在的型号")


def test_result_both_ok_semantics():
    """结果=OK 当且仅当已测阶段全部 OK（正负压双合格）。"""
    assert PyMySQLRepository._result(make_record(first=Result.OK, second=Result.OK)) == "OK"
    assert PyMySQLRepository._result(make_record(first=Result.NG, second=Result.OK)) == "NG"
    assert PyMySQLRepository._result(make_record(first=Result.OK, second=Result.NG)) == "NG"
    assert PyMySQLRepository._result(make_record(mode="single", first=Result.OK)) == "OK"


def test_verify_schema_accepts_v2():
    repo = PyMySQLRepository(host="127.0.0.1", user="u", password="p", database="test")
    repo.verify_schema({"info_A": V2_DDL, "info_B": V2_DDL})
    assert repo._schema_verified is True


def test_verify_schema_rejects_v1_scan_schema():
    repo = PyMySQLRepository(host="127.0.0.1", user="u", password="p", database="test")
    with pytest.raises(RuntimeError, match="扫码版表结构"):
        repo.verify_schema({"info_A": V1_DDL, "info_B": V1_DDL})


def test_verify_schema_rejects_missing_tables():
    repo = PyMySQLRepository(host="127.0.0.1", user="u", password="p", database="test")
    with pytest.raises(RuntimeError, match="缺少 info_A/info_B"):
        repo.verify_schema({"info_A": V2_DDL})


def test_update_stage2_fallback_without_pending():
    """防御分支：第一测直接落库（无 pending）时也能合并第二次结果。"""
    repo = make_repo()
    record = make_record(first=Result.NG)
    record.second = None
    repo.insert_stage1(record)
    repo.update_stage2("A-1", Measurement(2.0, 0.2, Result.OK, b"F"))
    merged = repo.get("A-1")
    assert merged.second is not None and merged.second.pressure == 2.0


def test_mark_marked_requires_existing_row():
    repo = make_repo()
    with pytest.raises(KeyError):
        repo.mark_marked("missing")


def test_repository_port_methods_complete():
    """控制器依赖的方法在两种实现上都存在（contracts 一致性）。"""
    from app.composition import ReadOnlyRepository
    from app.contracts import RepositoryPort
    repo = make_repo()
    for method in ("insert_stage1", "update_stage2", "row_id", "get",
                   "get_committed", "mark_marked", "query"):
        assert hasattr(repo, method)
        assert hasattr(PyMySQLRepository, method)
        assert hasattr(ReadOnlyRepository(), method)
    assert RepositoryPort is not None


class _FakeCursor:
    def __init__(self, row=None):
        self.row = row
        self.executed = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.row


class _FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._cursor


def _live_repo():
    repo = PyMySQLRepository(host="127.0.0.1", user="u", password="p", database="test")
    repo._schema_verified = True
    return repo


def _committed_row(cycle_id="A-1", result="OK", second=True):
    from datetime import datetime
    return (datetime(2026, 10, 1, 12, 0, 0), "DB-PART", "DB-OP",
            111.0, "kPa", 1.1, "ml/min",
            222.0 if second else None, "kPa" if second else "",
            2.2 if second else None, "ml/min" if second else "",
            result, 0, None, cycle_id)


def test_get_committed_bypasses_cache_and_selects_row():
    repo = _live_repo()
    cached = make_record()
    cached.part_no = "CACHED"
    repo.records["A-1"] = cached
    cursor = _FakeCursor(_committed_row())
    calls = []

    def fake_connect():
        calls.append(True)
        return _FakeConnection(cursor)

    repo._connect = fake_connect
    assert repo.get("A-1").part_no == "CACHED"
    assert calls == []  # cache hit never opens a connection
    committed = repo.get_committed("A-1")
    assert calls == [True]
    assert committed.part_no == "DB-PART" and committed.person == "DB-OP"
    assert committed.first.pressure == 111.0 and committed.second.pressure == 222.0
    assert committed.test_mode == "dual" and committed.date_scheme == "YYYYMMDD"
    assert "WHERE `cycle_id`=%s" in cursor.executed[0][0]
    assert cursor.executed[0][1] == ("A-1",)
    assert repo.records["A-1"].part_no == "CACHED"  # cache untouched


def test_row_to_record_defaults_unpersisted_fields():
    record = PyMySQLRepository._row_to_record("info_A", _committed_row(second=False))
    assert record.test_mode == "dual"
    assert record.date_scheme == "YYYYMMDD"
    assert record.ateq_program == ""
    assert record.sample_cycle is False
    assert record.first.raw_frame == b"DB"
    assert record.second is None


def test_get_committed_requires_verified_schema_before_connect():
    repo = PyMySQLRepository(host="127.0.0.1", user="u", password="p", database="test")
    called = []
    repo._connect = lambda: called.append(True)
    with pytest.raises(RuntimeError, match="LIVE_BLOCKED"):
        repo.get_committed("A-1")
    assert called == []


def test_get_committed_propagates_connection_error():
    pymysql = pytest.importorskip("pymysql")
    repo = _live_repo()

    def explode():
        raise pymysql.err.OperationalError(2003, "can't connect")

    repo._connect = explode
    with pytest.raises(pymysql.err.OperationalError):
        repo.get_committed("A-1")


def test_get_committed_propagates_select_error():
    pymysql = pytest.importorskip("pymysql")
    repo = _live_repo()

    class _BadCursor(_FakeCursor):
        def execute(self, sql, params=None):
            raise pymysql.err.ProgrammingError(1064, "bad sql")

    repo._connect = lambda: _FakeConnection(_BadCursor())
    with pytest.raises(pymysql.err.ProgrammingError):
        repo.get_committed("A-1")


def test_get_committed_missing_row_returns_none():
    repo = _live_repo()
    cursor = _FakeCursor(None)
    repo._connect = lambda: _FakeConnection(cursor)
    assert repo.get_committed("A-1") is None
    assert "WHERE `cycle_id`=%s" in cursor.executed[0][0]
