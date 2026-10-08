"""Fail-safe, restartable two-stage station state machine."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from uuid import uuid4

from .ateq import AteqRequest, AteqResponse
from .journal import CycleJournal
from .models import (Measurement, Phase, PrintState, RecoveryRecord, Result,
                     StationId, TraceRecord)
from .permissions import SecurityContext
from .contracts import AteqPort, PrinterPort, RepositoryPort, SafeStopPort


class StationController:
    def __init__(self, station: StationId, repository: RepositoryPort, printer: PrinterPort, ateq: AteqPort,
                 journal: CycleJournal | None = None, safe_stop=None,
                 program: str = "SIM", license_status=None,
                 security: SecurityContext | None = None) -> None:
        if security is None:
            raise PermissionError("StationController 必须提供 SecurityContext")
        self.station, self.repository, self.printer, self.ateq = station, repository, printer, ateq
        if getattr(self.ateq, "station", None) == "SIM":
            self.ateq.station = station.value
        self.phase = Phase.IDLE
        self.journal = journal
        self.record: TraceRecord | None = None
        self.error = ""
        self.safe_stop = safe_stop
        self.program, self.sequence = program, 0
        self.license_status = license_status
        self.security = security or SecurityContext(license_status=license_status)
        self.print_state, self.print_job_id, self.print_receipt = PrintState.NONE, "", ""
        self.db_row_id: int | None = None
        self.db_intents: list[str] = []
        self.db_commits: list[str] = []
        self.recovery_required = False
        self.recovery_reason = ""
        self.ateq_intents: list[dict] = []
        self.ateq_results: list[dict] = []
        self._last_ateq_sequence = 0
        self.cycle_events: list[dict[str, str]] = []
        self._restore()

    def _restore(self) -> None:
        if not self.journal:
            return
        try:
            pending = self.journal.recover_record()
        except RuntimeError as exc:
            self.phase, self.error, self.recovery_required = Phase.FAULT, str(exc), True
            self.recovery_reason = str(exc)
            self._startup_safe_stop(self.error)
            return
        if pending is None:
            return
        if pending.station is not self.station:
            self.phase, self.error, self.recovery_required = Phase.FAULT, (
                f"RECOVERY_REQUIRED：journal 工位 {pending.station.value} 与当前工位 {self.station.value} 不一致"), True
            self.recovery_reason = self.error
            self._startup_safe_stop(self.error)
            return
        self.record = pending.record
        self.db_row_id = pending.db_row_id
        self.db_intents, self.db_commits = list(pending.db_intents), list(pending.db_commits)
        self.ateq_intents, self.ateq_results = list(pending.ateq_intents), list(pending.ateq_results)
        self.print_state, self.print_job_id, self.print_receipt = pending.print_state, pending.print_job_id, pending.print_receipt
        self.error = pending.error
        if pending.phase is Phase.COMPLETE and pending.record is not None and not pending.recovery_required:
            self.phase = Phase.COMPLETE
            self.recovery_required = False
        else:
            self.phase = Phase.FAULT
            self.recovery_required = True
            self.recovery_reason = pending.error or "RECOVERY_REQUIRED：存在未完成周期"
            self.error = self.recovery_reason
            if self.print_state in (PrintState.INTENT, PrintState.ACCEPTED):
                self.print_state = PrintState.AMBIGUOUS
            self._startup_safe_stop(self.error)

    def scan(self, code: str, part_no: str = "", person: str = "") -> None:
        self.security.allow_new_cycle()
        if not code.strip() or self.phase not in (Phase.IDLE, Phase.WAIT_SCAN):
            raise ValueError("当前状态不允许扫码")
        cycle_id = f"{self.station.value}-{uuid4().hex}"
        self.record = TraceRecord(self.station, code.strip(), code.strip(), part_no, person, cycle_id=cycle_id)
        self.phase, self.error, self.sequence = Phase.READY, "", 0
        self.print_state, self.print_job_id, self.print_receipt = PrintState.NONE, "", ""
        self.db_row_id, self.db_intents, self.db_commits = None, [], []
        self.recovery_required = False
        self.ateq_intents, self.ateq_results, self._last_ateq_sequence = [], [], 0
        self._journal()

    def test_first(self) -> Measurement:
        self._require(Phase.READY)
        self.phase = Phase.TEST_1
        self.sequence += 1
        try:
            measurement = self._run_ateq()
            self.record.first = measurement
            self._db_insert_stage1()
            self.phase = Phase.WAIT_2 if measurement.result is Result.OK else Phase.COMPLETE
            self._journal()
            return measurement
        except Exception as exc:
            self._safe_fault(exc, "第一次测试失败")
            raise

    def test_second(self) -> Measurement:
        self._require(Phase.WAIT_2)
        self.phase = Phase.TEST_2
        self.sequence += 1
        try:
            measurement = self._run_ateq()
            self.record.second = measurement
            self._db_update_stage2(measurement)
            self.phase = Phase.LABELING if measurement.result is Result.OK else Phase.COMPLETE
            self._journal()
            return measurement
        except Exception as exc:
            self._safe_fault(exc, "第二次测试失败")
            raise

    def _run_ateq(self) -> Measurement:
        program = getattr(self.ateq, "program", "") or self.program
        request = AteqRequest(self.station.value, self.record.cycle_id, program,
                              self.sequence, datetime.now(timezone.utc).isoformat())
        intent = {"station": request.station, "cycle_id": request.cycle_id,
                  "program": request.program, "sequence": request.sequence,
                  "timestamp": request.timestamp, "state": "TEST_INTENT"}
        self.ateq_intents.append(intent)
        self._journal()
        response = self.ateq.run(request)
        if not isinstance(response, AteqResponse):
            raise RuntimeError("ATEQ 响应类型无效")
        echoed = response.request
        if (echoed.station != self.station.value or
                echoed.cycle_id != request.cycle_id or echoed.program != request.program or
                echoed.sequence != request.sequence or echoed.sequence <= self._last_ateq_sequence or
                echoed.timestamp != request.timestamp or not response.raw_frame or
                not response.measurement.raw_frame or len(response.raw_frame) < 4 or
                response.raw_frame == request.cycle_id.encode() or
                response.raw_frame != response.measurement.raw_frame):
            raise RuntimeError("ATEQ 响应身份不匹配，禁止写入数据库")
        self._last_ateq_sequence = echoed.sequence
        self.ateq_results.append({"station": echoed.station, "cycle_id": echoed.cycle_id,
                                  "program": echoed.program, "sequence": echoed.sequence,
                                  "timestamp": echoed.timestamp, "raw_frame_hex": response.raw_frame.hex(),
                                  "state": "TEST_RESULT"})
        self._journal()
        return response.measurement

    def _db_insert_stage1(self) -> None:
        intent = f"insert_stage1:{self.record.cycle_id}"
        self.db_intents.append(intent)
        self._journal()
        self.repository.insert_stage1(self.record)
        self.db_row_id = self.repository.row_id(self.record.cycle_id)
        self.db_commits.append(intent)
        self._journal()

    def _db_update_stage2(self, measurement: Measurement) -> None:
        intent = f"update_stage2:{self.record.cycle_id}"
        self.db_intents.append(intent)
        self._journal()
        self.repository.update_stage2(self.record.cycle_id, measurement)
        self.db_commits.append(intent)
        self._journal()

    def label(self) -> bool:
        self._require(Phase.LABELING)
        self.print_state = PrintState.INTENT
        self.print_job_id = f"label-{self.record.cycle_id}"
        self._journal()
        try:
            receipt = self.printer.print_label(self.record)
            accepted, job_id, receipt_text = self._receipt_values(receipt)
            if not accepted:
                raise RuntimeError("打印未确认")
            self.print_state = PrintState.ACCEPTED
            self.print_job_id = job_id or self.print_job_id
            self.print_receipt = receipt_text
            self._journal()
            intent = f"mark_labeled:{self.record.cycle_id}"
            self.db_intents.append(intent)
            self._journal()
            self.repository.mark_labeled(self.record.cycle_id)
            self.record.labeled = True
            self.db_commits.append(intent)
            self.print_state = PrintState.LABELED
            self._journal()
        except Exception as exc:
            self.print_state = PrintState.AMBIGUOUS if self.print_state in (PrintState.INTENT, PrintState.ACCEPTED) else self.print_state
            self._safe_fault(exc, "打印/贴标失败")
            return False
        self.phase = Phase.COMPLETE
        self._journal()
        return True

    @staticmethod
    def _receipt_values(receipt) -> tuple[bool, str, str]:
        if isinstance(receipt, bool):
            return receipt, "", ""
        return bool(getattr(receipt, "accepted", False)), str(getattr(receipt, "job_id", "")), str(getattr(receipt, "receipt", ""))

    def reset(self) -> None:
        if self.record is not None and self.phase not in (Phase.IDLE, Phase.COMPLETE, Phase.FAULT):
            self._safe_fault(RuntimeError("操作员复位"), "安全中止：活动周期保留待人工处理")
            return
        if self.recovery_required and self.record is not None:
            raise RuntimeError("RECOVERY_REQUIRED：必须授权人工处理后归档")
        if self.journal:
            self.journal.archive_and_clear("operator reset after terminal/fault")
        self.record = None
        self.error = ""
        self.phase = Phase.IDLE
        self.print_state, self.print_job_id, self.print_receipt = PrintState.NONE, "", ""
        self.db_row_id, self.db_intents, self.db_commits = None, [], []

    def resolve_recovery(self, reason: str = "人工确认") -> None:
        self.security.require("recovery_resolve")
        if not self.recovery_required:
            return
        original = self.journal.recover() if self.journal else None
        if self.journal:
            self.journal.archive_and_clear(reason, audit={
                "actor": self.security.session.username or self.security.role.value,
                "action": "recovery_resolve",
                "reason": reason,
                "resolved_at": datetime.now(timezone.utc).isoformat(),
                "cycle_id": self.record.cycle_id if self.record else "",
                "snapshot": original,
                "snapshot_sha256": hashlib.sha256(json.dumps(original, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest(),
            })
        self.security.audit("recovery_resolve", reason=reason,
                            cycle_id=self.record.cycle_id if self.record else "",
                            original_snapshot=str(original))
        self.record, self.error = None, ""
        self.phase, self.recovery_required = Phase.IDLE, False
        self.print_state, self.print_job_id, self.print_receipt = PrintState.NONE, "", ""

    def _safe_fault(self, exc: Exception, prefix: str) -> None:
        self.phase = Phase.FAULT
        if self.record is not None:
            self.recovery_required = True
        self.error = f"{prefix}：{exc}"
        if self.safe_stop is not None:
            try:
                self.safe_stop.safe_stop(self.error)
                if hasattr(self.safe_stop, "outputs_energized") and self.safe_stop.outputs_energized():
                    self.error += "；停止状态未确认"
            except Exception as stop_exc:
                self.error += f"；安全停止失败：{stop_exc}"
        try:
            self._journal()
        except Exception:
            self.recovery_required = True

    def _startup_safe_stop(self, reason: str) -> None:
        if self.safe_stop is None:
            return
        try:
            self.safe_stop.safe_stop(reason)
            if hasattr(self.safe_stop, "outputs_energized") and self.safe_stop.outputs_energized():
                self.error += "；启动安全停止状态未确认"
        except Exception as exc:
            self.error += f"；启动安全停止失败：{exc}"

    def _journal(self) -> None:
        if not self.journal or not self.record:
            return
        self.cycle_events.append({"station": self.station.value, "cycle_id": self.record.cycle_id,
                                  "phase": self.phase.name, "timestamp": datetime.now(timezone.utc).isoformat(),
                                  "error": self.error})
        state = RecoveryRecord(self.station, self.record.cycle_id, self.phase, self.record,
                               self.db_row_id, list(self.db_intents), list(self.db_commits),
                               list(self.ateq_intents), list(self.ateq_results),
                               self.print_state, self.print_job_id, self.print_receipt,
                               self.error, self.recovery_required)
        try:
            self.journal.write_record(state)
        except OSError as exc:
            self.phase, self.error, self.recovery_required = Phase.FAULT, f"RECOVERY_REQUIRED：journal 写入失败 {exc}", True
            if self.safe_stop:
                self.safe_stop.safe_stop(self.error)
            raise RuntimeError(self.error) from exc

    def _require(self, phase: Phase) -> None:
        if self.recovery_required:
            raise RuntimeError("RECOVERY_REQUIRED：存在未完成周期")
        if self.record is None or self.phase is not phase:
            raise RuntimeError(f"工位 {self.station.value} 当前状态为 {self.phase.value}")
