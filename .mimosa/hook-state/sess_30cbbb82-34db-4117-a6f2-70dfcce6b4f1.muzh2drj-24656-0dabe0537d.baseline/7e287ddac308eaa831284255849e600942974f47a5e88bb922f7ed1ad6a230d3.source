"""Idempotent in-memory repository and live-write gate."""
from __future__ import annotations
from threading import Lock
from .config import Settings, LiveCapability
from .models import Measurement, Result, TraceRecord
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
    """Validated production boundary; no guessed schema or silent writes."""
    def __init__(self, *, host: str, user: str, password: str, database: str, port: int = 3306) -> None:
        if not host or not user or not database or not 1 <= port <= 65535:
            raise ValueError("MySQL 配置无效")
        self.host = host
        self.user = user
        self.password = password
        self.database = database
        self.port = port
        self._schema_verified = False

    def verify_schema(self, show_create_results: dict[str, str]) -> None:
        required = {"info_A", "info_B"}
        if not required.issubset(show_create_results):
            raise RuntimeError("LIVE_BLOCKED: 缺少 info_A/info_B 表结构证据")
        if not all("PRIMARY KEY" in value.upper() for value in show_create_results.values()):
            raise RuntimeError("LIVE_BLOCKED: info_A/info_B 没有可靠唯一键")
        self._schema_verified = True

    def _blocked(self) -> None:
        if not self._schema_verified:
            raise RuntimeError("LIVE_BLOCKED: MySQL schema 未通过 SHOW CREATE TABLE 校验")
        raise RuntimeError("LIVE_BLOCKED: 生产数据库写入适配器尚未启用")

    def insert_stage1(self, record: TraceRecord, capability=None) -> TraceRecord:
        self._blocked()

    def update_stage2(self, cycle_id: str, measurement: Measurement, capability=None) -> TraceRecord:
        self._blocked()

    def row_id(self, cycle_id: str) -> int | None:
        return None

    def mark_labeled(self, cycle_id: str, capability=None) -> None:
        self._blocked()

    def query(self, text: str = "") -> list[TraceRecord]:
        return []
