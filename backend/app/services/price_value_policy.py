"""Shared validity checks for persisted market price values."""

from __future__ import annotations

from app.infra.serialization import finite_float_or_none


def is_usable_adjusted_close(value: object) -> bool:
    price = finite_float_or_none(value)
    return price is not None and price > 0


__all__ = ["is_usable_adjusted_close"]
