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


def test_mark_marked_requires_existing_row():
    repo = make_repo()
    with pytest.raises(KeyError):
        repo.mark_marked("missing")


def test_repository_port_methods_complete():
    """控制器依赖的方法在两种实现上都存在（contracts 一致性）。"""
    from app.contracts import RepositoryPort
    repo = make_repo()
    for method in ("insert_stage1", "update_stage2", "row_id", "get",
                   "mark_marked", "query"):
        assert hasattr(repo, method)
        assert hasattr(PyMySQLRepository, method)
    assert RepositoryPort is not None
