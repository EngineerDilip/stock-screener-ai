"""Deployment policy for ENABLED_MARKETS-style configuration."""

from __future__ import annotations

from collections.abc import Iterable

from ..domain.markets import normalize_market_codes


DEFAULT_DEPLOYMENT_ENABLED_MARKETS: tuple[str, ...] = ("US",)


def normalize_deployment_enabled_markets(
    raw: str | Iterable[object] | None,
) -> list[str]:
    """Parse and canonicalize deployment-enabled market codes.

    Blank deployment config intentionally defaults to US so single-market
    deployments keep their legacy worker and Beat topology.
    """
    if raw is None or isinstance(raw, str):
        raw_values = (raw or "").split(",")
    elif isinstance(raw, Iterable):
        raw_values = raw
    else:
        raw_values = [raw]

    values = [
        value
        for value in raw_values
        if value is not None and str(value).strip()
    ]
    if not values:
        values = list(DEFAULT_DEPLOYMENT_ENABLED_MARKETS)

    return normalize_market_codes(values)
