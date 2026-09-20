# ATEQ F620 双腔气密检测 + 激光打码（双工位）

两台 ATEQ F620 双腔（正负压）气密检测设备，测试合格后直接**激光打码**（不再打印标签+扫码）。
两个工位**完全独立**：同一套代码分别装在 A、B 两台电脑上，每个实例只控制本工位
（本工位 PLC + 本工位 ATEQ + 本工位激光 + 本工位界面 + 本机 MySQL）。

- 基线：[xiezhong-Morocco-2-stations](https://github.com/huaweixiong-debug/xiezhong-Morocco-2-stations)
  （根目录 `app/`，现场验收过的 Python 版；S7-200 SMART snap7 适配器、ATEQ 串口适配器、
  状态机/周期日志/预检体系均复用）
- 激光打码接口移植自：[xiezhong-heating](https://github.com/huaweixiong-debug/xiezhong-heating)
  （`laser_files.py` 原子写文件 + PLC 启动位脉冲 + 延时清空）

## 生产流程（无扫码）

```
放件 → PLC 启动(上升沿) → 第一次测试(正压)
     → 正压 NG → 仪器自身终止检测，记录直接结束（不打码）
     → 正压 OK → 第二次测试(负压)
              → 负压 NG → 记录结束（不打码）
              → 负压 OK → 输出打码文本(8字段) → PLC 激光启动位脉冲
                       → 激光机打码 → 10 秒后文本自毁清空 → 周期完成
```

- 周期由 **ATEQ StepCode=4 硬件边沿**驱动派发（上位机只监控，不启动测试）。
- 周期身份：`工位-时间戳-短UUID`（无扫码工序，无产品序列号）。
- 打码内容（写入激光软件监听的 TXT，GBK 编码，每行一个字段，可配模板）：
  `日期、产品型号、测试压力1、泄漏量1、测试压力2、泄漏量2、结果、操作工`。
- 打码文本内容以**数据库提交后的记录回读**生成，保证"打的数据 = 存的数据"。
- 第一腔 NG 时 ATEQ 仪器自行终止、不会有第二次结果，上位机立即结束该周期（Morocco 原版在此处会傻等第二次结果，已修正）。
- NG/OK 样件验证逻辑与校准时效倒计时与 Morocco 项目完全一致；样件周期默认不打码。

## 部署架构

```
工位A电脑（实例A）                          工位B电脑（实例B）
┌──────────────────────────┐          ┌──────────────────────────┐
│ 程序实例A（station="A"）  │          │ 程序实例B（station="B"）  │
│  MySQL 8.4 (localhost)   │          │  MySQL 8.4 (localhost)   │
│  PLC A (snap7)           │   网线    │  PLC B (snap7)           │
│  ATEQ A (COMx)           │ ←可选只读→ │  ATEQ B (COMx)           │
│  打码TXT → 激光A软件 → 激光机A│        │  打码TXT → 激光B软件 → 激光机B│
└──────────────────────────┘          └──────────────────────────┘
```

两台电脑的数据库相互独立、互不写数据；网线仅用于可选的跨工位只读查询/远程维护。

## 代码结构

```
app/
├── main.py            # CLI：--mode/--config/--live-ui/--preflight/--diagnose/--ateq-test
├── config.py          # Settings（单工位 station=A/B、激光参数、点位表路径）
├── points.py          # PLC 点位表加载（config/points.toml，M<byte>.<bit>）
├── plc.py             # FakePlc / Snap7Plc（S7-200 SMART，python-snap7）
├── ateq.py            # FakeAteq / SerialAteq（F620 串口 Modbus 风格帧）
├── laser.py           # LaserFileWriter（原子写+回读校验）/ LaserMarker（文件+PLC脉冲+10s清空）
├── date_codes.py      # 日期方案（日期对照.ini）
├── model_settings.py  # 型号参数（日期设置.ini）/ 全局设置 / 作业员
├── station.py         # 单工位状态机（start_cycle/test1/test2/mark/remark + 周期日志恢复）
├── journal.py         # 断电恢复日志（intent/commit）
├── repository.py      # Fake / PyMySQL（schema v2：marked + Mark Time）
├── composition.py     # simulate/shadow/live 装配 + CapabilityPolicy
├── live_preflight.py  # LIVE 预检（点位/PLC/ATEQ/激光目录/型号/MySQL）
├── calibration.py     # NG→OK 样件验证 + 时效倒计时（与 Morocco 一致）
├── ui.py / ui_replica.py  # PySide6 主界面（单工位四页：测试/设置/查询/手动）
└── ui_theme.py        # 主题与多语言（中/英/法）
config/
├── default.toml       # 模拟模式
├── live.toml          # 现场实时配置模板（A/B 电脑各一份）
└── points.toml        # PLC 点位表（⚠️ 占位地址，等电气点位表替换）
tools/
├── install_mysql84*.ps1  # MySQL 8.4 离线安装（两台电脑各执行一次）
├── mysql_schema.sql      # schema v2
├── prepare_production_data.py  # 生成 D:\data 配置样例与 D:\激光打码 目录
└── run_tests_local.sh    # 本地镜像跑 pytest（开发机网络盘加速用）
tests/                  # pytest：配置/点位/激光/状态机/数据库/校准/UI离屏
docs/部署手册.md
```

## 快速开始（模拟模式，无硬件）

```bash
python -m venv .venv
.venv\Scripts\pip install PySide6 PyMySQL pyserial python-snap7 pytest
.venv\Scripts\python -m app.main --smoke-cycle  # 无扫码全流程冒烟
.venv\Scripts\python -m pytest tests/ -q        # 全部测试
.venv\Scripts\python -m app.main                # 模拟模式 UI
```

## 现场部署（概要，详见 docs/部署手册.md）

1. 两台电脑各装 Python 3.10 + 依赖、MySQL 8.4（`tools/install_mysql84*.ps1`）。
2. 执行 `tools/mysql_schema.sql`；运行 `tools/prepare_production_data.py` 生成数据目录。
3. 修改 `config/live.toml`：`station`、`plc_ip`、`ateq_com/ateq_slave`、激光参数。
4. **等电气点位表**填 `config/points.toml`，逐点核对后置 `points_confirmed = true`。
5. 安装激光打码软件并指向监听文件 `D:\激光打码\激光码信息.txt`。
6. 预检：`python -m app.main --config config/live.toml --preflight`，全部 PASS 后
   `run_live_ui.bat` 启动。

## 当前状态与待办

- [x] 核心层与 UI 改造完成；pytest 全绿；simulate 冒烟通过
- [x] **240429 移植适配**（2026-09-20）：FX 编程口协议直连 `FxSerialPlc`（COM3，替代
      老 LabVIEW+NI OPC Servers 链路）、称重 Modbus `WeightScale`（COM6 40002→PLC D900）、
      PLC 中转 `PlcRelayServer/RemoteFxPlc`（A 工位经 B 电脑写 PLC）、FX 点位档位
      `config/points_240429.toml`、A/B 现场配置模板 `config/live_240429_A/B.toml`、
      校准周期默认 8h；腔序：第一腔=负压、第二腔=正压（硬件时序驱动，两腔全 OK 才打码）
- [ ] **PLC 电气点位表（稍后提供）** → 确认 `config/points_240429.toml` 的
      laser_start/laser_done 地址后置 `points_confirmed = true`
- [ ] 激光打码软件文件格式现场核对（文件名/编码/每行字段顺序/是否需要完成位）
- [ ] ATEQ 程序号/串口参数逐台确认（沿用 9600 8E1 + 0x30 实时块）
- [ ] 现场联调：shadow → live 预检 → 小批量试产（B 电脑先跑通，再复制到 A 电脑）

### 240429 部署速查

1. **B 电脑**（右工位，PLC 所在机）：`config/live_240429_B.toml` —— FX PLC COM3 直连、
   ATEQ COM4、称重 COM6→D900、开启 relay_enabled（9101 端口）供 A 中转。
2. **A 电脑**（左工位）：`config/live_240429_A.toml` —— 本工位 ATEQ COM4 + 本地激光 TXT，
   `plc_relay_host` 指向 B 电脑 IP，token 与 B 一致；称重由 B 负责。
3. ⚠️ 联调切换前先退出老 LabVIEW 程序：COM3/COM4 是独占串口，双主站会互相干扰。
4. 老程序架构与老点位表还原记录见 `docs/240429移植分析.md`。
