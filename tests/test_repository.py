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
    from app.contracts import RepositoryPort
    repo = make_repo()
    for method in ("insert_stage1", "update_stage2", "row_id", "get",
                   "mark_marked", "query", "get_committed"):
        assert hasattr(repo, method)
        assert hasattr(PyMySQLRepository, method)
    assert hasattr(RepositoryPort, "get_committed")
    assert RepositoryPort is not None


# ------------------------------------------------------- TC38-TC42 committed

class FakeCursor:
    def __init__(self, results):
        self.results = list(results)
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self.results.pop(0) if self.results else None

    def fetchall(self):
        values, self.results = self.results, []
        return values

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeConnection:
    def __init__(self, cursor):
        self._cursor = cursor
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self._cursor

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def db_row(cycle_id="A-DB-1", part="P-DB", person="OP-DB", result="OK", marked=0,
           mark_time=None, created=None):
    from datetime import datetime
    return (created or datetime(2026, 10, 7, 1, 2, 3), part, person,
            500.0, "Kpa", 0.1, "ml/min", -45.0, "Kpa", 0.2, "ml/min",
            result, marked, mark_time, cycle_id)


def make_mysql_repo() -> PyMySQLRepository:
    repo = PyMySQLRepository(host="127.0.0.1", user="u", password="p", database="test")
    repo.verify_schema({"info_A": V2_DDL, "info_B": V2_DDL})
    return repo


def test_tc38_mysql_get_committed_selects_db_and_ignores_cache(monkeypatch):
    repo = make_mysql_repo()
    repo.records["A-DB-1"] = make_record()  # polluted UI cache must not be used
    cursor = FakeCursor([db_row(cycle_id="A-DB-1", part="FROM-DB"), None])
    connection = FakeConnection(cursor)
    monkeypatch.setattr(repo, "_connect", lambda: connection)
    record = repo.get_committed("A-DB-1")
    assert record is not None
    assert record.part_no == "FROM-DB"
    assert record.cycle_id == "A-DB-1"
    assert len(cursor.executed) == 2  # unknown mapping: both tables searched
    for sql, params in cursor.executed:
        assert sql.lstrip().upper().startswith("SELECT")
        assert params == ("A-DB-1",)
    assert "records" not in cursor.executed[0][0]


def test_tc38_mysql_get_committed_uses_known_table_mapping(monkeypatch):
    repo = make_mysql_repo()
    repo._cycle_station["A-DB-1"] = "info_A"
    cursor = FakeCursor([db_row(cycle_id="A-DB-1", part="MAPPED")])
    monkeypatch.setattr(repo, "_connect", lambda: FakeConnection(cursor))
    assert repo.get_committed("A-DB-1").part_no == "MAPPED"
    assert len(cursor.executed) == 1
    assert "info_A" in cursor.executed[0][0]


def test_tc39_unknown_mapping_only_station_b_row_is_returned(monkeypatch):
    repo = make_mysql_repo()
    cursor = FakeCursor([None, db_row(cycle_id="B-X", part="B-PART")])
    monkeypatch.setattr(repo, "_connect", lambda: FakeConnection(cursor))
    record = repo.get_committed("B-X")
    assert record is not None
    assert record.station is StationId.B
    assert record.part_no == "B-PART"
    assert any("info_A" in sql for sql, _ in cursor.executed)
    assert any("info_B" in sql for sql, _ in cursor.executed)


def test_tc40_duplicate_cycle_identity_across_tables_rejected(monkeypatch):
    repo = make_mysql_repo()
    cursor = FakeCursor([db_row(cycle_id="DUP-1"), db_row(cycle_id="DUP-1")])
    monkeypatch.setattr(repo, "_connect", lambda: FakeConnection(cursor))
    with pytest.raises(RuntimeError, match="歧义"):
        repo.get_committed("DUP-1")


def test_tc41_fake_committed_snapshot_isolated_from_process_record():
    repo = make_repo()
    record = make_record(mode="single", first=Result.OK)
    record.second = None
    repo.insert_stage1(record)
    snapshot = repo.get_committed("A-1")
    assert snapshot.part_no == "P1"
    # Mutating the live process record after insert must not change the snapshot.
    record.part_no = "CHANGED"
    record.first = Measurement(99.0, 9.9, Result.OK, b"X")
    again = repo.get_committed("A-1")
    assert again.part_no == "P1"
    assert again.first.pressure == 1.0
    # The returned object is a deep copy: mutating it cannot pollute the store.
    again.part_no = "MUTATED"
    assert repo.get_committed("A-1").part_no == "P1"


def test_tc42_fake_committed_updates_only_on_explicit_commits():
    repo = make_repo()
    record = make_record(mode="dual")
    record.second = None
    repo.insert_stage1(record)
    assert repo.get_committed("A-1").second is None
    repo.update_stage2("A-1", Measurement(2.0, 0.2, Result.OK, b"F"))
    committed = repo.get_committed("A-1")
    assert committed.second is not None and committed.second.pressure == 2.0
    assert committed.marked is False
    repo.mark_marked("A-1")
    assert repo.get_committed("A-1").marked is True
    assert repo.get("A-1").marked is True


def test_fake_committed_missing_cycle_returns_none():
    assert make_repo().get_committed("missing") is None
