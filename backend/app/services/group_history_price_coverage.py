"""Exact adjusted-close coverage required to build Group history."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.domain.markets import get_market_catalog
from app.domain.relative_strength import HORIZON_SESSIONS
from app.models.stock import StockPrice
from app.services.group_rank_history_policy import (
    DEFAULT_CALENDAR_DAY_GROUP_RANK_HISTORY_LOOKBACK_DAYS,
)
from app.services.market_calendar_service import MarketCalendarService
from app.services.price_value_policy import is_usable_adjusted_close


@dataclass(frozen=True)
class GroupHistoryPriceCoverage:
    complete_symbols: tuple[str, ...]
    incomplete_symbols: tuple[str, ...]
    required_anchor_count: int
    available_anchor_counts: Mapping[str, int]
    incomplete_samples: tuple[str, ...] = ()


class GroupHistoryPriceCoverageService:
    """Classify symbols against every exact Market RS history anchor."""

    def __init__(
        self,
        *,
        calendar_service: MarketCalendarService | None = None,
        sample_limit: int = 20,
    ) -> None:
        self._calendar_service = calendar_service or MarketCalendarService()
        self._sample_limit = sample_limit

    def required_anchor_dates(
        self,
        *,
        market: str,
        through_date: date,
    ) -> frozenset[date]:
        normalized_market = str(market or "").strip().upper()
        if not (
            get_market_catalog().get(normalized_market).capabilities.group_rankings
        ):
            return frozenset()

        target_start = through_date - timedelta(
            days=DEFAULT_CALENDAR_DAY_GROUP_RANK_HISTORY_LOOKBACK_DAYS
        )
        target_dates = self._calendar_service.trading_days(
            normalized_market,
            target_start,
            through_date,
        )
        anchor_dates: set[date] = set()
        offsets = tuple(HORIZON_SESSIONS.values())
        for target_date in target_dates:
            anchors = self._calendar_service.session_anchors(
                normalized_market,
                target_date,
                offsets=offsets,
            )
            anchor_dates.update(anchors.values())
        return frozenset(anchor_dates)

    def classify(
        self,
        db: Session,
        *,
        market: str,
        through_date: date,
        symbols: Sequence[str],
    ) -> GroupHistoryPriceCoverage:
        normalized_symbols = tuple(
            dict.fromkeys(str(symbol or "").strip().upper() for symbol in symbols)
        )
        normalized_symbols = tuple(symbol for symbol in normalized_symbols if symbol)
        anchor_dates = self.required_anchor_dates(
            market=market,
            through_date=through_date,
        )
        if not anchor_dates:
            return GroupHistoryPriceCoverage(
                complete_symbols=normalized_symbols,
                incomplete_symbols=(),
                required_anchor_count=0,
                available_anchor_counts={},
            )

        available_by_symbol: dict[str, set[date]] = {}
        for chunk_start in range(0, len(normalized_symbols), 500):
            chunk_symbols = normalized_symbols[chunk_start : chunk_start + 500]
            rows = (
                db.query(StockPrice.symbol, StockPrice.date, StockPrice.adj_close)
                .filter(
                    StockPrice.symbol.in_(chunk_symbols),
                    StockPrice.date.in_(anchor_dates),
                )
                .all()
            )
            for symbol, row_date, adjusted_close in rows:
                if row_date is None or not is_usable_adjusted_close(adjusted_close):
                    continue
                available_by_symbol.setdefault(str(symbol).upper(), set()).add(
                    row_date
                )

        required_count = len(anchor_dates)
        available_counts = {
            symbol: len(available_by_symbol.get(symbol, set()))
            for symbol in normalized_symbols
        }
        incomplete = tuple(
            symbol
            for symbol in normalized_symbols
            if available_counts[symbol] < required_count
        )
        incomplete_set = set(incomplete)
        return GroupHistoryPriceCoverage(
            complete_symbols=tuple(
                symbol for symbol in normalized_symbols if symbol not in incomplete_set
            ),
            incomplete_symbols=incomplete,
            required_anchor_count=required_count,
            available_anchor_counts=available_counts,
            incomplete_samples=incomplete[: self._sample_limit],
        )


__all__ = [
    "GroupHistoryPriceCoverage",
    "GroupHistoryPriceCoverageService",
]
