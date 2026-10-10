# 校准到期后 NG 首件 + OK 二件样件验证 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 校准到期并点击“启动验证”后，本工位必须完成一件 NG 样件和一件 OK 样件（均不打码），样件按与正常产品相同的两腔硬件时序判定，之后才恢复正常测试与激光打码。

**Architecture:** 判定规则抽成 `app/calibration.py` 中的纯函数 `judge_sample`；`StationController` 让双测样件和正常产品一样进入 WAIT_2；`ui_replica.py` 只在样件周期测完（或半程 WAIT_2）时调用判定，并用一个统一的 `_prepare_sample_cycle` 在 StepCode=4 上升沿建立/重建样件周期。

**Tech Stack:** Python 3、PySide6（离屏 UI 测试）、pytest。

**Spec:** `docs/superpowers/specs/2026-10-10-sample-validation-design.md`

---

## 文件结构

| 文件 | 改动 | 职责 |
| --- | --- | --- |
| `app/calibration.py` | 修改 | `begin_validation(test_mode)` 冻结模式；新增 `SampleVerdict`、`judge_sample` |
| `app/station.py` | 修改 | `test_first`：双测样件第一腔 OK → WAIT_2 |
| `app/ui_theme.py` | 修改 | 新增 `sample_expected_ng` / `sample_expected_ok` 文案 |
| `app/ui_replica.py` | 修改 | 模式冻结/持久化；`_prepare_sample_cycle` 替代两个旧函数；`_handle_calibration_measurement` 改用 `judge_sample` |
| `tests/test_calibration.py` | 修改 | 判定表参数化测试、模式冻结测试 |
| `tests/test_station.py` | 修改 | 样件双测状态机测试 |
| `tests/test_ui.py` | 修改 | 模式冻结/持久化测试、StepCode 全流程测试；更新 3 个旧测试 |
| `README.md` | 修改 | 第 30、64 行改为新规则 |

已确认（读代码）：`StationPanel.reset()`（`ui_replica.py:898`）和 `MainWindow._apply_plc_reset()`（`ui_replica.py:1380`）都不触碰校准状态，spec 4.6 无需改代码。

已知限制（不在本计划范围）：`mark_samples=True` 时若样件判定为“不符合预期”且控制器停在 MARKING，下一次 StepCode=4 不会自动重建周期，需人工复位。默认配置 `mark_samples=False` 不受影响。

---

### Task 0: 建分支并跑基线

- [ ] **Step 1: 从设计文档分支切出实现分支**

```bash
cd C:/Users/Administrator/Documents/ATEQ-F620-Laser-2-stations
git checkout docs/sample-validation-design
git pull
git checkout -b feat/sample-validation
```

- [ ] **Step 2: 跑全量基线**

Run: `python -m pytest -q`
Expected: 全部通过（最近一次记录为 210 passed）。若有失败，先记录失败用例名，不要在本计划中修复。

---

### Task 1: 判定函数与模式冻结（`calibration.py`）

**Files:**
- Modify: `app/calibration.py`（`__init__`、`begin_validation`，文件末尾新增函数）
- Test: `tests/test_calibration.py`

- [ ] **Step 1: 写失败测试**

在 `tests/test_calibration.py` 顶部把导入改为：

```python
from app.calibration import Calibration, CalibrationPhase, SampleVerdict, judge_sample
from app.models import Result, StationId
```

文件末尾追加：

```python
def test_begin_validation_freezes_mode():
    cal = make_cal()
    cal.begin_validation("single")
    assert cal.test_mode == "single"
    default = make_cal()
    default.begin_validation()
    # 现场为双测：不传参数时按双测冻结。
    assert default.test_mode == "dual"


def test_begin_validation_rejects_unknown_mode():
    cal = make_cal()
    with pytest.raises(ValueError, match="single 或 dual"):
        cal.begin_validation("triple")
    assert cal.validation_started is False


W_NG, W_OK = CalibrationPhase.WAIT_NG, CalibrationPhase.WAIT_OK
OK, NG = Result.OK, Result.NG
PASSED, UNEXPECTED, INCOMPLETE = (SampleVerdict.PASSED, SampleVerdict.UNEXPECTED,
                                  SampleVerdict.INCOMPLETE)


@pytest.mark.parametrize("phase, mode, first, second, expected", [
    (W_NG, "dual", NG, None, PASSED),
    (W_NG, "single", NG, None, PASSED),
    (W_NG, "dual", OK, None, INCOMPLETE),
    (W_NG, "dual", OK, NG, PASSED),       # 只看最终结果
    (W_NG, "dual", OK, OK, UNEXPECTED),
    (W_NG, "single", OK, None, UNEXPECTED),
    (W_OK, "single", OK, None, PASSED),
    (W_OK, "dual", OK, None, INCOMPLETE),
    (W_OK, "dual", OK, OK, PASSED),
    (W_OK, "dual", NG, None, UNEXPECTED),
    (W_OK, "single", NG, None, UNEXPECTED),
    (W_OK, "dual", OK, NG, UNEXPECTED),
])
def test_judge_sample_table(phase, mode, first, second, expected):
    assert judge_sample(phase, mode, first, second) is expected


def test_judge_sample_without_first_result_is_incomplete():
    assert judge_sample(W_NG, "dual", None, None) is INCOMPLETE


def test_judge_sample_outside_validation_rejected():
    with pytest.raises(ValueError, match="样件验证阶段"):
        judge_sample(CalibrationPhase.COMPLETE, "dual", OK, OK)
    with pytest.raises(ValueError, match="single 或 dual"):
        judge_sample(W_NG, "triple", NG, None)
```

- [ ] **Step 2: 确认测试失败**

Run: `python -m pytest tests/test_calibration.py -q`
Expected: 收集阶段 `ImportError: cannot import name 'SampleVerdict'`。

- [ ] **Step 3: 实现**

`app/calibration.py` 导入改为：

```python
from .models import Result, StationId
```

`Calibration.__init__` 中 `self.sample_demand = "NG"` 之后加一行：

```python
        self.test_mode = "dual"
```

`begin_validation` 整体替换为：

```python
    def begin_validation(self, test_mode: str = "dual") -> None:
        """Arm the NG -> OK validation and freeze the station's test mode."""
        if not self.due:
            raise RuntimeError("当前未到校准周期")
        if self._clear_pending:
            raise RuntimeError("校准已完成，等待下一周期清除状态")
        if test_mode not in ("single", "dual"):
            raise ValueError("校准检测模式必须是 single 或 dual")
        self.test_mode = test_mode
        self.phase = CalibrationPhase.WAIT_NG
        self.ng_count = self.ok_count = 0
        self.sample_demand = "NG"
        self.countdown = self.required_samples
        self.locked = True
        self.validation_started = True
        self.remaining_seconds = 0.0
        self._last_tick = time.monotonic()
```

在 `class CalibrationPhase` 之后新增：

```python
class SampleVerdict(str, Enum):
    PASSED = "符合预期"
    UNEXPECTED = "不符合预期"
    INCOMPLETE = "未测完"
```

文件末尾新增：

```python
def judge_sample(phase: CalibrationPhase, test_mode: str,
                 first: Result | None, second: Result | None) -> SampleVerdict:
    """Judge one NG/OK sample cycle from its chamber results.

    The hardware fixes the chamber order: first = negative, second = positive.
    A first-chamber NG makes the instrument stop, so ``second`` only matters
    after a first-chamber OK in dual mode.  The sample passes when the cycle's
    final result equals the stage's expected result.
    """
    if phase not in (CalibrationPhase.WAIT_NG, CalibrationPhase.WAIT_OK):
        raise ValueError("当前不在样件验证阶段")
    if test_mode not in ("single", "dual"):
        raise ValueError("校准检测模式必须是 single 或 dual")
    if first is None:
        return SampleVerdict.INCOMPLETE
    if first is Result.NG or test_mode == "single":
        final = first
    elif second is None:
        return SampleVerdict.INCOMPLETE
    else:
        final = second
    expected = Result.NG if phase is CalibrationPhase.WAIT_NG else Result.OK
    return SampleVerdict.PASSED if final is expected else SampleVerdict.UNEXPECTED
```

- [ ] **Step 4: 确认测试通过**

Run: `python -m pytest tests/test_calibration.py -q`
Expected: 全部 PASS（原有 8 个 + 新增 16 个）。

- [ ] **Step 5: Commit**

```bash
git add app/calibration.py tests/test_calibration.py
git commit -m "feat(calibration): 冻结样件检测模式并新增 judge_sample 判定

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: 双测样件进入第二腔（`station.py`）

**Files:**
- Modify: `app/station.py:128-134`（`test_first` 的 OK 分支）
- Test: `tests/test_station.py`

- [ ] **Step 1: 写测试**

在 `tests/test_station.py` 的 `test_sample_cycle_marks_when_enabled` 之后追加：

```python
def test_sample_dual_first_ok_waits_for_positive(station_parts):
    """OK 样件与正常产品同一时序：负压 OK 后必须等正压，不能提前结束。"""
    controller, repository, marker, plc, _ = station_parts
    controller.start_cycle(make_selection(mode="dual"), sample=True)
    controller.ateq.result = Result.OK
    controller.test_first()
    assert controller.phase is Phase.WAIT_2
    controller.test_second()
    assert controller.phase is Phase.COMPLETE
    assert controller.record.second.result is Result.OK
    assert marker.intents == set()


def test_sample_dual_first_ng_ends_without_second(station_parts):
    controller, repository, marker, plc, _ = station_parts
    controller.start_cycle(make_selection(mode="dual"), sample=True)
    controller.ateq.result = Result.NG
    controller.test_first()
    assert controller.phase is Phase.COMPLETE
    with pytest.raises(RuntimeError, match="当前状态"):
        controller.test_second()
    assert marker.intents == set()


def test_sample_dual_second_fault_enters_fault(station_parts):
    controller, repository, marker, plc, _ = station_parts
    controller.start_cycle(make_selection(mode="dual"), sample=True)
    controller.test_first()
    controller.ateq.connected = False
    with pytest.raises(ConnectionError):
        controller.test_second()
    assert controller.phase is Phase.FAULT
    assert controller.recovery_required is True
    assert marker.intents == set()
```

- [ ] **Step 2: 确认第一个测试失败**

Run: `python -m pytest tests/test_station.py -q -k sample_dual`
Expected: `test_sample_dual_first_ok_waits_for_positive` FAIL（`Phase.COMPLETE is not Phase.WAIT_2`）。另外两个用例可能已经通过，它们是行为保护。

- [ ] **Step 3: 实现**

`app/station.py` 中 `test_first` 的 OK 分支：

```python
            if measurement.result is Result.OK:
                # 双测：两测都合格才进入打码；单测：一次合格即打码。
                # 样件周期默认不打码（mark_samples 配置可放开）。
                if self.record.sample_cycle and not self._sample_marking_enabled():
                    self.phase = Phase.COMPLETE
                else:
                    self.phase = Phase.MARKING if self.record.test_mode == "single" else Phase.WAIT_2
```

替换为：

```python
            if measurement.result is Result.OK:
                # 双测（含样件）第一腔 OK 后仪器继续测正压，必须等第二腔；
                # 单测一次合格即打码，样件周期默认不打码（mark_samples 可放开）。
                if self.record.test_mode == "dual":
                    self.phase = Phase.WAIT_2
                elif self.record.sample_cycle and not self._sample_marking_enabled():
                    self.phase = Phase.COMPLETE
                else:
                    self.phase = Phase.MARKING
```

`test_second` 已对样件处理不打码，不改。

- [ ] **Step 4: 确认通过（含回归）**

Run: `python -m pytest tests/test_station.py -q`
Expected: 全部 PASS。

- [ ] **Step 5: Commit**

```bash
git add app/station.py tests/test_station.py
git commit -m "fix(station): 双测样件第一腔 OK 后等待第二腔，避免腔序错位

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: 启动验证时冻结并持久化模式（`ui_replica.py`）

**Files:**
- Modify: `app/ui_replica.py`：`_single_mode_marking_unsupported`（约 1257 行）、`start_calibration`（约 2503 行）、`_persist_calibration` / `_restore_calibration`（约 2259/2294 行）、`StationPanel.refresh` 中 `start_validation_button` 块之后（约 1039 行）
- Test: `tests/test_ui.py`

- [ ] **Step 1: 写失败测试并更新两个旧测试**

在 `tests/test_ui.py` 的 `test_calibration_ng_ok_validation` 之前追加：

```python
def test_start_validation_freezes_button_mode(window):
    card = window.cards[0]
    card.part_no.setCurrentText(PART)
    card.mode_button.setChecked(False)
    window.mark_calibration_due(window.station)
    assert window.start_calibration(window.station) is True
    calibration = window.calibration[window.station]
    assert calibration.test_mode == "single"
    assert card.controller.record.test_mode == "single"
    card.refresh()
    assert not card.mode_button.isEnabled()
    # 验证期间按钮即使被程序改动，refresh 也恢复为冻结模式。
    card.mode_button.setChecked(True)
    card.refresh()
    assert card.mode_button.isChecked() is False


def test_calibration_mode_persisted_and_restored(window):
    card = window.cards[0]
    card.part_no.setCurrentText(PART)
    card.mode_button.setChecked(False)
    window.mark_calibration_due(window.station)
    window.start_calibration(window.station)
    window._persist_calibration()
    calibration = window.calibration[window.station]
    calibration.test_mode = "dual"
    window._restore_calibration()
    assert calibration.test_mode == "single"


def test_calibration_restore_old_snapshot_defaults_dual(window):
    import json

    calibration = window.calibration[window.station]
    calibration.test_mode = "single"
    window._calibration_state_path.write_text(json.dumps({window.station.value: {
        "due": True, "locked": True, "validation_started": True,
        "phase": "WAIT_OK", "ng_count": 1, "ok_count": 0,
        "remaining_seconds": 0.0, "period_seconds": 7200,
        "clear_pending": False, "sample_demand": "OK", "audit_events": [],
    }}), encoding="utf-8")
    window._restore_calibration()
    assert calibration.phase is CalibrationPhase.WAIT_OK
    assert calibration.test_mode == "dual"
```

更新 `test_live_default_calibration_sample_still_starts`：按钮为双测，样件现在按双测冻结。把

```python
    assert selection.test_mode == "single"
```

改为 `assert selection.test_mode == "dual"`，把

```python
    assert record.test_mode == "single"
```

改为 `assert record.test_mode == "dual"`。

更新 `test_live_calibration_site_guard_blocks_mark_samples`：双测样件打码与双测生产一致，不再拦截；守卫只拦截单测打码。把该测试中的

```python
    card.mode_button.setChecked(True)
```

改为 `card.mode_button.setChecked(False)`。

- [ ] **Step 2: 确认失败**

Run: `python -m pytest tests/test_ui.py -q -k "freezes_button_mode or mode_persisted or old_snapshot or live_default_calibration or site_guard"`
Expected: `freezes_button_mode`、`mode_persisted`、`live_default_calibration` FAIL（样件仍写死 single / 不持久化）；`old_snapshot` 可能已通过（`__init__` 默认 dual），保留为保护。

- [ ] **Step 3: 实现**

(a) `_single_mode_marking_unsupported` 中

```python
        mode = "single" if sample else ("dual" if card.mode_button.isChecked()
                                        else "single")
```

替换为：

```python
        button_mode = "dual" if card.mode_button.isChecked() else "single"
        calibration = self.calibration[card.station]
        # 样件按启动验证时冻结的模式运行；启动前（守卫先于冻结调用）看按钮。
        mode = (calibration.test_mode if sample and calibration.validation_started
                else button_mode)
```

并把 docstring 中 “Sample sites (``sample=True``) always freeze ``test_mode="single"`` and” 一句改为 “Sample sites (``sample=True``) use the mode frozen by Start Validation and”。

(b) `start_calibration` 中 `calibration.begin_validation()` 改为：

```python
        calibration.begin_validation("dual" if card.mode_button.isChecked() else "single")
```

同一函数里构造 `CycleSelection` 的 `"single",` 参数改为 `calibration.test_mode,`。

(c) `_persist_calibration` 快照字典在 `"sample_demand": cal.sample_demand,` 之后加：

```python
                "test_mode": cal.test_mode,
```

`_restore_calibration` 中 `cal.sample_demand = str(state.get("sample_demand", ""))` 之后加：

```python
                mode = str(state.get("test_mode", "dual"))
                # 旧快照没有该字段：现场为双测，按双测恢复。
                cal.test_mode = mode if mode in ("single", "dual") else "dual"
```

(d) `StationPanel.refresh` 中 `start_validation_button` 的 `if` 块结束后（`if hasattr(self, "cancel_calibration_button"):` 之前）插入：

```python
        if hasattr(self, "mode_button"):
            # 验证期间单/双测是冻结契约：按钮置灰并显示冻结值。
            mode_locked = bool(calibration and calibration.validation_started)
            if mode_locked:
                frozen_dual = calibration.test_mode == "dual"
                if self.mode_button.isChecked() != frozen_dual:
                    self.mode_button.setChecked(frozen_dual)
            self.mode_button.setEnabled(not mode_locked)
```

- [ ] **Step 4: 确认通过**

Run: `python -m pytest tests/test_ui.py -q`
Expected: 全部 PASS（旧的 `test_calibration_ng_ok_validation` 此时仍通过，Task 4 会替换它）。

- [ ] **Step 5: Commit**

```bash
git add app/ui_replica.py tests/test_ui.py
git commit -m "feat(ui): 启动验证冻结单/双测模式并持久化

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: StepCode 派发与判定（`ui_replica.py`、`ui_theme.py`）

**Files:**
- Modify: `app/ui_theme.py:115` 之后（MESSAGES 新增两条）
- Modify: `app/ui_replica.py`：导入（第 32 行）、`_handle_calibration_measurement`（约 820 行）、`_handle_live_stepcode`（约 1434 行）、删除 `_restore_pending_calibration_cycle` 与 `_begin_ok_validation_cycle`（约 1515-1601 行），新增 `_prepare_sample_cycle`
- Test: `tests/test_ui.py`

- [ ] **Step 1: 写失败测试**

删除 `tests/test_ui.py` 中的 `test_calibration_ng_ok_validation`（它直接调用将被删除的 `_begin_ok_validation_cycle`，由下面的主流程测试取代）。在原位置追加：

```python
def _edge(window, qapp, result=None, timeout_s=3.0):
    """模拟一次 StepCode 0→4 上升沿，等待异步测试与 _finish_test 处理完。"""
    card = window.cards[0]
    if result is not None:
        card.controller.ateq.result = result
    window._handle_live_stepcode(window.station, 0)
    window._handle_live_stepcode(window.station, 4)
    deadline = time.monotonic() + timeout_s
    while getattr(card, "_test_worker_running", False) and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.01)
    # test_finished 由工作线程发出，排队到 GUI 线程执行 _finish_test。
    for _ in range(10):
        qapp.processEvents()
        time.sleep(0.005)
    return card


def _start_sample_validation(window, dual=True):
    card = window.cards[0]
    card.part_no.setCurrentText(PART)
    card.staff.setCurrentText("张三")
    card.mode_button.setChecked(dual)
    window.mark_calibration_due(window.station)
    assert window.start_calibration(window.station) is True
    return card, window.calibration[window.station]


def test_sample_validation_main_flow_then_production_marks(window, qapp):
    card, cal = _start_sample_validation(window)
    marker = window.marker
    _edge(window, qapp, Result.NG)              # NG 件：负压 NG，仪器终止
    assert cal.phase is CalibrationPhase.WAIT_OK
    _edge(window, qapp, Result.OK)              # OK 件负压
    assert card.controller.phase is Phase.WAIT_2
    assert card.controller.record.sample_cycle is True
    ok_cycle = card.controller.record.cycle_id
    # 腔序错位回归：负压 OK 不能放行工位。
    assert cal.phase is CalibrationPhase.WAIT_OK
    assert cal.validation_started and cal.locked
    _edge(window, qapp, Result.OK)              # OK 件正压，落在同一样件周期
    rows = [r for r in window.repository.records.values() if r.cycle_id == ok_cycle]
    assert rows and rows[0].second is not None and rows[0].second.result is Result.OK
    assert cal.phase is CalibrationPhase.COMPLETE
    assert not cal.validation_started and not cal.locked
    assert cal.remaining_seconds == cal.period_seconds
    assert marker.intents == set()               # 样件全程不打码
    card.refresh()
    assert card.mode_button.isEnabled()
    _edge(window, qapp, Result.OK)              # 生产件负压
    assert card.controller.record.sample_cycle is False
    assert card.controller.phase is Phase.WAIT_2
    _edge(window, qapp, Result.OK)              # 生产件正压 → 打码
    assert card.controller.phase is Phase.COMPLETE
    assert card.controller.record.marked is True
    assert len(marker.intents) == 1


def test_waiting_ng_but_sample_passes_both_chambers_retries_ng(window, qapp):
    card, cal = _start_sample_validation(window)
    _edge(window, qapp, Result.OK)              # 负压 OK
    assert card.controller.phase is Phase.WAIT_2
    _edge(window, qapp, Result.OK)              # 正压 OK → 不符合预期
    assert card.controller.phase is Phase.COMPLETE
    assert cal.phase is CalibrationPhase.WAIT_NG
    assert card._error_key == "sample_expected_ng"
    failed_cycle = card.controller.record.cycle_id
    _edge(window, qapp, Result.NG)              # 自动按 NG 阶段重测，不卡死
    assert card.controller.record.cycle_id != failed_cycle
    assert card.controller.record.sample_cycle is True
    assert cal.phase is CalibrationPhase.WAIT_OK
    assert card._error_key is None


def test_ng_sample_negative_ok_positive_ng_passes(window, qapp):
    card, cal = _start_sample_validation(window)
    _edge(window, qapp, Result.OK)
    _edge(window, qapp, Result.NG)
    assert cal.phase is CalibrationPhase.WAIT_OK
    assert card._error_key is None


def test_ok_sample_positive_ng_retries_from_negative(window, qapp):
    card, cal = _start_sample_validation(window)
    _edge(window, qapp, Result.NG)
    _edge(window, qapp, Result.OK)
    _edge(window, qapp, Result.NG)              # 正压 NG → 不符合预期
    assert cal.phase is CalibrationPhase.WAIT_OK
    assert card._error_key == "sample_expected_ok"
    failed_cycle = card.controller.record.cycle_id
    _edge(window, qapp, Result.OK)              # 重测从负压开始：新周期
    assert card.controller.record.cycle_id != failed_cycle
    assert card.controller.phase is Phase.WAIT_2
    assert card.controller.record.second is None
    _edge(window, qapp, Result.OK)
    assert cal.phase is CalibrationPhase.COMPLETE


def test_sample_fault_skips_judgement_and_retries_after_reset(window, qapp):
    card, cal = _start_sample_validation(window)
    _edge(window, qapp, Result.NG)
    _edge(window, qapp, Result.OK)              # OK 件负压 OK
    card.controller.ateq.connected = False
    _edge(window, qapp)                          # 正压通讯失败 → FAULT
    assert card.controller.phase is Phase.FAULT
    assert cal.phase is CalibrationPhase.WAIT_OK  # 不判定、不推进
    _edge(window, qapp)                          # FAULT 不自动处理
    assert card.controller.phase is Phase.FAULT
    card.controller.ateq.connected = True
    card.reset()
    assert card.controller.phase is Phase.IDLE
    assert cal.phase is CalibrationPhase.WAIT_OK and cal.test_mode == "dual"
    _edge(window, qapp, Result.OK)
    assert card.controller.record.sample_cycle is True
    assert card.controller.phase is Phase.WAIT_2


def test_due_without_start_validation_blocks_production(window, qapp):
    card = window.cards[0]
    card.part_no.setCurrentText(PART)
    window.mark_calibration_due(window.station)
    _edge(window, qapp, Result.OK)
    assert card.controller.record is None
    assert card.controller.phase is Phase.IDLE
    assert len(window.repository.records) == 0
    assert window.marker.intents == set()


def test_sample_expectation_catalog_all_languages():
    from app.ui_theme import UiTextCatalog

    for language in UiTextCatalog.LANGUAGES:
        for key in ("sample_expected_ng", "sample_expected_ok"):
            text = UiTextCatalog.message(language, key)
            assert text and key not in text
```

- [ ] **Step 2: 确认失败**

Run: `python -m pytest tests/test_ui.py -q -k "sample or due_without"`
Expected: `main_flow`（在 `Phase.WAIT_2` 断言处，旧桥接建的是单测周期）、`retries_ng`（卡死，记录未变）、`negative_ok_positive_ng`、`positive_ng_retries`、`fault_skips`、`catalog` FAIL；`due_without_start_validation` 可能已通过（现有拦截行为），保留为保护。

- [ ] **Step 3: 新增文案**

`app/ui_theme.py` 中 `"calibration_error": {...},` 这一行之后插入：

```python
        "sample_expected_ng": {"中文": "样件验证不符合预期：要求 NG，实际 OK；请重新放 NG 首件测试", "English": "Sample check failed: expected NG, got OK; retest the NG first piece", "Français": "Échantillon non conforme : NG attendu, OK obtenu ; retestez la pièce NG"},
        "sample_expected_ok": {"中文": "样件验证不符合预期：要求 OK，实际 NG；请重新放 OK 二件，从负压开始重测", "English": "Sample check failed: expected OK, got NG; retest the OK second piece from the negative chamber", "Français": "Échantillon non conforme : OK attendu, NG obtenu ; retestez la pièce OK depuis la chambre négative"},
```

- [ ] **Step 4: 判定改用 `judge_sample`**

`app/ui_replica.py` 第 32 行导入改为：

```python
from .calibration import Calibration, CalibrationPhase, SampleVerdict, judge_sample
```

`StationPanel._handle_calibration_measurement` 整体替换为：

```python
    def _handle_calibration_measurement(self):
        """Judge a sample cycle only after the instrument has finished it.

        Chamber order follows the hardware (first = negative, second =
        positive).  A faulted cycle is never judged, and a half-finished dual
        cycle (WAIT_2) only updates the prompt, so the positive-pressure
        StepCode=4 of an OK sample cannot be taken for the next production part.
        """
        calibration = self._calibration()
        record = self.controller.record
        if (calibration is None or not calibration.validation_started
                or record is None or not record.sample_cycle):
            return
        window = self.window()
        trace = getattr(window, "_live_trace", None)
        phase = self.controller.phase
        if phase is Phase.FAULT:
            if trace is not None:
                trace(f"CAL_SAMPLE_NOT_JUDGED station={self.station.value} reason=fault "
                      f"stage={calibration.sample_demand} cycle={record.cycle_id}")
            return
        if phase not in (Phase.WAIT_2, Phase.COMPLETE, Phase.MARKING):
            return
        first = record.first.result if record.first is not None else None
        second = record.second.result if record.second is not None else None
        verdict = judge_sample(calibration.phase, calibration.test_mode, first, second)
        if trace is not None:
            measurement = record.second or record.first
            if measurement is not None:
                trace(f"{self.station.value} TEST_RESULT result={measurement.result.value} "
                      f"pressure={measurement.pressure}{measurement.pressure_unit} "
                      f"leakage={measurement.leakage}{measurement.leakage_unit} "
                      f"raw={measurement.raw_frame.hex()}")
            trace(f"CAL_SAMPLE_VERDICT station={self.station.value} "
                  f"stage={calibration.sample_demand} mode={calibration.test_mode} "
                  f"first={first.value if first else ''} second={second.value if second else ''} "
                  f"verdict={verdict.name} cycle={record.cycle_id}")
        language = getattr(window, "_language", "中文")
        status = getattr(window, "calibration_status", None)
        if verdict is SampleVerdict.INCOMPLETE:
            if status is not None:
                status.setText({
                    "中文": f"工位 {self.station.value}：负压 OK，等待正压",
                    "English": f"Station {self.station.value}: negative OK, waiting for positive",
                    "Français": f"Poste {self.station.value} : négatif OK, attente du positif",
                }[language])
            return
        if verdict is SampleVerdict.UNEXPECTED:
            # 记录已落库；阶段不推进，下一次 StepCode=4 由 _prepare_sample_cycle 重建同阶段周期。
            self._error_key = ("sample_expected_ng"
                               if calibration.phase is CalibrationPhase.WAIT_NG
                               else "sample_expected_ok")
            if status is not None:
                status.setText(UiTextCatalog.message(language, self._error_key))
            return
        window.on_calibration_sample(calibration.sample_demand, self.station)
```

- [ ] **Step 5: 统一样件周期入口**

删除 `MainWindow._restore_pending_calibration_cycle` 和 `MainWindow._begin_ok_validation_cycle` 两个方法，在原位置新增：

```python
    def _prepare_sample_cycle(self, card) -> bool:
        """Open the next NG/OK sample cycle on a fresh StepCode=4.

        One path for both stages, for retries after an unexpected result and
        after a restart.  A finished sample cycle is archived first; a faulted
        cycle is left for the operator's reset so its recovery record survives.
        """
        calibration = self.calibration[card.station]
        controller = card.controller
        if (not calibration.validation_started
                or calibration.phase not in (CalibrationPhase.WAIT_NG,
                                             CalibrationPhase.WAIT_OK)):
            return False
        if controller.phase is Phase.FAULT:
            self._live_trace(
                f"CAL_SAMPLE_CYCLE_BLOCKED station={card.station.value} "
                f"reason=fault_reset_required")
            return False
        if controller.phase not in (Phase.IDLE, Phase.COMPLETE):
            return False
        part_no = card.part_no.currentText().strip()
        if not part_no:
            self._live_trace(
                f"CAL_SAMPLE_CYCLE_BLOCKED station={card.station.value} no selected model")
            return False
        if self._single_mode_marking_unsupported(card, sample=True):
            self._live_trace(
                f"CAL_SAMPLE_CYCLE_BLOCKED station={card.station.value} "
                f"reason=single_mode_unsupported")
            return False
        try:
            config = self._model_for(part_no)
        except Exception as exc:
            self._live_trace(
                f"CAL_SAMPLE_CYCLE_BLOCKED station={card.station.value} "
                f"{type(exc).__name__}: {exc}")
            return False
        if controller.phase is Phase.COMPLETE:
            previous = controller.record.cycle_id if controller.record is not None else ""
            controller.reset()
            self._live_trace(
                f"CAL_SAMPLE_CYCLE_ARCHIVED station={card.station.value} cycle={previous}")
        selection = CycleSelection(card.station, part_no,
                                   card.staff.currentText().strip() or "Operator",
                                   calibration.test_mode, str(config.ateq_program),
                                   date_scheme=self._resolved_date_scheme(config))
        controller.start_cycle(selection, sample=True)
        card.refresh()
        self._live_trace(
            f"CAL_SAMPLE_CYCLE_READY station={card.station.value} "
            f"stage={calibration.sample_demand} mode={calibration.test_mode} "
            f"cycle={controller.record.cycle_id}")
        return True
```

`_handle_live_stepcode` 中：

```python
        if (phase not in (Phase.READY, Phase.WAIT_2)
                and self._restore_pending_calibration_cycle(card)):
```

改为：

```python
        if (phase not in (Phase.READY, Phase.WAIT_2)
                and self._prepare_sample_cycle(card)):
```

并删除派发块中的这两行（样件周期现在都在上面建好，进入 READY 分支）：

```python
            elif self._begin_ok_validation_cycle(card):
                card.first()
```

- [ ] **Step 6: 确认没有残留引用**

Run: `git grep -n "_restore_pending_calibration_cycle\|_begin_ok_validation_cycle"`
Expected: 无输出。

- [ ] **Step 7: 确认通过**

Run: `python -m pytest tests/test_ui.py -q`
Expected: 全部 PASS。

- [ ] **Step 8: Commit**

```bash
git add app/ui_replica.py app/ui_theme.py tests/test_ui.py
git commit -m "fix(ui): 样件按两腔时序判定，不符合预期自动同阶段重测

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: README 改为新规则

**Files:**
- Modify: `README.md:30`、`README.md:64`

- [ ] **Step 1: 改文档**

第 30 行：

```markdown
- NG/OK 样件验证逻辑与校准时效倒计时与 Morocco 项目完全一致；样件周期默认不打码。
```

替换为：

```markdown
- 校准到期后须点“启动验证”，再做一件 NG 首件 + 一件 OK 二件（均不打码），之后才恢复生产与打码；未完成时 PLC 仍可启动测试，但不建周期、不写库、不出打码文本。
  样件与产品同一硬件时序：NG 首件最终结果为 NG 即通过（负压 NG 仪器终止，或负压 OK 后正压 NG）；OK 二件双测须负压、正压都 OK。
  不符合预期时保留记录、不推进，下一次 PLC 启动自动按同阶段重测；仪器报警（含压力高/低）按故障处理，复位后重测。单/双测在启动验证时冻结。
```

第 64 行 `├── calibration.py     # NG→OK 样件验证 + 时效倒计时（与 Morocco 一致）` 改为：

```markdown
├── calibration.py     # NG→OK 样件验证、judge_sample 判定 + 时效倒计时
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: README 更新样件验证规则

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: 全量验证并推送

- [ ] **Step 1: 全量测试**

Run: `python -m pytest -q`
Expected: 全部通过，数量 = Task 0 基线 + 本计划新增（calibration 16、station 3、ui 3+7，减去删除的 1 个）。

- [ ] **Step 2: 推送分支**

```bash
git push -u origin feat/sample-validation
```

不直接推 main；PR 在用户确认后再开。

---

### Task 7: 现场验证（需要用户在场，部署前先确认）

部署到 A/B 工位机 `D:\ateq` 属于生产变更：先备份现有 `D:\ateq\app`，经用户确认后再覆盖，并保留回滚路径。按 spec 第 6 节在实机上确认：

- [ ] NG 件只出现一次 StepCode=4；OK 件出现两次，`live_trace` 中两次的 `cycle=` 一致（看 `CAL_SAMPLE_VERDICT` 与 `ATEQ_STEP_4_RISE`）。
- [ ] 样件期间激光机不动作，激光文本文件始终为空。
- [ ] 验证通过后第一件产品两腔数据与激光内容一致。
- [ ] 故意让 NG 件测出 OK（或 OK 件某腔 NG）：界面显示“样件验证不符合预期”，下一次启动自动重测同阶段。
- [ ] 实测一次重启：验证进行到 WAIT_OK 时重启程序，确认阶段与冻结模式恢复，下一次启动按 OK 阶段建周期。
