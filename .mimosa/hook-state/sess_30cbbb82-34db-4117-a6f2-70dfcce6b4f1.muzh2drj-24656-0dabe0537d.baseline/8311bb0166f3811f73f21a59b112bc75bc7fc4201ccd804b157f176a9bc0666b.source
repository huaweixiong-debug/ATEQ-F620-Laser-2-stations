"""Calibration NG -> OK workflow."""
from __future__ import annotations
from datetime import datetime, timezone
from enum import Enum
import time

from .models import StationId

class CalibrationPhase(str, Enum):
    WAIT_NG = "等待NG样件"
    WAIT_OK = "等待OK样件"
    COMPLETE = "校准完成"

class Calibration:
    def __init__(self, required_samples: int = 1, station: StationId | None = None,
                 *, initial_due: bool = False, period_seconds: int = 2 * 60 * 60) -> None:
        if required_samples < 1: raise ValueError("校准样件数量必须大于 0")
        if period_seconds <= 0: raise ValueError("校准周期必须大于 0 秒")
        self.phase = CalibrationPhase.WAIT_NG
        self.required_samples = required_samples
        self.ng_count = self.ok_count = 0
        self.sample_demand = "NG"
        # ``countdown`` remains the NG/OK sample-count compatibility field.
        # The production interval uses a separate monotonic timer so a
        # two-hour setting is not confused with the two validation samples.
        self.countdown = 0
        self.period_seconds = int(period_seconds)
        self.remaining_seconds = 0.0
        self._last_tick = time.monotonic()
        self.station = station
        self.due = False
        self.locked = False
        self.validation_started = False
        self._clear_pending = False
        self.audit_events: list[dict[str, str]] = []
        if initial_due:
            self.mark_due()

    @property
    def indicators(self) -> tuple[bool, bool, bool]:
        """Return ``(calibration_due, ng_verified, ok_verified)``.

        The three footer lights are state indicators, not operator controls.
        After the OK verification succeeds we deliberately keep all three
        lights on until the first successfully accepted production scan.  This
        makes the hand-off visible to the operator while still allowing the
        next cycle to clear the lamps atomically.
        """
        if not self.due and not self.validation_started and not self._clear_pending:
            return False, False, False
        return self.due, self.ng_count > 0, self.ok_count > 0

    @property
    def clear_pending(self) -> bool:
        """Whether the validated lamps await the next accepted scan."""
        return self._clear_pending

    def set_period(self, period_seconds: int) -> None:
        """Set the next production interval without changing validation state."""
        period_seconds = int(period_seconds)
        if period_seconds <= 0:
            raise ValueError("校准周期必须大于 0 秒")
        self.period_seconds = period_seconds

    def tick(self, elapsed_seconds: float | None = None) -> bool:
        """Advance the independent production timer.

        Returns ``True`` only when this tick expires the station interval and
        raises the calibration lock.  An explicit elapsed value keeps the
        state machine deterministic in tests; the UI uses the monotonic clock.
        """
        if self.due or self.remaining_seconds <= 0:
            self._last_tick = time.monotonic()
            return False
        if elapsed_seconds is None:
            now = time.monotonic()
            elapsed_seconds = max(0.0, now - self._last_tick)
            self._last_tick = now
        else:
            elapsed_seconds = max(0.0, float(elapsed_seconds))
            self._last_tick = time.monotonic()
        self.remaining_seconds = max(0.0, self.remaining_seconds - elapsed_seconds)
        if self.remaining_seconds <= 0:
            self.mark_due()
            return True
        return False

    def begin_validation(self) -> None:
        """Arm the NG -> OK validation before production or after a cycle."""
        if not self.due:
            raise RuntimeError("当前未到校准周期")
        if self._clear_pending:
            raise RuntimeError("校准已完成，等待下一周期清除状态")
        self.phase = CalibrationPhase.WAIT_NG
        self.ng_count = self.ok_count = 0
        self.sample_demand = "NG"
        self.countdown = self.required_samples
        self.locked = True
        self.validation_started = True
        self.remaining_seconds = 0.0
        self._last_tick = time.monotonic()

    def clear_after_resume(self) -> None:
        """Clear the three lamps when the next production cycle is accepted."""
        if self.phase is not CalibrationPhase.COMPLETE or not self._clear_pending:
            return
        self.due = False
        self.locked = False
        self.validation_started = False
        self._clear_pending = False
        self.ng_count = self.ok_count = 0
        self.sample_demand = ""
        self.countdown = 0
        self.remaining_seconds = float(self.period_seconds)
        self._last_tick = time.monotonic()

    def template_name(self, result: str) -> str:
        if self.station is None:
            raise ValueError("校准模板需要明确工位")
        if result not in ("NG", "OK"):
            raise ValueError("校准结果必须是 NG/OK")
        return f"Cal_{result}_{self.station.value}.btw"

    def sample(self, result: str) -> CalibrationPhase:
        result = str(result).strip().upper()
        if ((self.phase is CalibrationPhase.WAIT_NG and result != "NG") or
                (self.phase is CalibrationPhase.WAIT_OK and result != "OK")):
            raise ValueError("校准样件顺序错误")
        # Keep the old programmatic diagnostic API usable for tests and for
        # the setup-page diagnostic entry point.  Production UI starts this
        # same state through begin_validation().
        if not self.validation_started:
            self.validation_started = True
            self.due = True
            self.locked = True
        if self.phase is CalibrationPhase.WAIT_NG and result == "NG":
            self.ng_count += 1
            self.phase = CalibrationPhase.WAIT_OK
            self.sample_demand = "OK"
            self.countdown = self.required_samples
        elif self.phase is CalibrationPhase.WAIT_OK and result == "OK":
            self.ok_count += 1
            self.countdown = max(0, self.required_samples - self.ok_count)
            if self.ok_count >= self.required_samples:
                self.phase = CalibrationPhase.COMPLETE
                self.sample_demand = ""
                # Do not clear the lamps in the same event that validates the
                # sample.  The operator must see NG/OK accepted before the
                # next production scan clears all three indicators.
                self.due = True
                self.locked = False
                self._clear_pending = True
                self.remaining_seconds = 0.0
                self._last_tick = time.monotonic()
            else:
                self.phase = CalibrationPhase.WAIT_NG
                self.sample_demand = "NG"
        else: raise ValueError("校准样件顺序错误")
        return self.phase

    def mark_due(self) -> None:
        self.phase = CalibrationPhase.WAIT_NG
        self.ng_count = self.ok_count = 0
        self.sample_demand = "NG"
        self.countdown = 0
        self.due = self.locked = True
        self.validation_started = False
        self._clear_pending = False
        self.remaining_seconds = 0.0
        self._last_tick = time.monotonic()

    def cancel(self, actor: str, reason: str) -> None:
        if actor != "admin" or not reason.strip():
            raise PermissionError("取消校准仅限 admin 且必须填写原因")
        self.due = self.locked = False
        self.validation_started = False
        self._clear_pending = False
        self.phase = CalibrationPhase.COMPLETE
        self.ng_count = self.ok_count = 0
        self.countdown = 0
        self.sample_demand = ""
        self.remaining_seconds = float(self.period_seconds)
        self._last_tick = time.monotonic()
        self.audit_events.append({"actor": actor, "reason": reason.strip(),
                                  "station": self.station.value if self.station else "",
                                  "time": datetime.now(timezone.utc).isoformat()})
