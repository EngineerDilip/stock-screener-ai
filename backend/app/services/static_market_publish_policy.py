"""Shared policy for publishing multi-market static site artifacts."""
from __future__ import annotations

from app.domain.markets import SUPPORTED_MARKET_CODES

REQUIRED_STATIC_MARKETS = frozenset({"US"})
OPTIONAL_STATIC_MARKETS = frozenset(
    market for market in SUPPORTED_MARKET_CODES if market not in REQUIRED_STATIC_MARKETS
)
