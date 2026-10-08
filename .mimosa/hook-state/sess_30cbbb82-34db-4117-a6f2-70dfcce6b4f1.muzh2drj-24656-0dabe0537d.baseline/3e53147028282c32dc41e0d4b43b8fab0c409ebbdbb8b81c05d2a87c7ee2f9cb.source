"""Offline license boundary. Cryptographic verification is live-deployment gated."""
from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class LicenseStatus:
    valid: bool
    reason: str

class LicenseVerifier:
    def __init__(self, simulator: bool = False) -> None:
        self.simulator = simulator
    def verify(self, payload: bytes, signature: bytes) -> LicenseStatus:
        if self.simulator and payload == b"SIMULATE" and signature == b"SIMULATE-SIGNATURE":
            return LicenseStatus(True, "模拟许可证")
        if not payload or not signature:
            return LicenseStatus(False, "缺少许可证或签名")
        return LicenseStatus(False, "生产许可证公钥和签名尚未配置")
