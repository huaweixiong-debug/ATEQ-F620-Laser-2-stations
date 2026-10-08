"""Fail-safe, restartable two-stage station state machine (laser marking)."""
from __future__ import annotations

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
        """第一腔测试并落库。

        240429 机的腔序是"先负压、后正压"：TEST_1=负压腔，TEST_2=正压腔，
        周期由 PLC/仪器硬件时序驱动，上位机只按顺序监控两次，两腔全 OK
        才允许打码；第一腔 NG 时仪器自行终止，周期立即完成（不打码）。
        """
        self._require(Phase.READY)
        self.phase = Phase.TEST_1
        self.sequence += 1
        try:
            measurement = self._run_ateq()
            self.record.first = measurement
            self._db_insert_stage1()
            if measurement.result is Result.OK:
                # 双测：两测都合格才进入打码；单测：一次合格即打码。
                # 样件周期默认不打码（mark_samples 配置可放开）。
                if self.record.sample_cycle and not self._sample_marking_enabled():
                    self.phase = Phase.COMPLETE
                else:
                    self.phase = Phase.MARKING if self.record.test_mode == "single" else Phase.WAIT_2
            else:
                # 第一腔 NG：仪器自身终止检测，不会有第二次测试结果，
                # 周期立即完成（记录落库、不打码）；单测 NG 同理。
                self.phase = Phase.COMPLETE
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
            # 正常流程只有第一次 OK 才会进入 WAIT_2/TEST_2；这里的双 OK
            # 校验是防御性的（仪器行为差异不至于误打码）。
            if (measurement.result is Result.OK
                    and self.record.first is not None
                    and self.record.first.result is Result.OK):
                if self.record.sample_cycle and not self.mark_samples:
                    self.phase = Phase.COMPLETE
                else:
                    self.phase = Phase.MARKING
            else:
                self.phase = Phase.COMPLETE
            self._journal()
            return measurement
        except Exception as exc:
            self._safe_fault(exc, "第二次测试失败")
            raise

    def _sample_marking_enabled(self) -> bool:
        return self.mark_samples

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

        打码内容以数据库提交后的记录回读为准（打的数据=存的数据）。
        """
        self._require(Phase.MARKING)
        self.mark_state = MarkState.INTENT
        self.mark_job_id = f"mark-{self.record.cycle_id}"
        self._journal()
        try:
            readback = self.repository.get_committed(self.record.cycle_id)
            if readback is None:
                raise ValueError("数据库未找到已提交周期记录，禁止打码")
            marked_record = self._validate_mark_readback(readback)
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
        """Admin re-mark of the completed cycle (idempotent re-pulse)."""
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

    def _validate_mark_readback(self, readback: TraceRecord) -> TraceRecord:
        """Fail-closed validation of the committed row used as marker input.

        All checks run before ``marker.mark``; the only value not sourced from
        the readback is the ``date_scheme`` formatting overlay.  Pass/fail is
        decided by the readback ``Result`` columns, never by in-memory data.
        """
        if not isinstance(readback, TraceRecord):
            raise ValueError("打码回读类型无效")
        if readback.station is not self.station:
            raise ValueError("打码回读工位不一致")
        if (not isinstance(readback.cycle_id, str) or not readback.cycle_id
                or readback.cycle_id != self.record.cycle_id):
            raise ValueError("打码回读周期号不一致")
        if not isinstance(readback.created_at, datetime):
            raise ValueError("打码回读时间无效")
        for name in ("part_no", "person"):
            value = getattr(readback, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"打码回读字段无效: {name}")
        scheme = self.record.date_scheme
        if not isinstance(scheme, str) or not scheme.strip():
            raise ValueError("打码日期方案无效")
        readback.date_scheme = scheme.strip()
        require_second = self.record.test_mode != "single"
        self._validate_mark_measurement(readback.first, "first")
        if readback.second is None:
            if require_second or readback.test_mode == "dual":
                raise ValueError("打码回读缺少第二次测量")
        else:
            self._validate_mark_measurement(readback.second, "second")
        return readback

    @staticmethod
    def _validate_mark_measurement(value, name: str) -> None:
        if not isinstance(value, Measurement):
            raise ValueError(f"打码回读测量无效: {name}")
        for field in ("pressure", "leakage"):
            number = getattr(value, field)
            if (isinstance(number, bool) or not isinstance(number, (int, float))
                    or not math.isfinite(float(number))):
                raise ValueError(f"打码回读数值无效: {name}.{field}")
        for field in ("pressure_unit", "leakage_unit"):
            if not isinstance(getattr(value, field), str):
                raise ValueError(f"打码回读单位无效: {name}.{field}")
        if not isinstance(value.result, Result) or value.result is not Result.OK:
            raise ValueError(f"打码回读结果非 OK: {name}")

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
