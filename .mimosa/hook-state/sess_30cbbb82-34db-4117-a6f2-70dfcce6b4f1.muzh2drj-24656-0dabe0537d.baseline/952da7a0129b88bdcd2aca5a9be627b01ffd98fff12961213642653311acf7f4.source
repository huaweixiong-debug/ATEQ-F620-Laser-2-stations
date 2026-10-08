"""Read-only production readiness checks.  A failed check blocks LIVE startup."""
from __future__ import annotations

import json
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .ateq import SerialAteq
from .barcode_rules import ProductRuleCatalog, DateCodeCatalog
from .config import Settings
from .models import StationId
from .repository import PyMySQLRepository


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class PreflightReport:
    checks: tuple[PreflightCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def format(self) -> str:
        return "\n".join(f"{'PASS' if item.passed else 'BLOCKED'} {item.name}: {item.detail}"
                         for item in self.checks)


def _tcp(name: str, host: str, port: int) -> PreflightCheck:
    try:
        with socket.create_connection((host, port), timeout=2):
            pass
        return PreflightCheck(name, True, f"{host}:{port} 可连接")
    except OSError as exc:
        return PreflightCheck(name, False, f"{host}:{port} {type(exc).__name__}: {exc}")


def _credentials(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise FileNotFoundError(f"数据库凭据文件不存在: {path}")
    values = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(values, dict) or not values.get("user") or not values.get("password"):
        raise ValueError("数据库凭据文件缺少 user/password")
    return {"user": str(values["user"]), "password": str(values["password"])}


def run_preflight(settings: Settings, probe_devices: bool = True) -> PreflightReport:
    checks: list[PreflightCheck] = []
    data = settings.data_dir
    product_ini, date_ini = data / "日期设置.ini", data / "日期对照.ini"
    try:
        products = ProductRuleCatalog.from_file(product_ini)
        DateCodeCatalog.from_file(date_ini)
        missing = [str(products.rule_for(product).template_for(station))
                   for product in products.known_products() for station in StationId
                   if not products.rule_for(product).template_for(station).is_file()]
        if missing:
            raise FileNotFoundError("缺少模板: " + ", ".join(missing))
        checks.append(PreflightCheck("日期/型号/模板", True, f"{len(products.known_products())} 个型号完整"))
    except Exception as exc:
        checks.append(PreflightCheck("日期/型号/模板", False, str(exc)))

    checks.append(_tcp("PLC", settings.plc_ip, 102))
    checks.append(_tcp("扫码枪", settings.scanner_ip, settings.scanner_port))

    if not settings.ports_confirmed or len(set(settings.ateq_ports)) != 2:
        checks.append(PreflightCheck("ATEQ 映射", False, "A/B 端口尚未逐台确认"))
    elif probe_devices:
        for index, station in enumerate(StationId):
            adapter = SerialAteq(settings.ateq_ports[index], station.value,
                                 slave=settings.ateq_slaves[index], timeout_s=0.6)
            try:
                adapter.connect()
                adapter.read_registers(adapter.REALTIME_ADDRESS, 1)
                checks.append(PreflightCheck(f"ATEQ {station.value}", True,
                                             f"{settings.ateq_ports[index]} slave={settings.ateq_slaves[index]}"))
            except Exception as exc:
                checks.append(PreflightCheck(f"ATEQ {station.value}", False,
                                             f"{settings.ateq_ports[index]}: {type(exc).__name__}: {exc}"))
            finally:
                adapter.close()

    bartender = Path(r"C:\Program Files\Seagull\BarTender Suite\bartend.exe")
    checks.append(PreflightCheck("BarTender", bartender.is_file(), str(bartender)))
    try:
        process = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Printer -Name 'ZDesigner ZT231-203dpi EPL' -ErrorAction Stop).PrinterStatus"],
            capture_output=True, text=True, timeout=10)
        detail = (process.stdout or process.stderr).strip()
        checks.append(PreflightCheck("打印机", process.returncode == 0, detail or "未返回状态"))
    except Exception as exc:
        checks.append(PreflightCheck("打印机", False, str(exc)))

    marker = data / "EV80016300-B.rebind-ok"
    checks.append(PreflightCheck("EV80016300-B 数据源复核", marker.is_file(),
                                 "已人工复核" if marker.is_file() else "缺少人工复核标记"))

    try:
        credential = _credentials(settings.credential_path)
        repo = PyMySQLRepository(host=settings.database_host, port=settings.database_port,
                                 user=credential["user"], password=credential["password"],
                                 database=settings.database)
        repo.connect_and_verify()
        checks.append(PreflightCheck("MySQL", True, "test.info_A/info_B 结构通过"))
    except Exception as exc:
        checks.append(PreflightCheck("MySQL", False, f"{type(exc).__name__}: {exc}"))
    return PreflightReport(tuple(checks))
