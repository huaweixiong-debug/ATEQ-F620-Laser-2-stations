"""Committed product configuration boundary below the UI."""
from __future__ import annotations
from dataclasses import dataclass
from .permissions import SecurityContext

@dataclass(frozen=True)
class ProductSettings:
    product_no: str = "SIM-PART"

class ProductSettingsService:
    def __init__(self, security: SecurityContext, initial: str = "SIM-PART") -> None:
        self.security = security
        self._committed = ProductSettings(initial)

    @property
    def committed(self) -> ProductSettings:
        return self._committed

    def save(self, product_no: str) -> ProductSettings:
        self.security.require("settings")
        value = product_no.strip()
        if not value:
            raise ValueError("产品型号不能为空")
        self._committed = ProductSettings(value)
        return self._committed

    def current_product(self) -> str:
        return self._committed.product_no
