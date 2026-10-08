"""Prepare D:\data assets safely; no hardware, DB, printer, or PLC calls."""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.barcode_rules import parse_kv_ini


DATE_MAP = """[日期对照]
方案=年方案X+月方案X+日方案X

[年方案1]
年=2025,2026,2027,2028,2029,2030,2031,2032,2033,2034,2035,2036,2037,2038,2039,2040
[年方案2]
年=25,26,27,28,29,30,31,32,33,34,35,36,37,38,39,40
[年方案3]
年=2025,2026,2027,2028,2029,2030,2031,2032,2033,2034,2035,2036,2037,2038,2039,2040
对应=S,T,V,W,X,Y,1,2,3,4,5,6,7,8,9,A

[月方案1]
月=01,02,03,04,05,06,07,08,09,10,11,12
[月方案2]
月=1,2,3,4,5,6,7,8,9,X,Y,Z
[月方案3]
月=1,2,3,4,5,6,7,8,9,A,B,C
[月方案4]
月=

[日方案1]
日=
[日方案2]
日=1-31
[日方案3]
日=1-365
"""


def _write_ini(path: Path, sections: dict[str, dict[str, str]]) -> None:
    content = "\n\n".join("\n".join([f"[{name}]", *(f"{key}={value}" for key, value in values.items())])
                              for name, values in sections.items()) + "\n"
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(content, encoding="utf-8", newline="")
    temp.replace(path)


def prepare(source: Path, target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    backup = target / "_backup" / datetime.now().strftime("%Y%m%d-%H%M%S")
    backup.mkdir(parents=True)
    names = ["日期设置.ini", "日期对照.ini", "Setup.ini", "EV80016300-B.btw",
             "Cal_NG_A.btw", "Cal_NG_B.btw", "Cal_OK_A.btw", "Cal_OK_B.btw"]
    for name in names:
        existing = target / name
        if existing.exists():
            shutil.copy2(existing, backup / name)

    product_file = target / "日期设置.ini"
    sections = parse_kv_ini(product_file)
    for product, values in sections.items():
        program_a = values.get("ATEQ程序号", values.get("ATEQ程序号A", "1")).strip()
        program_b = values.get("ATEQ程序号", values.get("ATEQ程序号B", program_a)).strip()
        if program_a != program_b:
            raise RuntimeError(f"[{product}] 旧 A/B 程序号冲突，拒绝自动合并: {program_a}/{program_b}")
        values["工位号A"] = values.get("工位号A", "8")
        values["工位号B"] = values.get("工位号B", "9")
        values["流水号A"] = r"D:\data\序列号A.txt"
        values["流水号B"] = r"D:\data\序列号B.txt"
        values["ATEQ程序号"] = program_a
        values["打印模板"] = fr"D:\data\{product}.btw"
        values["打印路径A"] = fr"D:\data\{product}-A.btw"
        values["打印路径B"] = fr"D:\data\{product}-B.btw"
        values.pop("ATEQ程序号A", None)
        values.pop("ATEQ程序号B", None)
    _write_ini(product_file, sections)
    (target / "日期对照.ini").write_text(DATE_MAP, encoding="utf-8", newline="")

    for name in ("Cal_NG_A.btw", "Cal_NG_B.btw", "Cal_OK_A.btw", "Cal_OK_B.btw"):
        shutil.copy2(source / name, target / name)
    ev_a, ev_b = target / "EV80016300-A.btw", target / "EV80016300-B.btw"
    if not ev_b.exists():
        shutil.copy2(ev_a, ev_b)
    # ATEQ mappings are intentionally invalidated until each physical unit is connected and identified.
    (target / "Setup.ini").write_text(
        "ATEQF620A(restartsoftwaretoactive)=UNCONFIRMED\n"
        "ATEQF620B(restartsoftwaretoactive)=UNCONFIRMED\n",
        encoding="utf-8", newline="")
    return backup


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--target", type=Path, required=True)
    args = parser.parse_args()
    print(f"backup={prepare(args.source, args.target)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
