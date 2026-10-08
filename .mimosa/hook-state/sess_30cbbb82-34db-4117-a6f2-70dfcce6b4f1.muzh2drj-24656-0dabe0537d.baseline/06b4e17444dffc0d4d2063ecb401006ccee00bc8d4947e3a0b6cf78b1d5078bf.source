"""Fail-closed QR-code generation compatible with the legacy Barcode.py files."""
from __future__ import annotations

import os
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock

from .models import StationId


_SERIAL_PROCESS_LOCK = RLock()


@contextmanager
def _serial_file_lock(serial_path: Path):
    """Serialize counter read/modify/write across UI processes.

    The live UI can be restarted while the previous process is winding down;
    an in-process lock alone would still allow both processes to reserve the
    same number.  An O_EXCL sidecar lock keeps the critical section atomic on
    the Windows data drive without adding a third-party dependency.
    """
    lock_path = Path(f"{serial_path}.lock")
    deadline = time.monotonic() + 5.0
    acquired = False
    with _SERIAL_PROCESS_LOCK:
        while not acquired:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                acquired = True
            except FileExistsError:
                try:
                    if time.time() - lock_path.stat().st_mtime > 30:
                        lock_path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                if time.monotonic() >= deadline:
                    raise BarcodeInputError(f"流水号文件锁超时: {serial_path}")
                time.sleep(0.02)
        try:
            yield
        finally:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass


class BarcodeError(Exception):
    pass


class BarcodeConfigError(BarcodeError):
    pass


class BarcodeInputError(BarcodeError):
    pass


class TemplateMissingError(BarcodeError):
    pass


def parse_kv_ini(path: Path) -> dict[str, dict[str, str]]:
    """Parse the mixed ``=``/Chinese-colon INI format used on the line."""
    path = Path(path)
    if not path.is_file():
        raise BarcodeConfigError(f"配置文件不存在: {path}")
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    section_name = ""
    for line_no, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith(("'", ";", "#")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section_name = line[1:-1].strip()
            if not section_name or section_name in sections:
                raise BarcodeConfigError(f"{path.name} 第{line_no}行节名无效或重复")
            current = sections.setdefault(section_name, {})
            continue
        if current is None:
            raise BarcodeConfigError(f"{path.name} 第{line_no}行位于配置节之外")
        split_at = [(line.find(mark), mark) for mark in ("=", "：") if line.find(mark) >= 0]
        if not split_at:
            raise BarcodeConfigError(f"{path.name} [{section_name}] 第{line_no}行无法解析: {line!r}")
        pos, mark = min(split_at, key=lambda item: item[0])
        key, value = line[:pos].strip(), line[pos + len(mark):].strip()
        if not key or key in current:
            raise BarcodeConfigError(f"{path.name} [{section_name}] 第{line_no}行键为空或重复: {key!r}")
        current[key] = value
    return sections


class DateCodeCatalog:
    def __init__(self, sections: dict[str, dict[str, str]]) -> None:
        self.sections = sections

    @classmethod
    def from_file(cls, path: Path) -> "DateCodeCatalog":
        return cls(parse_kv_ini(path))

    def date_code(self, scheme: str, when: datetime) -> str:
        local = when.astimezone() if when.tzinfo else when
        direct = {
            "YYYYMMDD": local.strftime("%Y%m%d"), "YYMMDD": local.strftime("%y%m%d"),
            "YYYYMM": local.strftime("%Y%m"), "YYMM": local.strftime("%y%m"),
            "YYYYDDD": local.strftime("%Y%j"), "YYDDD": local.strftime("%y%j"),
            "YYYY": local.strftime("%Y"),
        }
        if scheme in direct:
            return direct[scheme]
        if scheme in ("YYYYM", "YYM"):
            month = "123456789XYZ"[local.month - 1]
            return (local.strftime("%Y") if scheme == "YYYYM" else local.strftime("%y")) + month
        tokens = [part.strip() for part in scheme.split("+") if part.strip()]
        years = [part for part in tokens if part.startswith("年")]
        months = [part for part in tokens if part.startswith("月")]
        days = [part for part in tokens if part.startswith("日")]
        if len(years) != 1 or len(months) > 1 or len(days) > 1 or len(tokens) != len(years) + len(months) + len(days):
            raise BarcodeConfigError(f"日期方案无效: {scheme!r}")
        output = self._year(years[0], local)
        if months:
            output += self._indexed(months[0], "月", local.month)
        if days:
            output += self._day(days[0], local)
        return output

    def _section_value(self, section: str, key: str) -> str:
        values = self.sections.get(section)
        if values is None or key not in values:
            raise BarcodeConfigError(f"日期对照.ini 缺少 [{section}]/{key}")
        return values[key]

    def _year(self, scheme: str, when: datetime) -> str:
        raw = self._section_value(scheme, "年")
        choices = [item.strip() for item in raw.split(",") if item.strip()]
        year = str(when.year)
        values = self.sections[scheme]
        if "对应" in values:
            mapped = [item.strip() for item in values["对应"].split(",") if item.strip()]
            if len(mapped) != len(choices) or year not in choices:
                raise BarcodeConfigError(f"[{scheme}] 年份映射无效或不含 {year}")
            return mapped[choices.index(year)]
        for item in choices:
            if year == item or year[-2:] == item:
                return item
        raise BarcodeConfigError(f"[{scheme}] 不支持年份 {year}")

    def _indexed(self, scheme: str, key: str, index: int) -> str:
        raw = self._section_value(scheme, key)
        if not raw:
            return ""
        choices = [item.strip() for item in raw.split(",") if item.strip()]
        if not 1 <= index <= len(choices):
            raise BarcodeConfigError(f"[{scheme}] {key}映射不完整")
        return choices[index - 1]

    def _day(self, scheme: str, when: datetime) -> str:
        raw = self._section_value(scheme, "日")
        if not raw:
            return ""
        if raw == "1-31":
            return f"{when.day:02d}"
        if raw in ("1-365", "1-366"):
            return f"{when.timetuple().tm_yday:03d}"
        return self._indexed(scheme, "日", when.day)


@dataclass(frozen=True)
class ProductRule:
    product_id: str
    customer_model: str
    barcode_rule: str
    date_scheme: str
    ateq_program: int
    template_family: str
    station_codes: dict[StationId, str]
    serial_files: dict[StationId, Path]
    template_paths: dict[StationId, Path] | None = None

    def template_for(self, station: StationId) -> Path:
        if self.template_paths and station in self.template_paths:
            explicit = self.template_paths[station]
            if explicit.name:
                return explicit
        value = self.template_family.strip()
        path = Path(value)
        if path.suffix.lower() == ".btw":
            stem = path.stem
            if stem.upper().endswith(("-A", "-B")):
                stem = stem[:-2]
            return path.with_name(f"{stem}-{station.value}.btw")
        return Path(fr"D:\data\{value}-{station.value}.btw")


class ProductRuleCatalog:
    REQUIRED = ("条码规则", "客户型号", "日期", "工位号A", "工位号B", "流水号A", "流水号B")

    def __init__(self, rules: dict[str, ProductRule]) -> None:
        self.rules = rules

    @classmethod
    def from_file(cls, path: Path) -> "ProductRuleCatalog":
        rules: dict[str, ProductRule] = {}
        for product, values in parse_kv_ini(path).items():
            missing = [key for key in cls.REQUIRED if not values.get(key)]
            if missing:
                raise BarcodeConfigError(f"{Path(path).name} [{product}] 缺少配置项: {missing}")
            program_raw = values.get("ATEQ程序号", values.get("ATEQ程序号A", "1"))
            try:
                program = int(program_raw)
            except ValueError as exc:
                raise BarcodeConfigError(f"[{product}] ATEQ程序号不是整数") from exc
            if not 1 <= program <= 255:
                raise BarcodeConfigError(f"[{product}] ATEQ程序号必须为 1-255")
            template_a = values.get("打印路径A", "").strip()
            template_b = values.get("打印路径B", "").strip()
            template = values.get("打印模板", template_a or fr"D:\data\{product}-A.btw")
            path_value = Path(template)
            family = str(path_value.with_name(path_value.stem[:-2] + path_value.suffix)) if path_value.stem.upper().endswith("-A") else template
            rules[product] = ProductRule(
                product, values["客户型号"], values["条码规则"], values["日期"], program, family,
                {StationId.A: values["工位号A"], StationId.B: values["工位号B"]},
                {StationId.A: Path(values["流水号A"]), StationId.B: Path(values["流水号B"])},
                {station: Path(value) for station, value in ((StationId.A, template_a), (StationId.B, template_b)) if value},
            )
        return cls(rules)

    def rule_for(self, product: str) -> ProductRule:
        try:
            return self.rules[product]
        except KeyError as exc:
            raise BarcodeConfigError(f"未知产品型号 {product!r}; 已配置: {sorted(self.rules)}") from exc

    def known_products(self) -> list[str]:
        return sorted(self.rules)


@dataclass(frozen=True)
class LabelPayload:
    product_id: str
    customer_model: str
    station: StationId
    serial_no: str
    barcode_text: str
    date_code: str
    ateq_program: int
    template_path: Path
    output_txt_path: Path
    print_path_txt: Path
    product_txt_path: Path


def _apply_rule(rule: str, values: dict[str, str]) -> str:
    result: list[str] = []
    pos = 0
    while pos < len(rule):
        if rule[pos] == "+":
            pos += 1
            continue
        for token in ("客户型号", "日期", "工位号", "流水号"):
            if rule.startswith(token, pos):
                result.append(values[token])
                pos += len(token)
                break
        else:
            result.append(rule[pos])
            pos += 1
    return "".join(result)


def read_serial(path: Path) -> str:
    path = Path(path)
    if not path.is_file():
        raise BarcodeInputError(f"流水号文件不存在: {path}")
    value = path.read_text(encoding="utf-8-sig")
    if not value or value != value.strip() or any(ch in value for ch in "\r\n"):
        raise BarcodeInputError(f"流水号文件内容为空或含首尾空白/换行: {path}")
    return value


class BarcodeRuleEngine:
    def __init__(self, product_ini: Path, date_ini: Path, output_dir: Path = Path(r"D:\data")) -> None:
        self.products = ProductRuleCatalog.from_file(product_ini)
        self.dates = DateCodeCatalog.from_file(date_ini)
        self.output_dir = Path(output_dir)

    def generate(self, product: str, station: StationId, when: datetime | None = None,
                 serial_override: str | None = None) -> LabelPayload:
        if not isinstance(station, StationId):
            raise BarcodeInputError(f"工位必须为 StationId: {station!r}")
        rule = self.products.rule_for(product.strip())
        serial = serial_override if serial_override is not None else read_serial(rule.serial_files[station])
        date_code = self.dates.date_code(rule.date_scheme, when or datetime.now())
        barcode = _apply_rule(rule.barcode_rule, {
            "客户型号": rule.customer_model,
            "日期": date_code,
            "工位号": rule.station_codes[station],
            "流水号": serial,
        })
        template = rule.template_for(station)
        if not template.is_file():
            raise TemplateMissingError(f"模板不存在: {template}")
        return LabelPayload(product, rule.customer_model, station, serial, barcode, date_code,
                            rule.ateq_program, template,
                            self.output_dir / f"二维码{station.value}.txt",
                            self.output_dir / f"打印路径{station.value}.txt",
                            self.output_dir / f"协众产品号{station.value}.txt")

    def publish(self, payload: LabelPayload) -> None:
        _atomic_write(payload.output_txt_path, payload.barcode_text)
        _atomic_write(payload.print_path_txt, str(payload.template_path))
        _atomic_write(payload.product_txt_path, payload.product_id)

    def advance_serial(self, product: str, station: StationId, when: datetime | None = None,
                       consumed_serial: str | None = None) -> str:
        """Consume the current serial and store the next one, resetting daily.

        现场规则：正常测试件流水号按周期递增（0001..9999），跨日归零。
        计数文件保持原有四位数字格式；日期锚点写在旁边的 ``序列号X日期.txt``。

        ``consumed_serial`` is the frozen serial attached to the cycle being
        closed.  If another process or a stale payload left the counter behind
        that value, the next value is based on the larger value instead of
        writing a duplicate back to disk.
        """
        if not isinstance(station, StationId):
            raise BarcodeInputError(f"工位必须为 StationId: {station!r}")
        rule = self.products.rule_for(product.strip())
        serial_path = Path(rule.serial_files[station])
        expected = None
        if consumed_serial is not None:
            raw_expected = str(consumed_serial).strip()
            if not raw_expected.isdigit() or not 0 <= int(raw_expected) <= 9999:
                raise BarcodeInputError(f"已消费流水号无效: {consumed_serial!r}")
            expected = int(raw_expected)
        with _serial_file_lock(serial_path):
            current = int(read_serial(serial_path))
            today = (when or datetime.now()).strftime("%Y%m%d")
            day_path = serial_path.with_name(f"{serial_path.stem}日期.txt")
            last_day = (day_path.read_text(encoding="utf-8-sig").strip()
                        if day_path.is_file() else "")
            if last_day and last_day != today:
                nxt = 1
            else:
                floor = max(current, expected if expected is not None else current)
                nxt = floor + 1 if floor < 9999 else 1
            _atomic_write(day_path, today)
            _atomic_write(serial_path, f"{nxt:04d}")
            return f"{nxt:04d}"

    def current_calibration_serial(self, product: str, station: StationId,
                                   when: datetime | None = None) -> str:
        """Read the current calibration serial (C001..C999) without advancing.

        现场规则：校准件流水号独立计数为 C001、C002...。周期创建时只读取
        当前号；样件验证通过后才递增（advance_calibration_serial），
        失败/重启的重试复用同一个号，保证 C001→C002→C003 连续。
        计数文件为 ``校准序列号X.txt``，日期锚点 ``校准序列号X日期.txt``。
        """
        if not isinstance(station, StationId):
            raise BarcodeInputError(f"工位必须为 StationId: {station!r}")
        rule = self.products.rule_for(product.strip())
        serial_path = Path(rule.serial_files[station]).with_name(
            f"校准序列号{station.value}.txt")
        today = (when or datetime.now()).strftime("%Y%m%d")
        day_path = serial_path.with_name(f"{serial_path.stem}日期.txt")
        last_day = day_path.read_text(encoding="utf-8-sig").strip() if day_path.is_file() else ""
        current = 0
        if serial_path.is_file():
            raw = read_serial(serial_path).strip()
            if raw[:1].upper() == "C" and raw[1:].isdigit():
                current = int(raw[1:])
        if last_day and last_day != today:
            _atomic_write(day_path, today)
            _atomic_write(serial_path, "C001")
            return "C001"
        if current >= 1:
            return f"C{current:03d}"
        # 首个校准周期：落盘 C001 作为当前号，验证通过后推进到 C002。
        _atomic_write(day_path, today)
        _atomic_write(serial_path, "C001")
        return "C001"

    def advance_calibration_serial(self, product: str, station: StationId,
                                   when: datetime | None = None) -> str:
        """Advance the calibration serial after a sample passed validation."""
        if not isinstance(station, StationId):
            raise BarcodeInputError(f"工位必须为 StationId: {station!r}")
        rule = self.products.rule_for(product.strip())
        serial_path = Path(rule.serial_files[station]).with_name(
            f"校准序列号{station.value}.txt")
        current = 0
        if serial_path.is_file():
            raw = read_serial(serial_path).strip()
            if raw[:1].upper() == "C" and raw[1:].isdigit():
                current = int(raw[1:])
        today = (when or datetime.now()).strftime("%Y%m%d")
        day_path = serial_path.with_name(f"{serial_path.stem}日期.txt")
        last_day = day_path.read_text(encoding="utf-8-sig").strip() if day_path.is_file() else ""
        if last_day and last_day != today:
            nxt = 1
        else:
            nxt = current + 1 if current < 999 else 1
        _atomic_write(day_path, today)
        _atomic_write(serial_path, f"C{nxt:03d}")
        return f"C{nxt:03d}"

    def generate_calibration(self, product: str, station: StationId,
                             when: datetime | None = None) -> LabelPayload:
        """Build and publish one calibration payload carrying the C-serial."""
        serial = self.current_calibration_serial(product, station, when)
        payload = self.generate(product, station, when, serial_override=serial)
        self.publish(payload)
        return payload


def _atomic_write(path: Path, value: str) -> None:
    """Write UTF-8 without BOM/newline and atomically replace the target."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def route_shared_scan(code: str, expected: dict[StationId, str], busy: set[StationId] | None = None) -> StationId:
    """Resolve a shared scanner frame by its frozen station QR-code prefix.

    Some scanners append auxiliary fields after the actual label payload.  The
    printed payload remains the source of truth; a frame is accepted when it
    equals that payload or starts with it.  This keeps unrelated labels out
    while allowing the scanner's trailing fields to be ignored.
    """
    normalized = code.strip()
    if not normalized or normalized != code:
        raise BarcodeInputError("扫码内容为空或包含首尾空白")
    matches = [station for station, candidate in expected.items()
               if scanner_code_matches(normalized, candidate)]
    if len(matches) != 1:
        raise BarcodeInputError("二维码未匹配任何工位" if not matches else "A/B 二维码相同，无法确定工位")
    station = matches[0]
    if busy and station in busy:
        raise BarcodeInputError(f"工位 {station.value} 正忙，拒绝扫码")
    return station


def scanner_code_matches(scanned: str, expected: str) -> bool:
    """Return whether a scanner frame contains the complete expected QR code.

    The expected value is generated by the configured barcode rule and is
    never truncated.  Only data appended *after* that value is ignored.
    """
    actual = str(scanned).strip()
    target = str(expected).strip()
    return bool(target) and actual.startswith(target)
