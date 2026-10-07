"""Pure phase-decision functions extracted from StationController (P01/P02).

BR06-BR10 stay frozen: these functions are the single source of truth for the
first/second measurement branch matrix and perform no I/O.
"""
from __future__ import annotations

from .models import Phase, Result


def _require_result(value, name: str) -> None:
    if not isinstance(value, Result):
        raise TypeError(f"{name} 必须是 Result 枚举，实际 {type(value).__name__}")


def next_phase_after_first(result: Result, mode: str, sample: bool,
                           mark_samples: bool) -> Phase:
    """P01: branch after the first measurement (first measurement is mandatory)."""
    _require_result(result, "result")
    if mode not in ("single", "dual"):
        raise ValueError(f"检测模式无效: {mode}")
    if result is not Result.OK:
        return Phase.COMPLETE
    if sample and not mark_samples:
        return Phase.COMPLETE
    return Phase.MARKING if mode == "single" else Phase.WAIT_2


def next_phase_after_second(first_result: Result | None, second_result: Result,
                            sample: bool, mark_samples: bool) -> Phase:
    """P02: branch after the second measurement (defensive double-OK check)."""
    if first_result is not None:
        _require_result(first_result, "first_result")
    _require_result(second_result, "second_result")
    if first_result is not Result.OK or second_result is not Result.OK:
        return Phase.COMPLETE
    if sample and not mark_samples:
        return Phase.COMPLETE
    return Phase.MARKING
