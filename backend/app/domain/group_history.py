"""Shared identity contracts for Group ranking history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class GroupHistoryTarget:
    market: str
    formula_version: str
    through_date: date

    def __post_init__(self) -> None:
        market = str(self.market or "").strip().upper()
        formula = str(self.formula_version or "").strip()
        if not market:
            raise ValueError("Group history target market is required")
        if not formula:
            raise ValueError("Group history target formula is required")
        object.__setattr__(self, "market", market)
        object.__setattr__(self, "formula_version", formula)


__all__ = ["GroupHistoryTarget"]
