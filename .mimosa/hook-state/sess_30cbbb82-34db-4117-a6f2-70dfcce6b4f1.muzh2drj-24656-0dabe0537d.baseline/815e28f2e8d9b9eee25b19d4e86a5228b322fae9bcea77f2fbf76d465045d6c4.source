"""Idempotent in-memory repository and live-write gate."""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime, timezone
from threading import Lock, RLock
from .config import Settings, LiveCapability
from .models import Measurement, Result, StationId, TraceRecord
from pathlib import Path

class FakeRepository:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.records: dict[str, TraceRecord] = {}
        self._lock = Lock()
        self._row_ids: dict[str, int] = {}
        self._next_row_id = 1

    def insert_stage1(self, record: TraceRecord, capability: LiveCapability | None = None) -> TraceRecord:
        if self.settings.mode.value != "simulate":
            raise PermissionError("database writes are disabled outside approved live mode")
        with self._lock:
            if record.cycle_id not in self.records:
                self.records[record.cycle_id] = record
                self._row_ids[record.cycle_id] = self._next_row_id
                self._next_row_id += 1
            return self.records[record.cycle_id]

    def row_id(self, cycle_id: str) -> int | None:
        return self._row_ids.get(cycle_id)

    def update_stage2(self, cycle_id: str, measurement: Measurement, capability: LiveCapability | None = None) -> TraceRecord:
        if self.settings.mode.value != "simulate":
            raise PermissionError("database writes are disabled outside approved live mode")
        with self._lock:
            record = self.records[cycle_id]
            record.second = measurement
            return record

    def mark_labeled(self, cycle_id: str, capability: LiveCapability | None = None) -> None:
        if self.settings.mode.value != "simulate":
            raise PermissionError("database writes are disabled outside approved live mode")
        with self._lock:
            self.records[cycle_id].labeled = True

    def count_ok(self) -> int:
        return sum(1 for row in self.records.values() if row.second and row.second.result is Result.OK)

    def query(self, text: str = "") -> list[TraceRecord]:
        needle = text.strip().lower()
        return [row for row in self.records.values() if not needle or needle in row.code_2d.lower() or needle in row.serial_no.lower()]


class PyMySQLRepository:
    """Idempotent production repository using one table per physical station."""
    def __init__(self, *, host: str, user: str, password: str, database: str, port: int = 3306) -> None:
        if not host or not user or not database or not 1 <= port <= 65535:
            raise ValueError("MySQL 配置无效")
        self.host = host
        self.user = user
        self.password = password
        self.database = database
        self.port = port
        self._schema_verified = False
        self._pending: dict[str, TraceRecord] = {}
        # UI compatibility cache: live records remain persisted in MySQL;
        # this cache contains records written during the current process.
        self.records: dict[str, TraceRecord] = {}
        self._cycle_station: dict[str, str] = {}
        self._row_ids: dict[str, int] = {}
        self._lock = RLock()

    def verify_schema(self, show_create_results: dict[str, str]) -> None:
        required = {"info_A", "info_B"}
        if not required.issubset(show_create_results):
            raise RuntimeError("LIVE_BLOCKED: 缺少 info_A/info_B 表结构证据")
        normalized = {name: ddl.upper() for name, ddl in show_create_results.items() if name in required}
        if not all("PRIMARY KEY" in value and "UNIQUE" in value and "CYCLE_ID" in value
                   for value in normalized.values()):
            raise RuntimeError("LIVE_BLOCKED: info_A/info_B 缺少主键或 cycle_id 唯一键")
        self._schema_verified = True

    def _require_verified(self) -> None:
        if not self._schema_verified:
            raise RuntimeError("LIVE_BLOCKED: MySQL schema 未通过 SHOW CREATE TABLE 校验")

    def connect_and_verify(self) -> None:
        with self._connect() as connection:
            with connection.cursor() as cursor:
                results = {}
                for table in ("info_A", "info_B"):
                    cursor.execute(f"SHOW CREATE TABLE `{table}`")
                    row = cursor.fetchone()
                    results[table] = row[1]
        self.verify_schema(results)
        # Rehydrate the test-page cache after an application restart.
        self.records = {record.cycle_id: record for record in self.query()}

    def _connect(self):
        try:
            import pymysql
        except ImportError as exc:
            raise RuntimeError("PyMySQL 未安装") from exc
        return pymysql.connect(host=self.host, port=self.port, user=self.user,
                               password=self.password, database=self.database,
                               charset="utf8mb4", autocommit=False,
                               connect_timeout=5, read_timeout=10, write_timeout=10)

    @staticmethod
    def _table(record: TraceRecord) -> str:
        return f"info_{record.station.value}"

    @staticmethod
    def _result(record: TraceRecord) -> str:
        measurement = record.second or record.first
        return measurement.result.value if measurement else ""

    @staticmethod
    def _values(record: TraceRecord) -> tuple:
        first, second = record.first, record.second
        return (
            record.created_at.astimezone().replace(tzinfo=None), record.serial_no, record.code_2d,
            first.pressure if first else None, first.pressure_unit if first else "",
            first.leakage if first else None, first.leakage_unit if first else "",
            second.pressure if second else None, second.pressure_unit if second else "",
            second.leakage if second else None, second.leakage_unit if second else "",
            PyMySQLRepository._result(record), record.part_no, record.person,
            1 if record.labeled else 0, record.cycle_id,
        )

    def _upsert(self, record: TraceRecord) -> TraceRecord:
        table = self._table(record)
        sql = f"""INSERT INTO `{table}`
            (`Time`,`Serial No.`,`2D Code`,`1 Pressure`,`1 Pressure Unit`,`1 Leakage`,`1 Leakage Unit`,
             `2 Pressure`,`2 Pressure Unit`,`2 Leakage`,`2 Leakage Unit`,
             `Result`,`Part No.`,`Person`,`labeled`,`cycle_id`)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
              `Time`=VALUES(`Time`),`Serial No.`=VALUES(`Serial No.`),`2D Code`=VALUES(`2D Code`),
              `1 Pressure`=VALUES(`1 Pressure`),`1 Pressure Unit`=VALUES(`1 Pressure Unit`),
              `1 Leakage`=VALUES(`1 Leakage`),`1 Leakage Unit`=VALUES(`1 Leakage Unit`),
              `2 Pressure`=VALUES(`2 Pressure`),`2 Pressure Unit`=VALUES(`2 Pressure Unit`),
              `2 Leakage`=VALUES(`2 Leakage`),`2 Leakage Unit`=VALUES(`2 Leakage Unit`),
              `Result`=VALUES(`Result`),`Part No.`=VALUES(`Part No.`),`Person`=VALUES(`Person`),
              `labeled`=GREATEST(`labeled`,VALUES(`labeled`)),id=LAST_INSERT_ID(id)"""
        with self._connect() as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(sql, self._values(record))
                    row_id = int(cursor.lastrowid)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        self._cycle_station[record.cycle_id] = table
        self._row_ids[record.cycle_id] = row_id
        self.records[record.cycle_id] = deepcopy(record)
        return record

    def query(self, text: str = "") -> list[TraceRecord]:
        needle = text.strip().lower()
        return [row for row in self.records.values()
                if not needle or needle in row.code_2d.lower() or needle in row.serial_no.lower()]

    def insert_stage1(self, record: TraceRecord, capability=None) -> TraceRecord:
        self._require_verified()
        if not isinstance(record, TraceRecord) or not record.cycle_id or record.first is None:
            raise ValueError("数据库记录缺少周期身份或第一次测量")
        with self._lock:
            # A dual OK cycle is not complete yet; keep it only in memory until test 2.
            if record.test_mode == "dual" and record.first.result is Result.OK and record.second is None:
                self._pending[record.cycle_id] = deepcopy(record)
                return record
            return self._upsert(record)

    def update_stage2(self, cycle_id: str, measurement: Measurement, capability=None) -> TraceRecord:
        self._require_verified()
        with self._lock:
            record = self._pending.pop(cycle_id, None)
            if record is None:
                raise KeyError(f"找不到待完成周期: {cycle_id}")
            record.second = measurement
            return self._upsert(record)

    def row_id(self, cycle_id: str) -> int | None:
        return self._row_ids.get(cycle_id)

    def mark_labeled(self, cycle_id: str, capability=None) -> None:
        self._require_verified()
        with self._lock:
            tables = [self._cycle_station[cycle_id]] if cycle_id in self._cycle_station else ["info_A", "info_B"]
            affected = 0
            with self._connect() as connection:
                try:
                    with connection.cursor() as cursor:
                        for table in tables:
                            cursor.execute(f"UPDATE `{table}` SET `labeled`=1 WHERE `cycle_id`=%s", (cycle_id,))
                            affected += cursor.rowcount
                    if affected != 1:
                        raise RuntimeError(f"标记打印成功必须且只能命中一条记录，实际 {affected}")
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise

    def query(self, text: str = "", station=None) -> list[TraceRecord]:
        self._require_verified()
        tables = [f"info_{station.value}"] if station is not None else ["info_A", "info_B"]
        rows: list[TraceRecord] = []
        where = " WHERE `2D Code` LIKE %s OR `Serial No.` LIKE %s" if text.strip() else ""
        params = (f"%{text.strip()}%", f"%{text.strip()}%") if text.strip() else ()
        with self._connect() as connection:
            with connection.cursor() as cursor:
                for table in tables:
                    cursor.execute(f"SELECT `Time`,`Serial No.`,`2D Code`,`1 Pressure`,`1 Pressure Unit`,`1 Leakage`,`1 Leakage Unit`,"
                                   f"`2 Pressure`,`2 Pressure Unit`,`2 Leakage`,`2 Leakage Unit`,`Result`,`Part No.`,`Person`,`labeled`,`cycle_id` "
                                   f"FROM `{table}`{where} ORDER BY `Time` DESC LIMIT 500", params)
                    for values in cursor.fetchall():
                        created = values[0]
                        if created.tzinfo is None:
                            created = created.astimezone()
                        result = Result(values[11]) if values[11] in ("OK", "NG") else Result.UNKNOWN
                        first = Measurement(float(values[3]), float(values[5]), result, b"DB", values[4] or "", values[6] or "") if values[3] is not None else None
                        second = Measurement(float(values[7]), float(values[9]), result, b"DB", values[8] or "", values[10] or "") if values[7] is not None else None
                        rows.append(TraceRecord(StationId(table[-1]), values[1], values[2], values[12], values[13],
                                                first, second, bool(values[14]), values[15], created))
        return rows
