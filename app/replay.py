"""Offline ATEQ fixture replay with identity and numeric tolerance checks."""
from __future__ import annotations
import json
import math
from pathlib import Path
from typing import Any

def load_fixture(path: Path) -> list[dict[str, Any]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("fixture 必须是列表")
    if not all(isinstance(row, dict) for row in value):
        raise ValueError("fixture 每行必须是对象")
    return value

def compare(expected: list[dict[str, Any]], observed: list[dict[str, Any]], tolerance: float = 0.0) -> list[str]:
    errors: list[str] = []
    for index in range(max(len(expected), len(observed))):
        if index >= len(expected):
            errors.append(f"记录 {index} 为额外响应")
            continue
        if index >= len(observed):
            errors.append(f"记录 {index} 缺少响应")
            continue
        left, right = expected[index], observed[index]
        for key in set(left) | set(right):
            if key not in left:
                errors.append(f"记录 {index} 多出字段 {key}")
            elif key not in right:
                errors.append(f"记录 {index} 缺少字段 {key}")
            elif isinstance(left[key], (int, float)) and isinstance(right[key], (int, float)):
                if not math.isclose(float(left[key]), float(right[key]), abs_tol=tolerance, rel_tol=0.0):
                    errors.append(f"记录 {index}.{key} 数值超差")
            elif left[key] != right[key]:
                errors.append(f"记录 {index}.{key} 不一致")
    return errors

def compare_report(expected: list[dict[str, Any]], observed: list[dict[str, Any]], tolerance: float = 0.0) -> dict[str, Any]:
    errors = compare(expected, observed, tolerance)
    return {"ok": not errors, "expected_count": len(expected), "observed_count": len(observed),
            "tolerance": tolerance, "errors": errors}

def write_report(path: Path, report: dict[str, Any]) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
