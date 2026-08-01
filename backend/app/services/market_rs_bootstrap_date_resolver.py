"""Resolve the latest safe through-date for bootstrap-time Market RS activation."""

from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.domain.relative_strength import HORIZON_SESSIONS
from app.domain.relative_strength.price_validity import is_valid_adjusted_price
from app.models.stock import StockPrice
from app.services.benchmark_registry_service import benchmark_registry
from app.services.market_calendar_service import MarketCalendarService
from app.services.market_rs_rollout_contracts import (
    MarketRsBootstrapThroughDateResolution,
    normalize_rollout_market,
)

BENCHMARK_READY_DATE_SEARCH_SAFETY_DAYS = 7


class MarketRsBootstrapThroughDateResolver:
    """Find the newest bootstrap date whose benchmark anchors are usable."""

    def __init__(self, *, calendar_service: MarketCalendarService) -> None:
        self.calendar_service = calendar_service

    def resolve(
        self,
        db: Session,
        *,
        market: str,
        requested_through_date: date,
    ) -> MarketRsBootstrapThroughDateResolution:
        normalized = normalize_rollout_market(market)
        candidates = tuple(benchmark_registry.get_candidate_symbols(normalized))
        max_lag_days = max(
            0,
            int(settings.market_rs_bootstrap_benchmark_max_lag_days),
        )
        earliest_candidate_date = requested_through_date - timedelta(
            days=max_lag_days + BENCHMARK_READY_DATE_SEARCH_SAFETY_DAYS
        )
        benchmark_through_date = (
            self._latest_benchmark_ready_date(
                db,
                market=normalized,
                requested_through_date=requested_through_date,
                earliest_candidate_date=earliest_candidate_date,
                candidates=candidates,
            )
            if candidates
            else None
        )
        if not isinstance(benchmark_through_date, date):
            return MarketRsBootstrapThroughDateResolution(
                market=normalized,
                requested_through_date=requested_through_date,
                selected_through_date=requested_through_date,
                benchmark_through_date=None,
                benchmark_lag_days=None,
                reason_code="benchmark_date_unavailable",
            )
        if benchmark_through_date >= requested_through_date:
            return MarketRsBootstrapThroughDateResolution(
                market=normalized,
                requested_through_date=requested_through_date,
                selected_through_date=requested_through_date,
                benchmark_through_date=benchmark_through_date,
                benchmark_lag_days=0,
                reason_code="requested_date_ready",
            )

        lag_days = (requested_through_date - benchmark_through_date).days
        if lag_days > max_lag_days:
            return MarketRsBootstrapThroughDateResolution(
                market=normalized,
                requested_through_date=requested_through_date,
                selected_through_date=requested_through_date,
                benchmark_through_date=benchmark_through_date,
                benchmark_lag_days=lag_days,
                reason_code="benchmark_lag_exceeds_policy",
            )
        return MarketRsBootstrapThroughDateResolution(
            market=normalized,
            requested_through_date=requested_through_date,
            selected_through_date=benchmark_through_date,
            benchmark_through_date=benchmark_through_date,
            benchmark_lag_days=lag_days,
            reason_code="benchmark_ready_lag",
        )

    def _latest_benchmark_ready_date(
        self,
        db: Session,
        *,
        market: str,
        requested_through_date: date,
        earliest_candidate_date: date,
        candidates: tuple[str, ...],
    ) -> date | None:
        current_dates = self._usable_benchmark_current_dates(
            db,
            candidates=candidates,
            requested_through_date=requested_through_date,
            earliest_candidate_date=earliest_candidate_date,
        )
        for current_date in current_dates:
            try:
                anchors = self.calendar_service.session_anchors(
                    market,
                    current_date,
                    offsets=tuple(HORIZON_SESSIONS.values()),
                )
            except ValueError:
                continue
            anchor_dates = frozenset(anchors.values())
            if self._has_complete_benchmark_anchor_set(
                db,
                candidates=candidates,
                anchor_dates=anchor_dates,
            ):
                return current_date
        return None

    @staticmethod
    def _usable_benchmark_current_dates(
        db: Session,
        *,
        candidates: tuple[str, ...],
        requested_through_date: date,
        earliest_candidate_date: date,
    ) -> tuple[date, ...]:
        rows = (
            db.query(StockPrice.date, StockPrice.adj_close)
            .filter(
                StockPrice.symbol.in_(candidates),
                StockPrice.date <= requested_through_date,
                StockPrice.date >= earliest_candidate_date,
            )
            .order_by(StockPrice.date.desc())
            .all()
        )
        usable_dates: list[date] = []
        seen: set[date] = set()
        for row in rows:
            if row.date in seen or not is_valid_adjusted_price(row.adj_close):
                continue
            seen.add(row.date)
            usable_dates.append(row.date)
        return tuple(usable_dates)

    @staticmethod
    def _has_complete_benchmark_anchor_set(
        db: Session,
        *,
        candidates: tuple[str, ...],
        anchor_dates: frozenset[date],
    ) -> bool:
        if not anchor_dates:
            return False
        rows = (
            db.query(StockPrice.symbol, StockPrice.date, StockPrice.adj_close)
            .filter(
                StockPrice.symbol.in_(candidates),
                StockPrice.date.in_(tuple(anchor_dates)),
            )
            .all()
        )
        valid_dates_by_symbol: dict[str, set[date]] = {}
        for row in rows:
            if not is_valid_adjusted_price(row.adj_close):
                continue
            valid_dates_by_symbol.setdefault(row.symbol, set()).add(row.date)
        return any(
            anchor_dates.issubset(valid_dates)
            for valid_dates in valid_dates_by_symbol.values()
        )


__all__ = ["MarketRsBootstrapThroughDateResolver"]
