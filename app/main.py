"""Application entry point. SIMULATE is the only default and is hardware-free."""
from __future__ import annotations
import argparse
import sys
import tempfile
from pathlib import Path
from .ateq import FakeAteq, SerialAteq
from .config import Settings
from .models import CycleSelection, Result, RunMode, StationId
from .laser import FakeMarker
from .plc import FakePlc
from .repository import FakeRepository
from .station import StationController
from .permissions import AuthSession, SecurityContext
from .license import LicenseVerifier

_UI_INSTANCE_LOCK = None


def _acquire_ui_instance_lock(station: StationId) -> bool:
    """One UI instance per station per Windows session."""
    global _UI_INSTANCE_LOCK
    try:
        from PySide6.QtCore import QLockFile
        lock_path = Path(tempfile.gettempdir()) / f"LaserLeakTest.{station.value}.ui.lock"
        lock = QLockFile(str(lock_path))
        lock.setStaleLockTime(10_000)
        if not lock.tryLock(0):
            return False
        _UI_INSTANCE_LOCK = lock
        return True
    except Exception as exc:
        print(f"UI instance lock unavailable: {exc}", file=sys.stderr)
        return False


def run_ateq_test(port: str, slave: int, program: str | None = None) -> int:
    """Run an isolated ATEQ communication check; opens no PLC/DB/laser."""
    adapter = SerialAteq(port, "ATEQ", slave=int(slave), timeout_s=0.8, cycle_timeout_s=2.0)
    try:
        adapter.connect()
        registers, raw = adapter.read_registers(adapter.REALTIME_ADDRESS, 1)
        current = adapter.current_program()
        print(f"ATEQ_TEST CONNECTED: port={port} slave={slave} realtime_word=0x{registers[0]:04X} frame={raw.hex()}")
        print(f"ATEQ_TEST PROGRAM: {current}")
        if program is not None:
            adapter.select_program(program)
            print(f"ATEQ_TEST PROGRAM_WRITE_OK: {adapter.current_program()}")
        return 0
    except Exception as exc:
        print(f"ATEQ_TEST_BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        adapter.close()


def run_simulation() -> int:
    settings = Settings()
    repository, marker = FakeRepository(settings), FakeMarker()
    security = SecurityContext(AuthSession(demo=True), LicenseVerifier(simulator=True).verify(b"SIMULATE", b"SIMULATE-SIGNATURE"))
    station = StationId.A
    controller = StationController(station, repository, marker, FakeAteq(), security=security)
    controller.start_cycle(CycleSelection(station, "SIM-PART", "SIM-OP", "dual", "1"))
    controller.test_first()
    controller.test_second()
    controller.mark()
    print(f"SIMULATE OK: station={station.value} phase={controller.phase.value} "
          f"marked={controller.record.marked} records={len(repository.records)} marks={len(marker.intents)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ATEQ F620 双腔气密检测 + 激光打码")
    parser.add_argument("--mode", choices=[mode.value for mode in RunMode], default="simulate")
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--smoke-cycle", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--ateq-test", action="store_true", help="仅测试本工位 ATEQ 串口，不启动其他设备")
    parser.add_argument("--port", default="COM6", help="ATEQ 串口（--ateq-test 用）")
    parser.add_argument("--slave", type=int, default=1, help="ATEQ 从站地址（--ateq-test 用）")
    parser.add_argument("--program", default=None, help="显式写入 ATEQ 程序号；省略则只读")
    parser.add_argument("--live-ui", action="store_true", help="启动本工位实时 UI（配置来自 live.toml）")
    parser.add_argument("--live-config", type=Path, default=None, help="实时 UI 使用的已验证 live.toml")
    parser.add_argument("--device", choices=["all", "plc", "ateq", "database", "laser"], default="all")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.live_ui:
        args.mode = RunMode.LIVE.value
        args.config = args.live_config or Path(__file__).parents[1] / "config" / "live.toml"
    if args.ateq_test:
        return run_ateq_test(args.port, args.slave, args.program)
    config_path = args.config or (Path(__file__).parents[1] / "config" / "default.toml")
    try:
        settings = Settings.from_toml(config_path) if config_path.exists() else Settings.from_args(args.mode)
    except (OSError, ValueError) as exc:
        print(f"config=BLOCKED: {exc}", file=sys.stderr)
        return 2
    if args.mode != settings.mode.value:
        print(f"模式与配置不一致: {args.mode} != {settings.mode.value}", file=sys.stderr)
        return 2
    if args.preflight or args.mode == RunMode.LIVE.value:
        from .live_preflight import run_preflight
        report = run_preflight(settings)
        print(report.format())
        if not report.passed:
            print("LIVE_BLOCKED: 预检未全部通过", file=sys.stderr)
            return 2
        if args.preflight:
            return 0
    if args.mode != RunMode.SIMULATE.value and not args.live_ui:
        print(f"模式 {args.mode} 已实现只读预检；生产 UI 需真实设备验收后启用。", file=sys.stderr)
        return 2
    if args.smoke_cycle:
        return run_simulation()
    if args.diagnose:
        devices = [args.device] if args.device != "all" else ["plc", "ateq", "database", "laser"]
        for device in devices:
            print(f"{device}=PASS (simulate/no side effects)")
        return 0
    try:
        if not _acquire_ui_instance_lock(settings.station):
            print("UI already running; refusing a second instance", file=sys.stderr)
            return 0
        from .ui import launch_ui
        return launch_ui(live=args.live_ui,
                         live_config=args.live_config or (config_path if args.live_ui else None))
    except RuntimeError as exc:
        print(f"UI unavailable: {exc}; use --diagnose for headless mode", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
