"""Canonical Market value object."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .catalog import get_market_catalog

SUPPORTED_MARKET_CODE_ORDER: tuple[str, ...] = tuple(
    get_market_catalog().supported_market_codes()
)
SUPPORTED_MARKET_CODES: frozenset[str] = frozenset(SUPPORTED_MARKET_CODE_ORDER)


class UnsupportedMarketError(ValueError):
    """Raised when a Market code is missing, blank, or unsupported."""


@dataclass(frozen=True, slots=True)
class Market:
    """Canonical supported Market code.

    The constructor is intentionally strict. Use ``from_str`` for wire/user
    input that may need whitespace and case normalization.
    """

    code: str

    def __post_init__(self) -> None:
        if self.code not in SUPPORTED_MARKET_CODES:
            supported = ", ".join(sorted(SUPPORTED_MARKET_CODES))
            raise UnsupportedMarketError(
                f"Unsupported market code {self.code!r}. Supported: {supported}"
            )

    @classmethod
    def from_str(cls, raw: object | None) -> "Market":
        if raw is None:
            raise UnsupportedMarketError("Market code is required")
        if not isinstance(raw, str):
            raise UnsupportedMarketError(f"Market code must be a string, got {type(raw).__name__}")
        code = raw.strip().upper()
        if not code:
            raise UnsupportedMarketError("Market code is required")
        return cls(code)

    def __str__(self) -> str:
        return self.code


def normalize_market_codes(raw_values: Iterable[object]) -> list[str]:
    """Return canonical supported market codes in caller order.

    Domain validation only knows which market codes exist. Deployment-specific
    defaults, such as treating blank ENABLED_MARKETS as US, live outside this
    module.
    """
    values = [raw_values] if isinstance(raw_values, str) else raw_values
    normalized: list[str] = []
    unsupported: list[str] = []
    for value in values:
        try:
            market = Market.from_str(value).code
        except UnsupportedMarketError:
            label = str(value).strip().upper() if value is not None else "<missing>"
            unsupported.append(label or "<blank>")
            continue
        if market not in normalized:
            normalized.append(market)

    if unsupported:
        raise UnsupportedMarketError(
            "Unsupported market(s): "
            + ", ".join(unsupported)
            + ". Supported markets: "
            + ", ".join(SUPPORTED_MARKET_CODE_ORDER)
        )

    return normalized
