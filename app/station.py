"""Fail-safe, restartable two-stage station state machine (laser marking)."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math
from uuid import uuid4

from .ateq import AteqRequest, AteqResponse
from .journal import CycleJournal
from .models import (Measurement, Phase, MarkState, RecoveryRecord, Result,
                     StationId, CycleSelection, TraceRecord)
from .permissions import SecurityContext
from .contracts import AteqPort, MarkerPort, RepositoryPort, SafeStopPort
from .workflow import next_phase_after_first, next_phase_after_second


def _validate_measurement_for_mark(measurement, label: str) -> None:
    """BR12: a measurement in the committed row must be finite OK numeric data."""
    if not isinstance(measurement, Measurement):
        raise ValueError(f"回读{label}测量类型无效，禁止打码")
    if measurement.result is not Result.OK:
        raise ValueError(f"回读{label}测量结果非 OK，禁止打码")
    for field_name in ("pressure", "leakage"):
        value = getattr(measurement, field_name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"回读{label}测量 {field_name} 数值无效，禁止打码")
        if not math.isfinite(float(value)):
            raise ValueError(f"回读{label}测量 {field_name} 不是有限数值，禁止打码")
    for unit_name in ("pressure_unit", "leakage_unit"):
        if not isinstance(getattr(measurement, unit_name), str):
            raise ValueError(f"回读{label}测量 {unit_name} 单位无效，禁止打码")


def validate_committed_for_mark(readback: TraceRecord, frozen_record: TraceRecord,
                                station: StationId) -> TraceRecord:
    """P04: validate one committed DB readback, then overlay ONLY frozen metadata.

    schema v2 does not store test_mode/sample_cycle/date_scheme/ateq_program in
    the database, so those four fields alone are copied from the frozen cycle;
    all business fields (identity/time/part/person/measurements) come from the
    committed row.  Returns a deep copy; the caller never gets a shared object.
    """
    if not isinstance(readback, TraceRecord):
        raise ValueError("回读记录类型无效，禁止打码")
    if readback.station is not station:
        raise ValueError("回读记录工位不一致，禁止打码")
    if (not isinstance(readback.cycle_id, str) or not readback.cycle_id.strip()
            or readback.cycle_id != frozen_record.cycle_id):
        raise ValueError("回读记录周期身份不一致，禁止打码")
    if not isinstance(readback.created_at, datetime):
        raise ValueError("回读记录时间戳无效，禁止打码")
    if not isinstance(readback.part_no, str) or not readback.part_no.strip():
        raise ValueError("回读记录型号无效，禁止打码")
    if not isinstance(readback.person, str) or not readback.person.strip():
        raise ValueError("回读记录人员无效，禁止打码")
    if frozen_record.test_mode not in ("single", "dual"):
        raise ValueError("冻结检测模式无效，禁止打码")
    if not isinstance(frozen_record.date_scheme, str) or not frozen_record.date_scheme.strip():
        raise ValueError("冻结日期方案无效，禁止打码")
    if readback.first is None:
        raise ValueError("回读记录缺少第一次测量，禁止打码")
    _validate_measurement_for_mark(readback.first, "第一次")
    if frozen_record.test_mode == "dual" and readback.second is None:
        raise ValueError("双测回读记录缺少第二次测量，禁止打码")
    if readback.second is not None:
        _validate_measurement_for_mark(readback.second, "第二次")
    output = deepcopy(readback)
    output.test_mode = frozen_record.test_mode
    output.sample_cycle = frozen_record.sample_cycle
    output.date_scheme = frozen_record.date_scheme
    output.ateq_program = frozen_record.ateq_program
    return output


class StationController:
    def __init__(self, station: StationId, repository: RepositoryPort, marker: MarkerPort, ateq: AteqPort,
                 journal: CycleJournal | None = None, safe_stop=None,
                 program: str = "SIM", license_status=None,
                 security: SecurityContext | None = None,
                 mark_samples: bool = False) -> None:
        if security is None:
            raise PermissionError("StationController 必须提供 SecurityContext")
        self.station, self.repository, self.marker, self.ateq = station, repository, marker, ateq
        self.mark_samples = bool(mark_samples)
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
        self.mark_state, self.mark_job_id, self.mark_receipt = MarkState.NONE, "", ""
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
        self.mark_state, self.mark_job_id, self.mark_receipt = pending.mark_state, pending.mark_job_id, pending.mark_receipt
        self.error = pending.error
        if pending.phase is Phase.COMPLETE and pending.record is not None and not pending.recovery_required:
            self.phase = Phase.COMPLETE
            self.recovery_required = False
        else:
            self.phase = Phase.FAULT
            self.recovery_required = True
            self.recovery_reason = pending.error or "RECOVERY_REQUIRED：存在未完成周期"
            self.error = self.recovery_reason
            if self.mark_state in (MarkState.INTENT, MarkState.PULSED):
                self.mark_state = MarkState.AMBIGUOUS
            self._startup_safe_stop(self.error)

    def start_cycle(self, selection: CycleSelection, *, sample: bool = False) -> None:
        """Open one production (or sample) cycle; the hardware owns cycle start.

        没有扫码工序：周期身份由 工位+时间戳+短UUID 生成，型号/人员/ATEQ
        程序号在硬件启动边沿时从界面冻结。
        """
        self.security.allow_new_cycle()
        if selection.station is not self.station:
            raise ValueError("冻结选择与控制器工位不一致")
        if self.phase is not Phase.IDLE:
            raise ValueError("当前状态不允许开始新周期")
        if selection.ateq_program:
            self.ateq.select_program(selection.ateq_program)
            self.program = selection.ateq_program
        cycle_id = f"{self.station.value}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6]}"
        self.record = TraceRecord(
            station=self.station, part_no=selection.product_id.strip(), person=selection.person.strip(),
            cycle_id=cycle_id, ateq_program=(selection.ateq_program or self.program).strip(),
            test_mode=selection.test_mode, sample_cycle=sample,
            date_scheme=selection.date_scheme,
        )
        self.phase, self.error, self.sequence = Phase.READY, "", 0
        self.mark_state, self.mark_job_id, self.mark_receipt = MarkState.NONE, "", ""
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
            # P01 纯决策：BR06 非 OK→COMPLETE；BR07 样件禁打→COMPLETE；
            # BR08 单测 OK→MARKING，双测 OK→WAIT_2。
            self.phase = next_phase_after_first(
                measurement.result, self.record.test_mode,
                self.record.sample_cycle, self.mark_samples)
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
            # P02 纯决策：BR09 双 OK→MARKING，任一非 OK/缺失→COMPLETE；
            # BR10 样件禁打→COMPLETE。双 OK 校验是防御性的。
            first = self.record.first
            self.phase = next_phase_after_second(
                first.result if first is not None else None,
                measurement.result, self.record.sample_cycle, self.mark_samples)
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
        # Real ATEQ cycles are started by the PLC hardware.  The live adapter
        # is monitor-only; simulation adapters may still mirror a start action.
        start_test = getattr(self.ateq, "start_test", None)
        if callable(start_test) and not getattr(self.ateq, "external_start", False):
            start_test()
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

    def mark(self) -> bool:
        """Laser-marking transaction: only reached when both tests are OK.

        BR11/BR12：打码内容以数据库已提交记录回读为准（打的数据=存的数据）；
        回读缺失、类型/身份/时间/测量无效或非 OK 时拒绝打码，绝不回退到
        过程内存记录或缓存 get()。
        """
        self._require(Phase.MARKING)
        self.mark_state = MarkState.INTENT
        self.mark_job_id = f"mark-{self.record.cycle_id}"
        self._journal()
        try:
            committed = self.repository.get_committed(self.record.cycle_id)
            if committed is None:
                raise RuntimeError("已提交数据库回读缺失，禁止打码")
            marked_record = validate_committed_for_mark(committed, self.record, self.station)
            receipt = self.marker.mark(marked_record)
            accepted, job_id, receipt_text = self._receipt_values(receipt)
            if not accepted:
                raise RuntimeError(receipt_text or "打码未确认")
            self.mark_state = MarkState.PULSED
            self.mark_job_id = job_id or self.mark_job_id
            self.mark_receipt = receipt_text
            self._journal()
            intent = f"mark_marked:{self.record.cycle_id}"
            self.db_intents.append(intent)
            self._journal()
            self.repository.mark_marked(self.record.cycle_id)
            self.record.marked = True
            self.record.marked_at = datetime.now(timezone.utc)
            self.db_commits.append(intent)
            self.mark_state = MarkState.MARKED
            self._journal()
        except Exception as exc:
            self.mark_state = MarkState.AMBIGUOUS if self.mark_state in (MarkState.INTENT, MarkState.PULSED) else self.mark_state
            self._safe_fault(exc, "打码失败")
            return False
        self.phase = Phase.COMPLETE
        self._journal()
        return True

    def remark(self) -> bool:
        """Admin re-mark of the completed cycle (a new physical pulse).

        BR19：服务层强制管理员权限；权限检查在任何状态变更或硬件 I/O 之前。
        """
        self.security.require("remark")
        if self.phase is not Phase.COMPLETE or self.record is None or not self.record.marked:
            raise RuntimeError("只有已打码完成的周期允许重打码")
        previous_phase = self.phase
        self.phase = Phase.MARKING
        try:
            return self.mark()
        except Exception:
            self.phase = previous_phase
            raise

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
        self.mark_state, self.mark_job_id, self.mark_receipt = MarkState.NONE, "", ""
        self.db_row_id, self.db_intents, self.db_commits = None, [], []

    def resolve_recovery(self, reason: str = "人工确认", *, require_permission: bool = True) -> None:
        if require_permission:
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
        self.mark_state, self.mark_job_id, self.mark_receipt = MarkState.NONE, "", ""

    def fault(self, reason: str) -> None:
        """Public fail-closed transition for PLC/runtime coordination errors."""
        self._safe_fault(RuntimeError(reason), "外部联动失败")

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
                               self.mark_state, self.mark_job_id, self.mark_receipt,
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
