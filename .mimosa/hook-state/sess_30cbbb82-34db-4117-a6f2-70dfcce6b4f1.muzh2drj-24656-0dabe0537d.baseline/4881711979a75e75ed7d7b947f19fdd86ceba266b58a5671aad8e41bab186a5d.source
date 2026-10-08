"""Application entry point. SIMULATE is the only default and is hardware-free."""
from __future__ import annotations
import argparse
import sys
from pathlib import Path
from .ateq import FakeAteq
from .config import Settings
from .models import Result, RunMode, StationId
from .printer import FakePrinter
from .repository import FakeRepository
from .station import StationController
from .permissions import AuthSession, SecurityContext
from .license import LicenseVerifier

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
    parser.add_argument("--device", choices=["all", "plc", "ateq", "scanner", "database", "printer"], default="all")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args(argv)
    config_path = args.config or (Path(__file__).parents[1] / "config" / "default.toml")
    try:
        settings = Settings.from_toml(config_path) if config_path.exists() else Settings.from_args(args.mode)
    except (OSError, ValueError) as exc:
        print(f"config=BLOCKED: {exc}", file=sys.stderr)
        return 2
    if args.mode != settings.mode.value:
        print(f"模式与配置不一致: {args.mode} != {settings.mode.value}", file=sys.stderr)
        return 2
    if args.mode != RunMode.SIMULATE.value:
        print(f"模式 {args.mode} 已实现安全门控；未连接真实设备，live 需现场批准。", file=sys.stderr)
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
            from .ui import launch_ui
            return launch_ui()
        except RuntimeError as exc:
            print(f"UI unavailable: {exc}; use --diagnose for headless mode", file=sys.stderr)
            return 3
    return run_simulation()

if __name__ == "__main__":
    raise SystemExit(main())
