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
    parser.add_argument("--device", choices=["all", "plc", "ateq", "database", "laser"], default="all")
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()
    try:
        settings = Settings.from_toml(args.config) if args.config else Settings.from_args(args.mode)
    except (OSError, ValueError) as exc:
        print(f"config=BLOCKED: {exc}")
        return 2
    devices = [args.device] if args.device != "all" else ["plc", "ateq", "database", "laser"]
    for device in devices:
        print(f"{device}=PASS (simulate/no side effects; station={settings.station.value})")
    print("说明：真实设备诊断请运行 python -m app.main --config config/live.toml --preflight")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
