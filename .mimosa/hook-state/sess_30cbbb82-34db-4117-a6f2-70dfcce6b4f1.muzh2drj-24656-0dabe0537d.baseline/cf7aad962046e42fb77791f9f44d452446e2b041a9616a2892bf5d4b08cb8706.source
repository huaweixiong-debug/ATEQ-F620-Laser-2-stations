"""Calibration NG -> OK workflow."""
from __future__ import annotations
from enum import Enum

class CalibrationPhase(str, Enum):
    WAIT_NG = "等待NG样件"
    WAIT_OK = "等待OK样件"
    COMPLETE = "校准完成"

class Calibration:
    def __init__(self, required_samples: int = 1) -> None:
        if required_samples < 1: raise ValueError("校准样件数量必须大于 0")
        self.phase = CalibrationPhase.WAIT_NG
        self.required_samples = required_samples
        self.ng_count = self.ok_count = 0
        self.sample_demand = "NG"
        self.countdown = 0
    def sample(self, result: str) -> CalibrationPhase:
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
            else:
                self.phase = CalibrationPhase.WAIT_NG
                self.sample_demand = "NG"
        else: raise ValueError("校准样件顺序错误")
        return self.phase
