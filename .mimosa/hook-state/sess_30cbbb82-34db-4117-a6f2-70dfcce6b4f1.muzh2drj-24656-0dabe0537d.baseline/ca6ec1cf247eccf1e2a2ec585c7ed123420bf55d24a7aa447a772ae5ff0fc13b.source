"""Field bring-up harness: real scanner, real PLC (read-only) and real
BarTender printer with simulated ATEQ measurements.

The ATEQ instruments are not connected on the current station, so this
entry point runs full cycles (scan -> two simulated tests -> label print)
against the real scanner/PLC/printer.  It is a bring-up tool; the
production path in app.main remains gated.

Usage:
    python -m app.fieldtest                          # 联调循环（默认配置）
    python -m app.fieldtest --plc-check              # 只读 PLC 点位表检查
    python -m app.fieldtest --scanner-probe          # 只收扫码数据 60 秒
    python -m app.fieldtest --print-test TEST001     # 只打一张测试标签
    python -m app.fieldtest --dry-run                # 打印改为只显示命令
"""
from __future__ import annotations
import argparse
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from .barprint import BarTenderCmdPrinter, bartend_command, write_field_files, write_label_data
from .barcode_rules import BarcodeRuleEngine, route_shared_scan
from .models import Measurement, Result, StationId, TraceRecord
from .plc import POINTS, Snap7Plc
from .scanner_tcp import FileScanner, TcpScanner

DEFAULTS = {
    "plc_ip": "192.168.2.1", "plc_rack": 0, "plc_slot": 1, "plc_poll_ms": 500,
    "scanner_role": "client", "scanner_ip": "192.168.2.10", "scanner_port": 9004,
    "scanner_listen_port": 8500, "scanner_trigger": "", "scanner_terminator": "\r",
    "bartend_exe": r"C:\Program Files\Seagull\BarTender Suite\bartend.exe",
    "template_dir": r"D:\data", "label_data_file": r"D:\data\label_data.txt",
    "bartender_close_after": True,
    "part_no": "E113015200", "part_no_a": "E113015200", "part_no_b": "E113015200",
    "station": "A", "person": "FIELDTEST",
}


def load_config(path: Path | None) -> dict:
    cfg = dict(DEFAULTS)
    if path and Path(path).exists():
        with open(path, "rb") as handle:
            raw = tomllib.load(handle)
        unknown = set(raw) - set(DEFAULTS)
        if unknown:
            raise ValueError(f"fieldtest.toml 未知配置项: {sorted(unknown)}")
        cfg.update({k: v for k, v in raw.items() if v != ""})
    cfg["plc_poll_ms"] = max(100, int(cfg["plc_poll_ms"]))
    cfg["plc_rack"] = int(cfg["plc_rack"])
    cfg["plc_slot"] = int(cfg["plc_slot"])
    cfg["scanner_port"] = int(cfg["scanner_port"])
    cfg["scanner_listen_port"] = int(cfg["scanner_listen_port"])
    cfg["bartender_close_after"] = bool(cfg["bartender_close_after"])
    return cfg


def build_scanner(cfg: dict):
    if cfg["scanner_role"] == "file":
        return FileScanner(Path(cfg["scanner_ip"]))
    terminator = cfg["scanner_terminator"].encode("latin-1")
    trigger = cfg["scanner_trigger"].encode("latin-1")
    return TcpScanner(cfg["scanner_ip"], cfg["scanner_port"], role=cfg["scanner_role"],
                      listen_port=cfg["scanner_listen_port"], terminator=terminator,
                      trigger=trigger)


def build_printer(cfg: dict) -> BarTenderCmdPrinter:
    return BarTenderCmdPrinter(
        Path(cfg["bartend_exe"]), Path(cfg["template_dir"]), Path(cfg["label_data_file"]),
        close_after=cfg["bartender_close_after"])


def fake_measurement(result: Result = Result.OK) -> Measurement:
    return Measurement(pressure=10.0, leakage=0.02, result=result, raw_frame=b"SIMFRAME:fieldtest")


def make_record(code: str, station: StationId, cfg: dict, payload=None) -> TraceRecord:
    serial = payload.serial_no if payload else code.strip()
    part = payload.product_id if payload else cfg["part_no"]
    return TraceRecord(station, serial, code.strip(), part, cfg["person"],
                       first=fake_measurement(), second=fake_measurement(),
                       cycle_id=f"{station.value}-{uuid4().hex}",
                       customer_no=payload.customer_model if payload else "",
                       template_path=str(payload.template_path) if payload else "")


def point_names() -> dict[tuple[int, int], str]:
    names: dict[tuple[int, int], str] = {}
    for name, per_station in POINTS.items():
        for station, address in per_station.items():
            names.setdefault(address, f"{name} [{station.value}]")
    return names


def cmd_plc_check(cfg: dict) -> int:
    plc = Snap7Plc(cfg["plc_ip"], cfg["plc_rack"], cfg["plc_slot"])
    try:
        plc.connect()
    except RuntimeError as exc:
        print(f"PLC=FAIL: {exc}")
        return 1
    print(f"PLC={cfg['plc_ip']} 已连接")
    try:
        raw = plc.read_bytes(0, 17)
    except RuntimeError as exc:
        print(f"PLC 读取失败: {exc}")
        return 1
    names = point_names()
    print(f"M0..M16 = {raw.hex(' ').upper()}")
    for (byte, bit), name in sorted(names.items()):
        if byte < len(raw):
            value = bool(raw[byte] & (1 << bit))
            print(f"  {name:<22} M{byte}.{bit} = {int(value)}")
    plc.disconnect()
    return 0


def cmd_scanner_probe(scanner, seconds: int) -> int:
    scanner.start()
    deadline = time.monotonic() + seconds
    print(f"监听扫码数据 {seconds} 秒（扫码枪 {getattr(scanner, 'ip', 'file')}），Ctrl+C 退出")
    count = 0
    while time.monotonic() < deadline:
        code = scanner.read_code(timeout=1.0)
        if code:
            count += 1
            print(f"[{count}] {code}")
        if not scanner.health() and scanner.last_error:
            print(f"scanner: {scanner.last_error}", flush=True)
    scanner.close()
    print(f"共收到 {count} 条扫码数据")
    return 0 if count > 0 else 2


def cmd_print_test(cfg: dict, code: str, station: StationId, dry_run: bool) -> int:
    printer = build_printer(cfg)
    record = make_record(code, station, cfg)
    if dry_run:
        from .barprint import resolve_template, bartend_command
        template = resolve_template(Path(cfg["template_dir"]), cfg["part_no"], station)
        write_field_files(record, Path(cfg["template_dir"]), template)
        write_label_data(record, Path(cfg["label_data_file"]))
        print("DRY-RUN 命令:")
        print("  " + " ".join(bartend_command(printer.executable, template, printer.close_after)))
        print(f"按字段 txt 已写入: {cfg['template_dir']}")
        return 0
    try:
        receipt = printer.print_label(record)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"打印失败: {exc}")
        return 1
    print(f"打印回执: accepted={receipt.accepted} job={receipt.job_id} receipt={receipt.receipt}")
    return 0 if receipt.accepted else 1


class PlcWatcher:
    """Reads the mapped M bytes periodically and reports changed points."""

    def __init__(self, plc: Snap7Plc, poll_ms: int) -> None:
        self.plc, self.poll_ms = plc, poll_ms
        self._stop = threading.Event()
        self._last: bytearray | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                raw = self.plc.read_bytes(0, 17)
            except RuntimeError:
                raw = None
            if raw is not None and raw != self._last:
                if self._last is None:
                    print(f"[PLC] M0..M16 = {raw.hex(' ').upper()}")
                else:
                    names = point_names()
                    changed = [(n, b, raw[b] & (1 << i)) for (b, i), n in sorted(names.items())
                               if b < len(raw) and raw[b] != self._last[b]]
                    for name, byte, value in changed:
                        print(f"[PLC] {name} M{byte} -> {int(bool(value))}")
                self._last = raw
            self._stop.wait(self.poll_ms / 1000.0)


def run_loop(cfg: dict, station_mode: str, dry_run: bool, allow_print: bool) -> int:
    scanner = build_scanner(cfg)
    plc = Snap7Plc(cfg["plc_ip"], cfg["plc_rack"], cfg["plc_slot"])
    printer = build_printer(cfg)
    watcher = PlcWatcher(plc, cfg["plc_poll_ms"])
    try:
        plc.connect()
        print(f"PLC {cfg['plc_ip']} 已连接（只读，写入保持禁用）")
        watcher.start()
    except RuntimeError as exc:
        print(f"PLC 连接失败（继续，仅扫码/打印测试）: {exc}")
    scanner.start()
    stations = [StationId.A, StationId.B] if station_mode == "both" else [StationId(station_mode)]
    engine = BarcodeRuleEngine(Path(cfg["template_dir"]) / "日期设置.ini",
                               Path(cfg["template_dir"]) / "日期对照.ini",
                               Path(cfg["template_dir"]))
    products = {StationId.A: cfg["part_no_a"], StationId.B: cfg["part_no_b"]}
    print(f"等待扫码（按二维码自动识别工位；默认不打印），Ctrl+C 退出")
    try:
        while True:
            code = scanner.read_code(timeout=1.0)
            if code:
                try:
                    payloads = {station: engine.generate(products[station], station) for station in stations}
                    station = route_shared_scan(code, {item: payload.barcode_text for item, payload in payloads.items()})
                except Exception as exc:
                    print(f"[扫码] 拒绝: {exc}")
                    continue
                payload = payloads[station]
                record = make_record(code, station, cfg, payload)
                print(f"[扫码] 工位 {station.value} 条码 {code.strip()}")
                if dry_run:
                    from .barprint import resolve_template, bartend_command
                    template = resolve_template(Path(cfg["template_dir"]), cfg["part_no"], station)
                    write_field_files(record, Path(cfg["template_dir"]), template)
                    write_label_data(record, Path(cfg["label_data_file"]))
                    print(f"[打印] DRY-RUN: {' '.join(bartend_command(printer.executable, template, printer.close_after))}")
                elif allow_print:
                    try:
                        receipt = printer.print_label(record)
                        print(f"[打印] {receipt.receipt} (accepted={receipt.accepted})")
                    except (FileNotFoundError, RuntimeError) as exc:
                        print(f"[打印] 失败: {exc}")
                else:
                    print("[打印] 已跳过（characterization 默认禁止真实打印）")
            if scanner.last_error:
                print(f"[扫码器] {scanner.last_error}", flush=True)
                scanner.last_error = ""
    except KeyboardInterrupt:
        print("\n测试结束")
    finally:
        watcher.stop()
        scanner.close()
        plc.disconnect()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="现场联调：扫码枪 + PLC(只读) + BarTender（ATEQ 模拟）")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--part", default=None, help="覆盖零件号（决定 .btw 模板）")
    parser.add_argument("--station", choices=["A", "B", "both"], default=None)
    parser.add_argument("--plc-check", action="store_true", help="只读检查 PLC 点位")
    parser.add_argument("--scanner-probe", action="store_true", help="只收扫码数据")
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--print-test", metavar="CODE", default=None, help="只打一张测试标签")
    parser.add_argument("--dry-run", action="store_true", help="打印/扫描只显示命令不执行")
    parser.add_argument("--no-print", action="store_true", help="兼容参数；循环默认即不打印")
    parser.add_argument("--allow-print", action="store_true", help="characterization 明确允许真实打印")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"config=BLOCKED: {exc}", file=sys.stderr)
        return 2
    if args.part:
        cfg["part_no"] = args.part
        cfg["part_no_a"] = args.part
        cfg["part_no_b"] = args.part
    if args.station:
        cfg["station"] = args.station
    if args.plc_check:
        return cmd_plc_check(cfg)
    if args.scanner_probe:
        return cmd_scanner_probe(build_scanner(cfg), args.seconds)
    if args.print_test:
        return cmd_print_test(cfg, args.print_test, StationId(cfg["station"]), args.dry_run)
    return run_loop(cfg, cfg["station"], args.dry_run, args.allow_print and not args.no_print)


if __name__ == "__main__":
    raise SystemExit(main())
