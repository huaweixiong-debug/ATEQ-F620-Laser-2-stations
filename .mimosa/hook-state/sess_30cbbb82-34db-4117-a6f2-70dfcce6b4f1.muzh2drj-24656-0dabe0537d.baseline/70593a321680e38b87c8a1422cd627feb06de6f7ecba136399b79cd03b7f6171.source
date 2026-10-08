"""Prepare D:\data assets safely; no hardware, DB, laser, or PLC calls."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATE_MAP = """[日期对照]
方案=YYYYMMDD

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

MODEL_MAP = """[E118015100]
客户型号=E118015100
日期=YYYYMMDD
ATEQ程序号=1
"""

PERSONNEL = "张三\n"

ADMIN = "0000\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="准备 D:\\data 配置与 D:\\激光打码.txt 监听文件")
    parser.add_argument("--data-dir", default=r"D:\data")
    parser.add_argument("--laser-dir", default="D:\\")
    parser.add_argument("--laser-file", default="激光打码.txt")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的样例文件")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    laser_dir = Path(args.laser_dir)
    laser_dir.mkdir(parents=True, exist_ok=True)

    targets = {
        data_dir / "日期对照.ini": DATE_MAP,
        data_dir / "日期设置.ini": MODEL_MAP,
        data_dir / "作业员列表.txt": PERSONNEL,
        data_dir / "管理员.txt": ADMIN,
    }
    for path, content in targets.items():
        if path.exists() and not args.force:
            print(f"SKIP {path}（已存在，--force 覆盖）")
            continue
        path.write_text(content, encoding="utf-8")
        print(f"WROTE {path}")

    watched = laser_dir / args.laser_file
    if not watched.exists():
        watched.write_bytes(b"")
        print(f"WROTE {watched}（空监听文件）")
    else:
        print(f"SKIP {watched}（已存在）")
    print("数据准备完成；型号参数请在 UI 设置页修改。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
