"""Application entry point. SIMULATE is the only default and is hardware-free."""
from __future__ import annotations
import argparse
import sys
import tempfile
from pathlib import Path
from .ateq import FakeAteq
from .config import Settings
from .models import Result, RunMode, StationId
from .printer import FakePrinter
from .repository import FakeRepository
from .station import StationController
from .permissions import AuthSession, SecurityContext
from .license import LicenseVerifier
from .ateq import SerialAteq

_UI_INSTANCE_LOCK = None


def _acquire_ui_instance_lock() -> bool:
    """Prevent a second UI from competing for the single scanner TCP link."""
    global _UI_INSTANCE_LOCK
    try:
        from PySide6.QtCore import QLockFile
        lock_path = Path(tempfile.gettempdir()) / "ATEQ-LeakTest2Channels.ui.lock"
        lock = QLockFile(str(lock_path))
        lock.setStaleLockTime(10_000)
        if not lock.tryLock(0):
            return False
        _UI_INSTANCE_LOCK = lock
        return True
    except Exception as exc:
        print(f"UI instance lock unavailable: {exc}", file=sys.stderr)
        return False


def run_b_test(port: str = "COM6", slave: int = 1, program: str | None = None) -> int:
    """Run an isolated, B-only ATEQ communication check.

    This path deliberately opens no PLC, scanner, database or printer.  It
    reads the F620 realtime block and current program; program selection is an
    explicit opt-in because it writes to the instrument.
    """
    adapter = SerialAteq(port, "B", slave=int(slave), timeout_s=0.8, cycle_timeout_s=2.0)
    try:
        adapter.connect()
        registers, raw = adapter.read_registers(adapter.REALTIME_ADDRESS, 1)
        current = adapter.current_program()
        print(f"B_TEST CONNECTED: port={port} slave={slave} realtime_word=0x{registers[0]:04X} frame={raw.hex()}")
        print(f"B_TEST PROGRAM: {current}")
        if program is not None:
            adapter.select_program(program)
            print(f"B_TEST PROGRAM_WRITE_OK: {adapter.current_program()}")
        return 0
    except Exception as exc:
        print(f"B_TEST_BLOCKED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    finally:
        adapter.close()

def run_simulation() -> int:
    settings = Settings()
    repository, printer = FakeRepository(settings), FakePrinter()
    security = SecurityContext(AuthSession(demo=True), LicenseVerifier(simulator=True).verify(b"SIMULATE", b"SIMULATE-SIGNATURE"))
    stations = [StationController(s, repository, printer, FakeAteq(), security=security) for s in StationId]
    for station in stations:
        station.scan(f"SIM-{station.station.value}-001", "SIM-PART", "SIM-OP")
        station.test_first()
        station.test_second()
        station.label()
    print(f"SIMULATE OK: stations={len(stations)} records={len(repository.records)} labels={len(printer.intents)}")
    return 0

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Leak Test 2 Channels")
    parser.add_argument("--mode", choices=[mode.value for mode in RunMode], default="simulate")
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--smoke-cycle", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--b-test", action="store_true", help="仅测试 B 工位 ATEQ 串口，不启动其他设备")
    parser.add_argument("--b-port", default="COM6")
    parser.add_argument("--b-slave", type=int, default=1)
    parser.add_argument("--b-program", default=None, help="显式写入 B 工位程序号；省略则只读")
    parser.add_argument("--b-live-ui", action="store_true", help="启动仅 B 工位使用 COM6 的实时 UI")
    parser.add_argument("--live-ui", action="store_true", help="启动 A/B 两工位独立真实 UI（配置来自 live.toml）")
    parser.add_argument("--live-config", type=Path, default=None, help="A/B 真实 UI 使用的已验证 live.toml")
    parser.add_argument("--device", choices=["all", "plc", "ateq", "scanner", "database", "printer"], default="all")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    if args.live_ui:
        args.mode = RunMode.LIVE.value
        args.config = args.live_config or Path(__file__).parents[1] / "config" / "live.toml"
    if args.b_test:
        return run_b_test(args.b_port, args.b_slave, args.b_program)
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
        devices = [args.device] if args.device != "all" else ["plc", "ateq", "scanner", "database", "printer"]
        for device in devices:
            print(f"{device}=PASS (simulate/no side effects)")
        return 0
    if not args.diagnose:
        try:
            if not _acquire_ui_instance_lock():
                print("UI already running; refusing a second scanner client", file=sys.stderr)
                return 0
            from .ui import launch_ui
            return launch_ui(b_live=args.b_live_ui, b_port=args.b_port, b_slave=args.b_slave,
                             live_all=args.live_ui,
                             live_config=args.live_config or (config_path if args.live_ui else None))
        except RuntimeError as exc:
            print(f"UI unavailable: {exc}; use --diagnose for headless mode", file=sys.stderr)
            return 3
    return run_simulation()

if __name__ == "__main__":
    raise SystemExit(main())
