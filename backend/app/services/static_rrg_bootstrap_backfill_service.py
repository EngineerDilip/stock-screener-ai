"""Backfill static-site RRG history from the current weekly-reference universe."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy.orm import Session

from app.analysis.rrg_weekly import rrg_week_start
from app.domain.markets import get_market_catalog
from app.infra.db.repositories.market_rs_repo import MarketRsRunRepository
from app.models.industry import IBDGroupRank
from app.services.canonical_group_ranking_service import CanonicalGroupRankingService
from app.services.group_rank_history_backfill_service import (
    DEFAULT_GROUP_RANK_HISTORY_LOOKBACK_DAYS,
)
from app.services.market_calendar_service import MarketCalendarService
from app.services.market_rs_inputs import MarketRsInputLoader
from app.services.market_rs_snapshot_service import MarketRsSnapshotService
from app.services.rrg_service import MIN_TAIL_WEEKS
from app.services.static_rrg_bootstrap_universe import (
    STATIC_RRG_BOOTSTRAP_UNIVERSE_POLICY,
    StaticRRGBootstrapUniverse,
)


class StaticRRGBootstrapBackfillStatus(StrEnum):
    COMPLETED = "completed"
    SKIPPED = "skipped"
    ERRORED = "errored"


@dataclass(frozen=True)
class StaticRRGBootstrapBackfillResult:
    status: StaticRRGBootstrapBackfillStatus
    market: str
    as_of_date: date
    formula_version: str
    lookback_start_date: date
    target_dates: tuple[date, ...] = ()
    existing: int = 0
    processed: int = 0
    errors: int = 0
    total_dates: int = 0
    policy: str = STATIC_RRG_BOOTSTRAP_UNIVERSE_POLICY
    reason: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "status": self.status.value,
            "market": self.market,
            "as_of_date": self.as_of_date.isoformat(),
            "formula_version": self.formula_version,
            "policy": self.policy,
            "lookback_start_date": self.lookback_start_date.isoformat(),
            "target_dates": [day.isoformat() for day in self.target_dates],
            "existing": self.existing,
            "processed": self.processed,
            "errors": self.errors,
            "total_dates": self.total_dates,
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        if self.error is not None:
            payload["error"] = self.error
        return payload


class StaticRRGBootstrapBackfillService:
    """Seed minimum weekly RRG history for static first-run publication."""

    def __init__(
        self,
        *,
        calendar_service: MarketCalendarService | None = None,
        market_rs_snapshot_service: MarketRsSnapshotService | None = None,
        canonical_group_service: CanonicalGroupRankingService | None = None,
        market_rs_repository: MarketRsRunRepository | None = None,
        lookback_days: int = DEFAULT_GROUP_RANK_HISTORY_LOOKBACK_DAYS,
    ) -> None:
        self.calendar_service = calendar_service or MarketCalendarService()
        self.lookback_days = lookback_days
        repository = market_rs_repository or MarketRsRunRepository()
        if market_rs_snapshot_service is None:
            input_loader = MarketRsInputLoader(
                point_in_time_universe=StaticRRGBootstrapUniverse(),
                market_calendar=self.calendar_service,
            )
            market_rs_snapshot_service = MarketRsSnapshotService(
                input_loader=input_loader,
                repository=repository,
            )
        self.market_rs_snapshot_service = market_rs_snapshot_service
        self.canonical_group_service = (
            canonical_group_service
            or CanonicalGroupRankingService(repository=repository)
        )

    @staticmethod
    def _weekly_targets(trading_days: list[date]) -> tuple[date, ...]:
        latest_by_week: dict[date, date] = {}
        for trading_day in trading_days:
            latest_by_week[rrg_week_start(trading_day)] = trading_day
        ordered = tuple(latest_by_week[week] for week in sorted(latest_by_week))
        return ordered[-MIN_TAIL_WEEKS:]

    def backfill(
        self,
        db: Session,
        *,
        market: str,
        through_date: date,
        formula_version: str,
    ) -> StaticRRGBootstrapBackfillResult:
        normalized_market = str(market or "").strip().upper()
        start_date = through_date - timedelta(days=self.lookback_days)
        if not get_market_catalog().rrg_scopes_for_market(normalized_market):
            return StaticRRGBootstrapBackfillResult(
                status=StaticRRGBootstrapBackfillStatus.SKIPPED,
                market=normalized_market,
                as_of_date=through_date,
                formula_version=formula_version,
                lookback_start_date=start_date,
                reason="rrg_not_enabled",
            )

        target_dates = self._weekly_targets(
            self.calendar_service.trading_days(
                normalized_market,
                start_date,
                through_date,
            )
        )
        if len(target_dates) < MIN_TAIL_WEEKS:
            return StaticRRGBootstrapBackfillResult(
                status=StaticRRGBootstrapBackfillStatus.ERRORED,
                market=normalized_market,
                as_of_date=through_date,
                formula_version=formula_version,
                lookback_start_date=start_date,
                target_dates=target_dates,
                total_dates=len(target_dates),
                reason="insufficient_trading_weeks",
            )

        existing_dates = {
            record_date
            for record_date, in db.query(IBDGroupRank.date)
            .filter(
                IBDGroupRank.market == normalized_market,
                IBDGroupRank.date.in_(target_dates),
                IBDGroupRank.rs_formula_version == formula_version,
            )
            .distinct()
            .all()
        }

        processed = 0
        errors: list[str] = []
        for target_date in target_dates:
            if target_date in existing_dates:
                continue
            try:
                self.market_rs_snapshot_service.calculate(
                    db,
                    market=normalized_market,
                    as_of_date=target_date,
                    formula_version=formula_version,
                )
                group_rows = self.canonical_group_service.calculate_and_store(
                    db,
                    market=normalized_market,
                    as_of_date=target_date,
                    formula_version=formula_version,
                )
                if not group_rows:
                    raise RuntimeError("No group-ranking rows were produced")
            except Exception as exc:
                db.rollback()
                errors.append(f"{target_date.isoformat()}: {exc}")
                continue
            processed += 1

        filled = len(existing_dates) + processed
        status = (
            StaticRRGBootstrapBackfillStatus.COMPLETED
            if not errors and filled >= MIN_TAIL_WEEKS
            else StaticRRGBootstrapBackfillStatus.ERRORED
        )
        return StaticRRGBootstrapBackfillResult(
            status=status,
            market=normalized_market,
            as_of_date=through_date,
            formula_version=formula_version,
            lookback_start_date=start_date,
            target_dates=target_dates,
            existing=len(existing_dates),
            processed=processed,
            errors=len(errors),
            total_dates=len(target_dates),
            error="; ".join(errors) if errors else None,
        )


__all__ = [
    "STATIC_RRG_BOOTSTRAP_UNIVERSE_POLICY",
    "StaticRRGBootstrapBackfillResult",
    "StaticRRGBootstrapBackfillService",
    "StaticRRGBootstrapBackfillStatus",
]
