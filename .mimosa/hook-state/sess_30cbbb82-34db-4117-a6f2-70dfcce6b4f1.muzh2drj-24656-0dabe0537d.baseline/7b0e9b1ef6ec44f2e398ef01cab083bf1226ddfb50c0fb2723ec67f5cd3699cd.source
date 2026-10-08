"""Per-model production settings and personnel list, persisted in D:\\data.

Model parameters follow the original 日期设置.ini contract (one section per
part number) extended with the ATEQ F620 program numbers.  The personnel
list is intentionally independent of models and stored in its own file.
Every mutation requires the admin "settings" permission through the
security context; reads are open.
"""
from __future__ import annotations
import configparser
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .permissions import SecurityContext
from .barcode_rules import parse_kv_ini

BARCODE_RULE_PRESETS = [
    "客户型号+#DPPH8#+日期+工位号+流水号",
    "客户型号+#DFBCD#+日期+工位号+流水号",
    "客户型号+日期+工位号+流水号",
    "客户型号T日期+工位号+流水号",
]

DATE_SCHEME_PRESETS = [
    "YYYYMMDD", "YYMMDD", "YYYYMM", "YYMM", "YYYYM", "YYM",
    "YYYYDDD", "YYDDD", "YYYY",
]


@dataclass
class ModelConfig:
    part_no: str = ""
    customer_no: str = ""
    barcode_rule: str = BARCODE_RULE_PRESETS[0]
    date_scheme: str = DATE_SCHEME_PRESETS[0]
    ateq_program: str = "1"
    template_family: str = ""
    station_code_a: str = "5"
    station_code_b: str = "6"
    serial_path_a: str = r"D:\data\序列号A.txt"
    serial_path_b: str = r"D:\data\序列号B.txt"
    # Keep the logical family for old configurations, while retaining the
    # explicit A/B paths edited on the setup page.
    template_path_a: str = ""
    template_path_b: str = ""

    def __post_init__(self) -> None:
        try:
            program = int(str(self.ateq_program).strip())
        except ValueError as exc:
            raise ValueError("ATEQ 程序号必须是 1-255 整数") from exc
        if not 1 <= program <= 255:
            raise ValueError("ATEQ 程序号必须是 1-255 整数")
        self.ateq_program = str(program)
        if not self.template_path_a.strip():
            self.template_path_a = self._derive_template("A")
        if not self.template_path_b.strip():
            self.template_path_b = self._derive_template("B")

    @property
    def ateq_program_a(self) -> str:  # compatibility with the first UI release
        return self.ateq_program

    @property
    def ateq_program_b(self) -> str:
        return self.ateq_program

    @property
    def template_a(self) -> str:
        return self.template_for("A")

    @property
    def template_b(self) -> str:
        return self.template_for("B")

    def template_for(self, station: str) -> str:
        explicit = self.template_path_a if station.upper() == "A" else self.template_path_b
        if explicit.strip():
            return explicit.strip()
        return self._derive_template(station)

    def _derive_template(self, station: str) -> str:
        family = self.template_family.strip() or fr"D:\data\{self.part_no}.btw"
        path = Path(family)
        stem = path.stem[:-2] if path.stem.upper().endswith(("-A", "-B")) else path.stem
        return str(path.with_name(f"{stem}-{station}.btw"))

    def to_dict(self) -> dict[str, str]:
        return {
            "条码规则": self.barcode_rule,
            "客户型号": self.customer_no,
            "工位号A": self.station_code_a,
            "工位号B": self.station_code_b,
            "流水号A": self.serial_path_a,
            "流水号B": self.serial_path_b,
            "打印模板": self.template_family,
            "打印路径A": self.template_a,
            "打印路径B": self.template_b,
            "ATEQ程序号": self.ateq_program,
            "日期": self.date_scheme,
        }

    @classmethod
    def from_section(cls, part_no: str, section: dict[str, str]) -> "ModelConfig":
        get = lambda key, default="": section.get(key, default).strip()
        return cls(
            part_no=part_no,
            customer_no=get("客户型号"),
            barcode_rule=get("条码规则", BARCODE_RULE_PRESETS[0]),
            date_scheme=get("日期", DATE_SCHEME_PRESETS[0]),
            ateq_program=get("ATEQ程序号", get("ATEQ程序号A", "1")),
            template_family=get("打印模板", get("打印路径A", fr"D:\data\{part_no}-A.btw")),
            station_code_a=get("工位号A", "5"),
            station_code_b=get("工位号B", "6"),
            serial_path_a=get("流水号A", r"D:\data\序列号A.txt"),
            serial_path_b=get("流水号B", r"D:\data\序列号B.txt"),
            template_path_a=get("打印路径A"),
            template_path_b=get("打印路径B"),
        )


class ModelSettingsService:
    """Read/write D:\\data\\日期设置.ini; mutations are admin-gated."""

    def __init__(self, security: SecurityContext, path: Path = Path(r"D:\data\日期设置.ini")) -> None:
        self.security = security
        self.path = Path(path)

    def _read_raw(self) -> dict[str, dict[str, str]]:
        if not self.path.exists():
            return {}
        return parse_kv_ini(self.path)

    def list_models(self) -> list[str]:
        return list(self._read_raw().keys())

    def load(self, part_no: str) -> ModelConfig:
        raw = self._read_raw()
        if part_no not in raw:
            raise KeyError(f"日期设置.ini 中没有型号 {part_no}")
        return ModelConfig.from_section(part_no, raw[part_no])

    def save(self, config: ModelConfig) -> ModelConfig:
        self.security.require("settings")
        part = config.part_no.strip()
        if not part:
            raise ValueError("产品型号不能为空")
        raw = self._read_raw()
        raw[part] = config.to_dict()
        self._write_raw(raw)
        return config

    def save_all(self, configs: list[ModelConfig]) -> list[ModelConfig]:
        """整表写回：表格是型号列表的唯一来源，替换 日期设置.ini 全部内容。"""
        self.security.require("settings")
        raw: dict[str, dict[str, str]] = {}
        for config in configs:
            part = config.part_no.strip()
            if not part:
                raise ValueError("产品型号不能为空")
            if part in raw:
                raise ValueError(f"产品型号重复: {part}")
            raw[part] = config.to_dict()
        self._write_raw(raw)
        return configs

    def delete(self, part_no: str) -> None:
        self.security.require("settings")
        raw = self._read_raw()
        if part_no not in raw:
            raise KeyError(f"日期设置.ini 中没有型号 {part_no}")
        del raw[part_no]
        self._write_raw(raw)

    def _write_raw(self, raw: dict[str, dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        text = "\n\n".join(
            "\n".join([f"[{name}]", *(f"{key}={value}" for key, value in values.items())])
            for name, values in raw.items()) + ("\n" if raw else "")
        fd, temp_name = tempfile.mkstemp(dir=str(self.path.parent), prefix=self.path.name + ".", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise


class GlobalSettingsService:
    """与型号无关的全局参数（工位号 A/B、校准周期），存 D:\data\全局设置.ini。"""

    DEFAULTS = {"工位号A": "5", "工位号B": "6", "校准周期": "02:00:00"}

    def __init__(self, security: SecurityContext, path: Path = Path(r"D:\data\全局设置.ini")) -> None:
        self.security = security
        self.path = Path(path)

    def load(self) -> dict[str, str]:
        values = dict(self.DEFAULTS)
        if not self.path.exists():
            return values
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        try:
            parser.read(self.path, encoding="utf-8")
        except (OSError, configparser.Error, UnicodeDecodeError):
            return values
        if parser.has_section("全局"):
            for key, value in parser.items("全局"):
                values[key] = value.strip()
        return values

    def save(self, values: dict[str, str]) -> dict[str, str]:
        self.security.require("settings")
        merged = self.load()
        merged.update({key: str(value).strip() for key, value in values.items()})
        parser = configparser.ConfigParser(interpolation=None)
        parser.optionxform = str
        parser["全局"] = merged
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            parser.write(handle)
        return merged


class PersonnelService:
    """Independent operator list; one entry per line, admin-gated edits."""

    def __init__(self, security: SecurityContext, path: Path = Path(r"D:\data\作业员列表.txt")) -> None:
        self.security = security
        self.path = Path(path)

    def list_all(self) -> list[str]:
        if not self.path.exists():
            return []
        try:
            text = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            text = self.path.read_text(encoding="gbk", errors="replace")
        return [line.strip() for line in text.splitlines() if line.strip()]

    def add(self, name: str) -> str:
        self.security.require("settings")
        value = name.strip()
        if not value:
            raise ValueError("人员姓名/工号不能为空")
        rows = self.list_all()
        if value in rows:
            raise ValueError(f"人员已存在: {value}")
        rows.append(value)
        self._write(rows)
        return value

    def remove(self, name: str) -> None:
        self.security.require("settings")
        rows = [row for row in self.list_all() if row != name.strip()]
        self._write(rows)

    def write_all(self, names: list[str]) -> list[str]:
        """整表写回：空行忽略，其余按输入顺序保存。"""
        self.security.require("settings")
        cleaned = [str(name).strip() for name in names if str(name).strip()]
        self._write(cleaned)
        return cleaned

    def _write(self, rows: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
