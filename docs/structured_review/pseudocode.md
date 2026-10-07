# F620 可执行伪代码契约 v1.1

## P01 纯第一测决策

```text
FUNCTION next_phase_after_first(result: Result, mode: str, sample: bool, mark_samples: bool) -> Phase
    REQUIRE result is Result; mode IN {single, dual}
    IF result != OK: RETURN COMPLETE
    IF sample AND NOT mark_samples: RETURN COMPLETE
    IF mode == single: RETURN MARKING
    RETURN WAIT_2
```

## P02 纯第二测决策

```text
FUNCTION next_phase_after_second(first_result: Result | None, second_result: Result,
                                 sample: bool, mark_samples: bool) -> Phase
    REQUIRE first_result is None OR Result; second_result is Result
    IF first_result != OK OR second_result != OK: RETURN COMPLETE
    IF sample AND NOT mark_samples: RETURN COMPLETE
    RETURN MARKING
```

## P03 周期与测量

```text
FUNCTION start_cycle(selection, sample=False)
    REQUIRE license valid AND phase=IDLE AND matching station
    SELECT program using existing adapter
    CREATE cycle_id and TraceRecord from immutable selection INCLUDING date_scheme
    CLEAR per-cycle intents/commits/sequence/mark metadata using existing rules
    phase=READY
    JOURNAL

FUNCTION test_first()
    REQUIRE phase=READY AND NOT recovery_required
    phase=TEST_1; sequence+=1
    TRY:
        first=run_correlated_ateq()     # current identity and raw-frame validation unchanged
        record.first=first
        db_insert_stage1()             # production dual-first-OK still pending
        phase=next_phase_after_first(first.result, record.test_mode, record.sample_cycle, mark_samples)
        JOURNAL; RETURN first
    EXCEPT error: SAFE_FAULT(error); RAISE

FUNCTION test_second()
    REQUIRE phase=WAIT_2 AND NOT recovery_required
    phase=TEST_2; sequence+=1
    TRY:
        second=run_correlated_ateq()
        record.second=second
        db_update_stage2(second)
        phase=next_phase_after_second(record.first.result IF first EXISTS ELSE None,
                                     second.result, record.sample_cycle, mark_samples)
        JOURNAL; RETURN second
    EXCEPT error: SAFE_FAULT(error); RAISE
```

## P04 已提交数据门禁

```text
FUNCTION validate_committed_for_mark(readback, frozen_record, station) -> TraceRecord
    REQUIRE readback is TraceRecord
    REQUIRE readback.station = station
    REQUIRE nonempty string readback.cycle_id = frozen_record.cycle_id
    REQUIRE readback.created_at is datetime
    REQUIRE part_no and person are nonempty strings
    REQUIRE frozen_record.test_mode IN {single, dual}
    REQUIRE frozen_record.date_scheme is nonempty string
    VALIDATE first EXISTS, type Measurement, Result.OK,
             pressure/leakage numeric NOT bool AND finite, units strings
    IF frozen_record.test_mode == dual: REQUIRE second EXISTS
    IF second EXISTS: VALIDATE second with identical rules
    output=DEEP_COPY(readback)
    # schema-v2 does not store these metadata columns; use ONLY frozen metadata overlay
    output.test_mode=frozen_record.test_mode
    output.sample_cycle=frozen_record.sample_cycle
    output.date_scheme=frozen_record.date_scheme
    output.ateq_program=frozen_record.ateq_program
    RETURN output

FUNCTION get_committed_mysql(cycle_id)
    REQUIRE schema_verified
    LOCK repository
    tables=[mapped table] IF mapping EXISTS ELSE [info_A, info_B]
    OPEN DB connection USING existing 5/10/10 timeouts
    SELECT by parameterized cycle_id FROM tables (ALWAYS actual DB, NEVER records/_pending)
    IF found_count > 1: RAISE ambiguous cycle identity
    IF no row: RETURN None
    RETURN row_to_record(table,row)     # DB first/second/Result authoritative

FUNCTION fake_commit(record)
    KEEP existing records compatibility behavior
    committed_snapshots[cycle_id]=DEEP_COPY(record)

FUNCTION get_committed_fake(cycle_id)
    LOCK; RETURN DEEP_COPY(committed_snapshots.get(cycle_id))
```

## P05 打码事务

```text
FUNCTION mark()
    REQUIRE phase=MARKING AND NOT recovery_required
    mark_state=INTENT; mark_job_id=mark-cycle_id; JOURNAL
    TRY:
        readback=repository.get_committed(cycle_id)
        REQUIRE readback EXISTS       # NEVER fall back to self.record or repository.get()
        payload=validate_committed_for_mark(readback, record, station)
        receipt=marker.mark(payload)
        REQUIRE receipt.accepted using current bool/object compatibility
        mark_state=PULSED; save receipt; JOURNAL
        APPEND db mark intent; JOURNAL
        repository.mark_marked(cycle_id)
        record.marked=True; record.marked_at=now
        APPEND db commit; mark_state=MARKED; JOURNAL
    EXCEPT error:
        IF mark_state IN {INTENT,PULSED}: mark_state=AMBIGUOUS
        SAFE_FAULT(error); RETURN False
    phase=COMPLETE; JOURNAL; RETURN True

FUNCTION remark()
    security.require(remark)     # before any mutation or hardware I/O
    REQUIRE phase=COMPLETE AND record EXISTS AND record.marked
    KEEP current re-mark transaction and exception policy
    phase=MARKING; RETURN mark()
```

## P06 激光异常清理

```text
FUNCTION laser_mark(record)
    job_id=mark-cycle_id
    TRY build_mark_text; EXCEPT RETURN rejected(content failure)
    CANCEL old clear timer per existing behavior
    TRY atomic_publish_and_verify; EXCEPT RETURN rejected(file failure) # PLC untouched
    byte,bit=laser_start address
    TRY:
        PLC.WRITE(byte,bit,True)                   # exactly ONE True attempt
        REQUIRE PLC.READ(byte,bit)==True
        hold_with_existing_diagnostic()
        PLC.WRITE(byte,bit,False)
        REQUIRE PLC.READ(byte,bit)==False
        SLEEP(settle)
        IF wait_done AND map.has(laser_done): WAIT per existing deadline/50ms policy
    EXCEPT error:
        TRY PLC.WRITE(byte,bit,False)              # cleanup, NOT a new start pulse
        EXCEPT cleanup_error: append start-bit deenergize unconfirmed to receipt
        SCHEDULE existing delayed clear
        RETURN rejected(error with cleanup status)
    SCHEDULE existing delayed clear
    RETURN accepted(receipt-job_id)
```

## P07 日期冻结与兼容

```text
DATACLASS frozen CycleSelection:
    existing 5 fields unchanged
    date_scheme: str = YYYYMMDD    # trailing compatible default
    POST_INIT:
        existing mode/required checks
        REQUIRE date_scheme is string AND strip(date_scheme) nonempty
        NORMALIZE date_scheme by stripping

UI selection entry points:
    _prepare_stepcode_production_cycle
    _restore_pending_calibration_cycle
    _begin_ok_validation_cycle
    start_calibration
    ALL pass date_scheme=config.date_scheme explicitly

Journal serialization:
    TraceRecord.date_scheme already serialized; keep schema VERSION=4 unchanged
```

## P08 失败定位与返工

```text
RUN all offline unit tests AND smoke; SAVE full command, stdout, stderr, exit code
IF ANY test fails:
    INPUT {requirements, pseudocode, test_cases, exact traceback, changed diff, baseline result}
          TO GPT-6 Luna / xhigh
    CLASSIFY {CODE_DEFECT, PSEUDOCODE_DEFECT, BASELINE_FAILURE, ENVIRONMENT_FAILURE}
    IF CODE_DEFECT: KEEP requirements; annotate corrected implementation guidance in pseudocode
    IF PSEUDOCODE_DEFECT: revise pseudocode WITHOUT changing BR01..BR22
    IF ENVIRONMENT_FAILURE: repair test environment, NEVER weaken tests or business rules
    HANDOFF revised pseudocode+root cause TO SAME OpenCode DeepSeek-V4.1-Flash/max session
    RERUN failing tests AND full suite
    REQUIRE final bounded review + acceptance
```

## P09 完成位超时测试时钟契约（GPT-6 Luna/xhigh根因修订）

|字段|约束|
|---|---|
|修订来源|TC49：1 failed/206 passed；有限假时钟耗尽，StopIteration被打码错误路径捕获|
|业务规则|BR15/16不变；不增加完成位边沿协议|
|生产时钟|现有monotonic；超时由截止时间判定|
|测试时钟|无限、单调递增；每次调用推进；不得依赖固定时钟调用次数|
|hold_seconds=0|允许保持诊断读取时钟；假时钟仍须可继续读取|
|超时信息|receipt包含完成位及未在配置秒数内置位的信息；不能因空StopIteration变为只有通用启动失败|

```text
FUNCTION test_done_timeout()
    clock=UNBOUNDED_MONOTONIC_FAKE(start=0, increment=0.5)
    GIVEN wait_done=True AND laser_done point exists AND every done read=False
          AND hold_seconds=0 AND settle_seconds=0 AND delayed_clear_disabled
    receipt=laser_mark(valid_committed_record, clock)
    ASSERT receipt.accepted == False
    ASSERT receipt.text includes completion-bit timeout reason
           # existing wording: 完成位...在Xs内未置位 is sufficient
    ASSERT writes == [True, False, False]
           # one start, normal reset, additional fault cleanup
    ASSERT True_count == 1 AND False_count == 2
    ASSERT no additional physical start and done stayed low until deadline
    DO NOT assert fixed clock-call count or fixed polling count
```
