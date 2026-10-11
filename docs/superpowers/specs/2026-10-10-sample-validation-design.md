# 校准到期后 NG 首件 + OK 二件样件验证 设计

日期：2026-10-10
范围：`app/calibration.py`、`app/station.py`、`app/ui_replica.py` 及对应测试；不改 PLC 程序。

## 1. 目标

校准倒计时到期后，本工位必须先完成一件 NG 样件和一件 OK 样件的测试，才能恢复正常产品的测试与激光打码。

- 样件周期只测试、落库，不打码（`mark_samples` 保持默认关闭）。
- 验证未完成时 PLC 仍可启动测试，但上位机不建周期、不写库、不生成激光文本，生产无法继续。
- PLC 对样件没有专门控制；NG/OK 样件与正常产品走同一套硬件时序。

## 2. 现场事实（2026-10-10 用户确认）

- 腔序：第一腔 = 负压，第二腔 = 正压，每腔对应一次 StepCode=4。
- NG 件：负压 NG 时仪器自行终止，只有一次 StepCode=4。
- OK 件：负压 OK 后继续测正压，共两次 StepCode=4，与正常产品一致。
- 压力高/低报警等仪器报警不视为 NG 验证通过，按故障处理，复位后重测。

## 3. 现状问题

1. 样件周期写死为 `"single"`，OK 件负压 OK 即判通过并放行；紧随的正压 StepCode=4 被当成下一件生产件的第一腔，腔序错位。
2. 等 NG 件时测出 OK：控制器停在 COMPLETE 且有记录，restore 要求无记录、bridge 只处理 WAIT_OK、生产分支被 `locked` 拦截，工位卡死，只能人工复位。
3. 判定在每腔测完后立即执行，取 `record.second or record.first`；改为两腔后，若第二腔进 FAULT，会误用第一腔结果判定。

## 4. 设计

### 4.1 校准状态（`calibration.py`）

- `begin_validation(test_mode)`：点“启动验证”时按工位模式按钮冻结 `"single"`/`"dual"`，非法值抛 `ValueError`。
- `test_mode` 加入 `_persist_calibration` / `_restore_calibration` 快照；旧快照缺该字段时按 `"dual"` 恢复（现场为双测）。
- 验证期间（`validation_started`）模式按钮置灰。
- 阶段 WAIT_NG → WAIT_OK → COMPLETE、倒计时、`locked`、指示灯、`clear_pending` 语义不变。

### 4.2 判定函数（新增纯函数 `judge_sample`，放在 `calibration.py`）

输入：当前阶段、冻结模式、第一腔结果、第二腔结果（未测为 `None`）。
输出：`PASSED` / `UNEXPECTED` / `INCOMPLETE`。

| 阶段 | 模式 | 第一腔 | 第二腔 | 判定 |
| --- | --- | --- | --- | --- |
| WAIT_NG | 任意 | NG | — | PASSED |
| WAIT_NG | dual | OK | None | INCOMPLETE |
| WAIT_NG | dual | OK | NG | PASSED（只看最终结果） |
| WAIT_NG | dual | OK | OK | UNEXPECTED |
| WAIT_NG | single | OK | — | UNEXPECTED |
| WAIT_OK | single | OK | — | PASSED |
| WAIT_OK | dual | OK | None | INCOMPLETE |
| WAIT_OK | dual | OK | OK | PASSED |
| WAIT_OK | 任意 | 任一腔 NG | | UNEXPECTED |

仪器报警不进入判定：控制器进 FAULT，按 4.4 处理。

### 4.3 样件周期（`station.py`）

- 样件周期按冻结模式运行：双测第一腔 OK → WAIT_2；单测第一腔 OK → COMPLETE；第一腔 NG → COMPLETE。
- 第二腔结束后一律 COMPLETE（`mark_samples` 关闭时不进 MARKING）。
- 样件记录照常落库，保留 `sample_cycle` 标记。
- 正常产品流程不变。

### 4.4 StepCode=4 派发与重测（`ui_replica.py`）

仅改 `validation_started` 为真时的路径；正常生产派发不变。

- READY → 测第一腔；WAIT_2 → 测第二腔（OK 件正压落在同一样件周期）。
- COMPLETE → 归档上一周期，按当前阶段新建样件周期并测第一腔。
- IDLE 且无记录 → 新建样件周期并测第一腔。
- FAULT → 不自动处理，记录日志，等待界面复位或 PLC 面板复位；复位后下一次 StepCode=4 按同一阶段新建周期。
- 新建样件周期使用 `calibration.test_mode`。
- `_restore_pending_calibration_cycle` 与 `_begin_ok_validation_cycle` 合并为 `_prepare_sample_cycle`，WAIT_NG/WAIT_OK 共用，修复问题 2。

### 4.5 判定时机（`_finish_test` → `_handle_calibration_measurement`）

- 控制器 FAULT：跳过判定（修复问题 3）。
- 控制器 WAIT_2：INCOMPLETE，仅更新界面提示（如“负压 OK，等待正压”）。
- 控制器 COMPLETE：调用 `judge_sample`。
  - PASSED：调用 `calibration.sample()`。NG 通过 → WAIT_OK；OK 通过 → 沿用现有逻辑归档周期、清灯、重启倒计时、恢复生产。
  - UNEXPECTED：不推进阶段，记录已落库；界面提示“验证不符合预期：要求 X，实际 Y”；下一次 StepCode=4 按 4.4 自动重测同阶段，OK 件从负压重新开始。

### 4.6 复位与重启

- 复位只归档当前样件周期，不改变校准阶段与冻结模式（实现时须确认 `reset()` / `_apply_plc_reset()` 不触碰校准状态）。
- 重启时样件周期停在 WAIT_2：journal 恢复为 FAULT（需恢复），复位后按同一阶段重测；校准阶段与模式从持久化快照恢复。

### 4.7 生产拦截

不改。验证未完成时 `locked` 拦截 `_prepare_stepcode_production_cycle`：不建周期、不写库、不生成激光文本。

### 4.8 入口

保留“启动验证”按钮，不做到期自动进入。未点按钮时 StepCode=4 被忽略（现有行为）。

## 5. 测试

全部不依赖硬件，写入现有测试文件。

**`tests/test_calibration.py`**
- 4.2 判定表每行一条参数化用例。
- `begin_validation("dual")` 冻结后持久化/恢复，`test_mode` 不变；旧快照缺字段恢复为 `"dual"`。
- 非法 `test_mode` 报错。

**`tests/test_station.py`**（模拟 ATEQ 顺序返回结果）
- 样件双测负压 OK → WAIT_2。
- 样件双测负压 OK + 正压 OK → COMPLETE，不进 MARKING。
- 样件负压 NG → COMPLETE，不测第二腔。
- 样件第二腔压力报警 → FAULT。
- 正常产品现有用例全部通过（回归）。

**`tests/test_ui.py`**（离屏 UI + 模拟 StepCode=4）
- 主流程：到期 → 启动验证 → NG 件（负压 NG）→ OK 件（负压 OK、正压 OK）→ 生产件正常打码；样件全程无激光文本；第二次 StepCode=4 落在 OK 件正压；验证后下一次 StepCode=4 建生产周期。
- 腔序错位回归：OK 件负压 OK 后验证未完成、倒计时未重启（现代码应失败，修改后通过）。
- 等 NG 件测出 OK+OK → UNEXPECTED，下一次 StepCode=4 自动按 NG 阶段重测。
- NG 件负压 OK + 正压 NG → PASSED。
- OK 件正压 NG → UNEXPECTED，重测从负压开始。
- 样件 FAULT：不判定、不推进；复位后同阶段重测，模式保持冻结值。
- 到期未点启动验证：StepCode=4 不建周期、不写库、无激光文本。

## 6. 现场验证（A/B 工位实机）

- NG 件只出现一次 StepCode=4；OK 件出现两次，且两次日志周期 ID 一致。
- 样件期间激光机不动作，激光文本文件始终为空。
- 验证通过后第一件产品两腔数据与激光内容一致。
