"""Universe policy for bounded Market RS bootstrap and snapshots."""

from __future__ import annotations

from app.services.bounded_history_universe import (
    BOUNDED_HISTORY_CURRENT_ACTIVE_FALLBACK_POLICY,
    BOUNDED_HISTORY_POINT_IN_TIME_POLICY,
    CurrentActiveFallbackUniverseResolver,
)


MARKET_RS_POINT_IN_TIME_POLICY = BOUNDED_HISTORY_POINT_IN_TIME_POLICY
MARKET_RS_CURRENT_ACTIVE_FALLBACK_POLICY = (
    BOUNDED_HISTORY_CURRENT_ACTIVE_FALLBACK_POLICY
)


class MarketRsUniverseResolver(CurrentActiveFallbackUniverseResolver):
    """Prefer PIT universe evidence, falling back for accepted bootstrap windows."""


__all__ = [
    "MARKET_RS_CURRENT_ACTIVE_FALLBACK_POLICY",
    "MARKET_RS_POINT_IN_TIME_POLICY",
    "MarketRsUniverseResolver",
]
