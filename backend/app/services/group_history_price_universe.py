"""Price symbols required by historical Group snapshot universes."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.domain.markets import get_market_catalog
from app.services.group_history_universe import GroupHistoryUniverseResolver
from app.services.group_rank_history_policy import (
    DEFAULT_CALENDAR_DAY_GROUP_RANK_HISTORY_LOOKBACK_DAYS,
)
from app.services.market_calendar_service import MarketCalendarService
from app.services.point_in_time_universe_service import PointInTimeUniverseService


class GroupHistoryPriceUniverseService:
    """Union symbols used by every desired historical Group snapshot."""

    def __init__(
        self,
        *,
        calendar_service: MarketCalendarService | None = None,
        universe_resolver: GroupHistoryUniverseResolver | None = None,
    ) -> None:
        self._calendar_service = calendar_service or MarketCalendarService()
        self._universe_resolver = universe_resolver or GroupHistoryUniverseResolver(
            point_in_time_universe=PointInTimeUniverseService(
                market_calendar=self._calendar_service
            )
        )

    def symbols(
        self,
        db: Session,
        *,
        market: str,
        through_date: date,
    ) -> tuple[str, ...]:
        normalized_market = str(market or "").strip().upper()
        if not get_market_catalog().get(normalized_market).capabilities.group_rankings:
            return ()

        target_dates = self._calendar_service.trading_days(
            normalized_market,
            through_date
            - timedelta(days=DEFAULT_CALENDAR_DAY_GROUP_RANK_HISTORY_LOOKBACK_DAYS),
            through_date,
        )
        symbols: dict[str, None] = {}
        for target_date in target_dates:
            universe = self._universe_resolver.resolve(
                db,
                market=normalized_market,
                as_of_date=target_date,
            )
            for symbol in universe.symbols:
                normalized_symbol = str(symbol or "").strip().upper()
                if normalized_symbol:
                    symbols.setdefault(normalized_symbol, None)
        return tuple(symbols)


__all__ = ["GroupHistoryPriceUniverseService"]
