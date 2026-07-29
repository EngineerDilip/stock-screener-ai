"""Canonical Market value object."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .catalog import get_market_catalog

SUPPORTED_MARKET_CODE_ORDER: tuple[str, ...] = tuple(
    get_market_catalog().supported_market_codes()
)
SUPPORTED_MARKET_CODES: frozenset[str] = frozenset(SUPPORTED_MARKET_CODE_ORDER)
DEFAULT_ENABLED_MARKET_CODES: tuple[str, ...] = ("US",)


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


def normalize_enabled_markets(
    raw: str | Iterable[str] | None,
    *,
    default: Iterable[str] = DEFAULT_ENABLED_MARKET_CODES,
) -> list[str]:
    """Return canonical deployment-enabled markets in caller order.

    This is shared by the runtime settings and Docker Compose worker helper so
    Beat schedules and deployed worker profiles interpret ENABLED_MARKETS the
    same way.
    """
    raw_values: Iterable[str]
    if raw is None or isinstance(raw, str):
        raw_values = (raw or "").split(",")
    else:
        raw_values = raw

    values = [str(value).strip().upper() for value in raw_values if str(value).strip()]
    if not values:
        values = [str(value).strip().upper() for value in default if str(value).strip()]

    normalized: list[str] = []
    unsupported: list[str] = []
    for value in values:
        try:
            market = Market.from_str(value).code
        except UnsupportedMarketError:
            unsupported.append(value)
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
