"""Typed domain models shared by adapters, state machines and UI."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

class RunMode(str, Enum):
    SIMULATE = "simulate"
    CHARACTERIZATION = "characterization"
    SHADOW = "shadow"
    LIVE = "live"

# Public name used by the execution plan.  Keep RunMode as a compatibility
# alias because the first simulator release exposed that name.
RuntimeMode = RunMode

class StationId(str, Enum):
    A = "A"
    B = "B"

class Phase(str, Enum):
    IDLE = "空闲"
    WAIT_SCAN = "等待扫码"
    READY = "就绪"
    TEST_1 = "第一次测试"
    WAIT_2 = "等待第二次测试"
    TEST_2 = "第二次测试"
    LABELING = "贴标"
    COMPLETE = "完成"
    CALIBRATION = "校准"
    FAULT = "故障"

    @classmethod
    def from_value(cls, value: str) -> "Phase":
        try:
            return cls[value]
        except KeyError:
            for phase in cls:
                if phase.value == value:
                    return phase
            raise ValueError(f"未知工位状态: {value}")

class Result(str, Enum):
    UNKNOWN = ""
    OK = "OK"
    NG = "NG"

@dataclass(frozen=True)
class Measurement:
    pressure: float
    leakage: float
    result: Result
    raw_frame: bytes = b""

    def to_dict(self) -> dict:
        return {
            "pressure": self.pressure,
            "leakage": self.leakage,
            "result": self.result.value,
            "raw_frame_hex": self.raw_frame.hex(),
        }

    @classmethod
    def from_dict(cls, value: dict) -> "Measurement":
        if not isinstance(value, dict):
            raise ValueError("测量数据必须为对象")
        return cls(float(value["pressure"]), float(value["leakage"]),
                   Result(value.get("result", "")),
                   bytes.fromhex(value.get("raw_frame_hex", "")))

@dataclass
class TraceRecord:
    station: StationId
    serial_no: str
    code_2d: str
    part_no: str = ""
    person: str = ""
    first: Measurement | None = None
    second: Measurement | None = None
    labeled: bool = False
    cycle_id: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "station": self.station.value, "serial_no": self.serial_no,
            "code_2d": self.code_2d, "part_no": self.part_no,
            "person": self.person, "first": self.first.to_dict() if self.first else None,
            "second": self.second.to_dict() if self.second else None,
            "labeled": self.labeled, "cycle_id": self.cycle_id,
            "created_at": self.created_at.astimezone(timezone.utc).isoformat(),
        }

    @classmethod
    def from_dict(cls, value: dict) -> "TraceRecord":
        if not isinstance(value, dict):
            raise ValueError("周期记录必须为对象")
        timestamp = datetime.fromisoformat(value["created_at"])
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return cls(StationId(value["station"]), str(value["serial_no"]),
                   str(value["code_2d"]), str(value.get("part_no", "")),
                   str(value.get("person", "")),
                   Measurement.from_dict(value["first"]) if value.get("first") else None,
                   Measurement.from_dict(value["second"]) if value.get("second") else None,
                   bool(value.get("labeled", False)), str(value["cycle_id"]), timestamp)


class PrintState(str, Enum):
    NONE = "NONE"
    INTENT = "INTENT"
    ACCEPTED = "ACCEPTED"
    LABELED = "LABELED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass
class RecoveryRecord:
    """Versioned durable state; every field needed for restart is explicit."""
    station: StationId
    cycle_id: str
    phase: Phase
    record: TraceRecord | None = None
    db_row_id: int | None = None
    db_intents: list[str] = field(default_factory=list)
    db_commits: list[str] = field(default_factory=list)
    ateq_intents: list[dict] = field(default_factory=list)
    ateq_results: list[dict] = field(default_factory=list)
    print_state: PrintState = PrintState.NONE
    print_job_id: str = ""
    print_receipt: str = ""
    error: str = ""
    recovery_required: bool = False
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    VERSION = 3

    def to_dict(self) -> dict:
        stamp = self.updated_at.astimezone(timezone.utc)
        return {
            "schema_version": self.VERSION,
            "station": self.station.value,
            "cycle_id": self.cycle_id,
            "phase": self.phase.name,
            "record": self.record.to_dict() if self.record else None,
            "db_row_id": self.db_row_id,
            "db_intents": list(self.db_intents),
            "db_commits": list(self.db_commits),
            "ateq_intents": list(self.ateq_intents),
            "ateq_results": list(self.ateq_results),
            "print_state": self.print_state.value,
            "print_job_id": self.print_job_id,
            "print_receipt": self.print_receipt,
            "receipt": self.print_receipt,
            "print_intent": self.print_state in (PrintState.INTENT, PrintState.ACCEPTED, PrintState.AMBIGUOUS),
            "error": self.error,
            "recovery_required": self.recovery_required,
            "updated_at": stamp.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: dict) -> "RecoveryRecord":
        required = ("schema_version", "station", "cycle_id", "phase", "updated_at")
        if not isinstance(value, dict) or any(key not in value for key in required):
            raise ValueError("恢复日志字段不完整")
        if int(value["schema_version"]) != cls.VERSION:
            raise ValueError("不支持的恢复日志版本")
        stamp = datetime.fromisoformat(value["updated_at"])
        if stamp.tzinfo is None:
            raise ValueError("恢复日志时间戳必须带时区")
        record = TraceRecord.from_dict(value["record"]) if value.get("record") else None
        station = StationId(value["station"])
        if record is not None and (record.station is not station or record.cycle_id != value["cycle_id"]):
            raise ValueError("恢复日志周期身份不一致")
        return cls(station, str(value["cycle_id"]), Phase.from_value(str(value["phase"])),
                   record, int(value["db_row_id"]) if value.get("db_row_id") is not None else None,
                   [str(x) for x in value.get("db_intents", [])],
                   [str(x) for x in value.get("db_commits", [])],
                   [dict(x) for x in value.get("ateq_intents", [])],
                   [dict(x) for x in value.get("ateq_results", [])],
                   PrintState(value.get("print_state", "NONE")),
                   str(value.get("print_job_id", "")), str(value.get("print_receipt", value.get("receipt", ""))),
                   str(value.get("error", "")), bool(value.get("recovery_required", False)), stamp)

@dataclass(frozen=True)
class DeviceHealth:
    name: str
    connected: bool
    detail: str = ""
