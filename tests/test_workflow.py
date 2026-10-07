"""TC01-TC11: P01/P02 pure phase decision functions (no I/O)."""
from __future__ import annotations

import pytest

from app.models import Phase, Result
from app.workflow import next_phase_after_first, next_phase_after_second

ALL_MODES = ("single", "dual")
ALL_RESULTS = (Result.UNKNOWN, Result.OK, Result.NG)


def test_tc01_first_ok_single_production_reaches_marking():
    assert next_phase_after_first(Result.OK, "single", False, False) is Phase.MARKING


def test_tc02_first_ok_dual_production_waits_second():
    assert next_phase_after_first(Result.OK, "dual", False, False) is Phase.WAIT_2


@pytest.mark.parametrize("mode", ALL_MODES, ids=["TC03-single", "TC03-dual"])
@pytest.mark.parametrize("sample", [False, True], ids=["prod", "sample"])
@pytest.mark.parametrize("mark_samples", [False, True], ids=["noMarkSample", "markSample"])
def test_tc03_first_ng_completes_in_all_combinations(mode, sample, mark_samples):
    assert next_phase_after_first(Result.NG, mode, sample, mark_samples) is Phase.COMPLETE


@pytest.mark.parametrize("mode", ALL_MODES, ids=["TC04-single", "TC04-dual"])
def test_tc04_first_unknown_completes_and_never_marks(mode):
    assert next_phase_after_first(Result.UNKNOWN, mode, False, False) is Phase.COMPLETE
    assert next_phase_after_first(Result.UNKNOWN, mode, True, True) is Phase.COMPLETE


@pytest.mark.parametrize("mode", ALL_MODES, ids=["TC05-single", "TC05-dual"])
def test_tc05_sample_without_marking_completes_before_second_test(mode):
    assert next_phase_after_first(Result.OK, mode, True, False) is Phase.COMPLETE


def test_tc06_sample_marking_enabled_follows_mode():
    assert next_phase_after_first(Result.OK, "single", True, True) is Phase.MARKING
    assert next_phase_after_first(Result.OK, "dual", True, True) is Phase.WAIT_2


def test_tc07_second_ok_after_first_ok_reaches_marking():
    assert next_phase_after_second(Result.OK, Result.OK, False, False) is Phase.MARKING


@pytest.mark.parametrize("second", [Result.NG, Result.UNKNOWN],
                         ids=["TC08-second-NG", "TC08-second-UNKNOWN"])
def test_tc08_any_non_ok_second_completes(second):
    assert next_phase_after_second(Result.OK, second, False, False) is Phase.COMPLETE


def test_tc08_missing_or_non_ok_first_completes():
    assert next_phase_after_second(None, Result.OK, False, False) is Phase.COMPLETE
    assert next_phase_after_second(Result.NG, Result.OK, False, False) is Phase.COMPLETE
    assert next_phase_after_second(Result.UNKNOWN, Result.OK, False, False) is Phase.COMPLETE


def test_tc09_sample_pair_marking_policy():
    assert next_phase_after_second(Result.OK, Result.OK, True, False) is Phase.COMPLETE
    assert next_phase_after_second(Result.OK, Result.OK, True, True) is Phase.MARKING
    assert next_phase_after_second(Result.OK, Result.OK, False, False) is Phase.MARKING


def test_tc10_invalid_mode_and_non_result_values_rejected():
    with pytest.raises(ValueError):
        next_phase_after_first(Result.OK, "triple", False, False)
    with pytest.raises(TypeError):
        next_phase_after_first("OK", "single", False, False)
    with pytest.raises(TypeError):
        next_phase_after_first(None, "single", False, False)
    with pytest.raises(TypeError):
        next_phase_after_second(Result.OK, "OK", False, False)
    with pytest.raises(TypeError):
        next_phase_after_second("OK", Result.OK, False, False)
    with pytest.raises(TypeError):
        next_phase_after_second(Result.OK, None, False, False)


def _oracle_first(result, mode, sample, mark_samples):
    """Independent business truth table for BR06/BR07/BR08."""
    if result is not Result.OK:
        return Phase.COMPLETE
    if sample and not mark_samples:
        return Phase.COMPLETE
    return Phase.MARKING if mode == "single" else Phase.WAIT_2


def _oracle_second(first_result, second_result, sample, mark_samples):
    """Independent business truth table for BR09/BR10."""
    if first_result is not Result.OK or second_result is not Result.OK:
        return Phase.COMPLETE
    if sample and not mark_samples:
        return Phase.COMPLETE
    return Phase.MARKING


@pytest.mark.parametrize("mode", ALL_MODES)
@pytest.mark.parametrize("result", ALL_RESULTS)
@pytest.mark.parametrize("sample", [False, True])
@pytest.mark.parametrize("mark_samples", [False, True])
def test_tc11_first_cartesian_matches_business_oracle(mode, result, sample, mark_samples):
    assert next_phase_after_first(result, mode, sample, mark_samples) is _oracle_first(
        result, mode, sample, mark_samples)


@pytest.mark.parametrize("first_result", (None, *ALL_RESULTS))
@pytest.mark.parametrize("second_result", ALL_RESULTS)
@pytest.mark.parametrize("sample", [False, True])
@pytest.mark.parametrize("mark_samples", [False, True])
def test_tc11_second_cartesian_matches_business_oracle(first_result, second_result,
                                                        sample, mark_samples):
    assert next_phase_after_second(first_result, second_result, sample,
                                   mark_samples) is _oracle_second(
        first_result, second_result, sample, mark_samples)
