"""Read-only readiness checks for live Group ranking history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.analysis.rrg_weekly import bucket_rrg_weekly
from app.domain.markets import get_market_catalog
from app.services.group_rank_history_policy import (
    CALENDAR_DAY_GROUP_RANK_CHANGE_WINDOWS,
    CALENDAR_DAY_GROUP_RANK_LOOKUP_TOLERANCE_DAYS,
    DEFAULT_CALENDAR_DAY_GROUP_RANK_HISTORY_LOOKBACK_DAYS,
)
from app.services.group_rank_snapshot_reader import (
    GroupRankSnapshotReader,
    GroupSnapshotWindowIntegrityError,
)
from app.services.group_history_reconciliation import GroupHistoryTarget
from app.services.market_calendar_service import MarketCalendarService
from app.services.rrg_service import (
    DEFAULT_LOOKBACK_DAYS,
    MIN_TAIL_WEEKS,
    compute_group_rrg,
)


@dataclass(frozen=True)
class GroupHistoryReadinessReport:
    market: str
    through_date: date
    formula_version: str | None
    supported: bool
    desired_dates: tuple[date, ...] = ()
    valid_dates: tuple[date, ...] = ()
    missing_dates: tuple[date, ...] = ()
    invalid_dates: tuple[date, ...] = ()
    invalid_reasons: tuple[tuple[date, str], ...] = ()
    rank_change_ready: dict[str, bool] | None = None
    rrg_required: bool = False
    rrg_usable_weeks: int = 0
    rrg_plottable_series: int = 0
    ready: bool = False
    reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "through_date": self.through_date.isoformat(),
            "formula_version": self.formula_version,
            "supported": self.supported,
            "desired_dates": [item.isoformat() for item in self.desired_dates],
            "valid_dates": [item.isoformat() for item in self.valid_dates],
            "missing_dates": [item.isoformat() for item in self.missing_dates],
            "invalid_dates": [item.isoformat() for item in self.invalid_dates],
            "invalid_reasons": {
                item.isoformat(): reason for item, reason in self.invalid_reasons
            },
            "rank_change_ready": dict(self.rank_change_ready or {}),
            "rrg_required": self.rrg_required,
            "rrg_usable_weeks": self.rrg_usable_weeks,
            "rrg_plottable_series": self.rrg_plottable_series,
            "ready": self.ready,
            "reason": self.reason,
        }


class GroupHistoryReadinessService:
    """Verify formula-scoped Group history without writing to the database."""

    def __init__(
        self,
        *,
        calendar_service: MarketCalendarService | None = None,
        snapshot_reader: GroupRankSnapshotReader | None = None,
        market_rs_repository=None,
        rrg_history_provider=None,
    ) -> None:
        if market_rs_repository is None:
            from app.infra.db.repositories.market_rs_repo import MarketRsRunRepository

            market_rs_repository = MarketRsRunRepository()
        if rrg_history_provider is None:
            raise ValueError("RRG history provider is required")
        self._calendar_service = calendar_service or MarketCalendarService()
        self._snapshot_reader = snapshot_reader or GroupRankSnapshotReader()
        self._market_rs_repository = market_rs_repository
        self._rrg_history_provider = rrg_history_provider

    def evaluate(
        self,
        db: Session,
        *,
        target: GroupHistoryTarget | None = None,
        market: str | None = None,
        through_date: date | None = None,
    ) -> GroupHistoryReadinessReport:
        normalized_market = (
            target.market
            if target is not None
            else str(market or "").strip().upper()
        )
        through = (
            target.through_date
            if target is not None
            else through_date
            or self._calendar_service.last_completed_trading_day(normalized_market)
        )
        catalog_entry = get_market_catalog().get(normalized_market)
        if not catalog_entry.capabilities.group_rankings:
            return GroupHistoryReadinessReport(
                market=normalized_market,
                through_date=through,
                formula_version=None,
                supported=False,
                ready=True,
                reason="group_rankings_not_supported",
            )

        formula_version = (
            target.formula_version
            if target is not None
            else self._market_rs_repository.active_formula(
                db,
                market=normalized_market,
            )
        )
        start_date = through - timedelta(
            days=DEFAULT_CALENDAR_DAY_GROUP_RANK_HISTORY_LOOKBACK_DAYS
        )
        desired_dates = tuple(
            sorted(
                dict.fromkeys(
                    self._calendar_service.trading_days(
                        normalized_market,
                        start_date,
                        through,
                    )
                )
            )
        )
        invalid_by_date: dict[date, str] = {}
        try:
            snapshots = self._snapshot_reader.load_window(
                db,
                market=normalized_market,
                formula_version=formula_version,
                dates=desired_dates,
                include_top_symbol_names=False,
            )
        except GroupSnapshotWindowIntegrityError as exc:
            snapshots = exc.snapshots
            invalid_by_date = exc.errors
        valid = [item for item in desired_dates if item in snapshots]
        invalid = [item for item in desired_dates if item in invalid_by_date]
        missing = [
            item
            for item in desired_dates
            if item not in snapshots and item not in invalid_by_date
        ]
        invalid_reasons = [
            (item, invalid_by_date[item]) for item in invalid
        ]

        rank_change_ready = self._rank_change_readiness(
            through_date=through,
            valid_dates=valid,
        )
        rrg_required = bool(catalog_entry.capabilities.rrg_scopes)
        rrg_usable_weeks = 0
        rrg_plottable_series = 0
        if rrg_required:
            rrg_usable_weeks, rrg_plottable_series = self._rrg_readiness(
                db,
                market=normalized_market,
                through_date=through,
            )

        ready = (
            bool(desired_dates)
            and not missing
            and not invalid
            and all(rank_change_ready.values())
            and (
                not rrg_required
                or (
                    rrg_usable_weeks >= MIN_TAIL_WEEKS
                    and rrg_plottable_series > 0
                )
            )
        )
        return GroupHistoryReadinessReport(
            market=normalized_market,
            through_date=through,
            formula_version=formula_version,
            supported=True,
            desired_dates=desired_dates,
            valid_dates=tuple(valid),
            missing_dates=tuple(missing),
            invalid_dates=tuple(invalid),
            invalid_reasons=tuple(invalid_reasons),
            rank_change_ready=rank_change_ready,
            rrg_required=rrg_required,
            rrg_usable_weeks=rrg_usable_weeks,
            rrg_plottable_series=rrg_plottable_series,
            ready=ready,
        )

    @staticmethod
    def _rank_change_readiness(
        *,
        through_date: date,
        valid_dates: list[date],
    ) -> dict[str, bool]:
        readiness: dict[str, bool] = {}
        for period, days in CALENDAR_DAY_GROUP_RANK_CHANGE_WINDOWS.items():
            target = through_date - timedelta(days=days)
            readiness[period] = any(
                abs((candidate - target).days)
                <= CALENDAR_DAY_GROUP_RANK_LOOKUP_TOLERANCE_DAYS
                for candidate in valid_dates
                if candidate < through_date
            )
        return readiness

    def _rrg_readiness(
        self,
        db: Session,
        *,
        market: str,
        through_date: date,
    ) -> tuple[int, int]:
        try:
            _latest, _meta, series = (
                self._rrg_history_provider.get_all_groups_history(
                    db,
                    market=market,
                    days=DEFAULT_LOOKBACK_DAYS,
                    as_of_date=through_date,
                )
            )
        except (GroupSnapshotIntegrityError, LookupError, ValueError):
            return 0, 0
        usable_weeks = 0
        plottable = 0
        for observations in series.values():
            daily = [(item[0], float(item[1])) for item in observations]
            usable_weeks = max(usable_weeks, len(bucket_rrg_weekly(daily)))
            if compute_group_rrg(daily) is not None:
                plottable += 1
        return usable_weeks, plottable


__all__ = [
    "GroupHistoryReadinessReport",
    "GroupHistoryReadinessService",
]
