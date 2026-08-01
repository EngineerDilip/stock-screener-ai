"""Shared adjusted-price validity predicates for Market RS inputs."""

from __future__ import annotations

import math


def is_valid_adjusted_price(value: float | None) -> bool:
    if value is None:
        return False
    try:
        price = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(price) and price > 0


__all__ = ["is_valid_adjusted_price"]
