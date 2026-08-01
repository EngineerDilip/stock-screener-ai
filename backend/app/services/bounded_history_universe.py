"""Universe policy for bounded historical repairs during bootstrap."""

from __future__ import annotations

from collections import Counter
from datetime import date

from sqlalchemy.orm import Session

from app.models.stock_universe import StockUniverse
from app.services.point_in_time_universe_service import (
    PointInTimeUniverse,
    PointInTimeUniverseService,
    PointInTimeUniverseUnavailable,
    hash_point_in_time_universe_symbols,
)

BOUNDED_HISTORY_POINT_IN_TIME_POLICY = "point_in_time"
BOUNDED_HISTORY_CURRENT_ACTIVE_FALLBACK_POLICY = "current_active_fallback_v1"


class CurrentActiveUniverse:
    """Resolve membership from the market's current active universe."""

    def resolve(
        self,
        db: Session,
        *,
        market: str,
        as_of_date: date,
    ) -> PointInTimeUniverse:
        normalized_market = str(market or "").strip().upper()
        symbols = tuple(
            symbol
            for (symbol,) in db.query(StockUniverse.symbol)
            .filter(
                StockUniverse.market == normalized_market,
                StockUniverse.active_filter(),
            )
            .order_by(StockUniverse.symbol.asc())
            .all()
        )
        return PointInTimeUniverse(
            market=normalized_market,
            as_of_date=as_of_date,
            symbols=symbols,
            universe_hash=hash_point_in_time_universe_symbols(symbols),
        )


class CurrentActiveFallbackUniverseResolver:
    """Prefer reproducible historical membership, with an explicit fallback."""

    def __init__(
        self,
        *,
        point_in_time_universe=None,
        current_active_universe=None,
        track_policies: bool = True,
    ) -> None:
        self._point_in_time_universe = (
            point_in_time_universe or PointInTimeUniverseService()
        )
        self._current_active_universe = (
            current_active_universe or CurrentActiveUniverse()
        )
        self._track_policies = bool(track_policies)
        self._policy_by_identity: dict[tuple[str, date], str] = {}

    def _record_policy(self, market: str, as_of_date: date, policy: str) -> None:
        if self._track_policies:
            self._policy_by_identity[(market, as_of_date)] = policy

    def resolve(
        self,
        db: Session,
        *,
        market: str,
        as_of_date: date,
    ) -> PointInTimeUniverse:
        normalized_market = str(market or "").strip().upper()
        try:
            universe = self._point_in_time_universe.resolve(
                db,
                market=normalized_market,
                as_of_date=as_of_date,
            )
        except PointInTimeUniverseUnavailable:
            universe = None
        if universe is not None and universe.symbols:
            self._record_policy(
                normalized_market, as_of_date, BOUNDED_HISTORY_POINT_IN_TIME_POLICY
            )
            return universe

        fallback = self._current_active_universe.resolve(
            db,
            market=normalized_market,
            as_of_date=as_of_date,
        )
        self._record_policy(
            normalized_market,
            as_of_date,
            BOUNDED_HISTORY_CURRENT_ACTIVE_FALLBACK_POLICY,
        )
        return fallback

    def policy_for(self, market: str, as_of_date: date) -> str | None:
        return self._policy_by_identity.get(
            (str(market or "").strip().upper(), as_of_date)
        )

    def policy_counts(self) -> dict[str, int]:
        return dict(Counter(self._policy_by_identity.values()))


__all__ = [
    "BOUNDED_HISTORY_CURRENT_ACTIVE_FALLBACK_POLICY",
    "BOUNDED_HISTORY_POINT_IN_TIME_POLICY",
    "CurrentActiveFallbackUniverseResolver",
    "CurrentActiveUniverse",
]
