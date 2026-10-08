"""Read-only startup diagnostics."""
from __future__ import annotations
import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parents[1]))
from app.config import Settings
from app.models import RunMode

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", default="simulate")
    parser.add_argument("--device", choices=["all", "plc", "ateq", "scanner", "database", "printer"], default="all")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()
    try:
        settings = Settings.from_toml(args.config) if args.config else Settings.from_args(args.mode)
    except (OSError, ValueError) as exc:
        print(f"config=BLOCKED: {exc}")
        return 2
    if args.config and settings.mode.value != args.mode:
        print(f"config=BLOCKED: mode mismatch config={settings.mode.value} arg={args.mode}")
        return 2
    print(f"mode={settings.mode.value}")
    print(f"plc={settings.plc_ip} (read-only diagnostics; no writes)")
    print(f"ateq={','.join(settings.ateq_ports)} (not opened)")
    print(f"setup_exists={settings.setup_path.exists()}")
    devices = [args.device] if args.device != "all" else ["plc", "ateq", "scanner", "database", "printer"]
    for device in devices:
        if settings.mode is RunMode.SIMULATE:
            print(f"{device}=PASS (fake/no side effects)")
        else:
            print(f"{device}=BLOCKED (no production connection in this build)")
    if settings.mode is not RunMode.SIMULATE:
        return 2
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
