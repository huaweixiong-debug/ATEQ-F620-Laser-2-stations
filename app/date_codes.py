"""Configurable date-code schemes (from 日期对照.ini) for the mark text.

Extracted from the Morocco project's barcode_rules.py: the scanning and
serial-counter machinery was removed, the date-scheme engine is retained
because the laser mark text carries a configurable 日期 field.
"""
from __future__ import annotations
from datetime import datetime
from pathlib import Path


class DateCodeError(Exception):
    pass


class DateConfigError(DateCodeError):
    pass


def parse_kv_ini(path: Path) -> dict[str, dict[str, str]]:
    """Parse the mixed ``=``/Chinese-colon INI format used on the line."""
    path = Path(path)
    if not path.is_file():
        raise DateConfigError(f"配置文件不存在: {path}")
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
                raise DateConfigError(f"{path.name} 第{line_no}行节名无效或重复")
            current = sections.setdefault(section_name, {})
            continue
        if current is None:
            raise DateConfigError(f"{path.name} 第{line_no}行位于配置节之外")
        split_at = [(line.find(mark), mark) for mark in ("=", "：") if line.find(mark) >= 0]
        if not split_at:
            raise DateConfigError(f"{path.name} [{section_name}] 第{line_no}行无法解析: {line!r}")
        pos, mark = min(split_at, key=lambda item: item[0])
        key, value = line[:pos].strip(), line[pos + len(mark):].strip()
        if not key or key in current:
            raise DateConfigError(f"{path.name} [{section_name}] 第{line_no}行键为空或重复: {key!r}")
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
            raise DateConfigError(f"日期方案无效: {scheme!r}")
        output = self._year(years[0], local)
        if months:
            output += self._indexed(months[0], "月", local.month)
        if days:
            output += self._day(days[0], local)
        return output

    def _section_value(self, section: str, key: str) -> str:
        values = self.sections.get(section)
        if values is None or key not in values:
            raise DateConfigError(f"日期对照.ini 缺少 [{section}]/{key}")
        return values[key]

    def _year(self, scheme: str, when: datetime) -> str:
        raw = self._section_value(scheme, "年")
        choices = [item.strip() for item in raw.split(",") if item.strip()]
        year = str(when.year)
        values = self.sections[scheme]
        if "对应" in values:
            mapped = [item.strip() for item in values["对应"].split(",") if item.strip()]
            if len(mapped) != len(choices) or year not in choices:
                raise DateConfigError(f"[{scheme}] 年份映射无效或不含 {year}")
            return mapped[choices.index(year)]
        for item in choices:
            if year == item or year[-2:] == item:
                return item
        raise DateConfigError(f"[{scheme}] 不支持年份 {year}")

    def _indexed(self, scheme: str, key: str, index: int) -> str:
        raw = self._section_value(scheme, key)
        if not raw:
            return ""
        choices = [item.strip() for item in raw.split(",") if item.strip()]
        if not 1 <= index <= len(choices):
            raise DateConfigError(f"[{scheme}] {key}映射不完整")
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
