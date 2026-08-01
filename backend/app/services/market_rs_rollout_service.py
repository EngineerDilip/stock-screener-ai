"""Public facade for canonical Market RS backfill, validation, and activation."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.domain.relative_strength import HORIZON_SESSIONS
from app.infra.db.repositories.feature_run_repo import SqlFeatureRunRepository
from app.infra.db.repositories.market_rs_repo import MarketRsRunRepository
from app.models.stock import StockPrice
from app.services.benchmark_registry_service import benchmark_registry
from app.services.canonical_group_ranking_service import CanonicalGroupRankingService
from app.services.market_calendar_service import MarketCalendarService
from app.services.market_rs_activation_coverage import MarketRsActivationCoverage
from app.services.market_rs_activation_validator import (
    MarketRsActivationValidator,
)
from app.services.market_rs_activator import MarketRsActivator
from app.services.market_rs_backfill_service import MarketRsBackfillService
from app.services.market_rs_inputs import MarketRsInputLoader
from app.services.market_rs_rollout_contracts import (
    ActivationValidationReport,
    BackfillDateResult,
    BackfillReport,
    MarketRsActivationRejected,
    MarketRsBootstrapThroughDateResolution,
    normalize_rollout_market,
)
from app.services.market_rs_snapshot_service import MarketRsSnapshotService

FeatureRunRepositoryFactory = Callable[[Session], SqlFeatureRunRepository]


class MarketRsRolloutService:
    """Coordinate explicit rollout collaborators behind the stable public API."""

    def __init__(
        self,
        *,
        calendar_service: MarketCalendarService,
        input_loader: MarketRsInputLoader,
        market_rs_snapshot_service: MarketRsSnapshotService,
        market_rs_repository: MarketRsRunRepository,
        canonical_group_service: CanonicalGroupRankingService,
        feature_run_repository_factory: FeatureRunRepositoryFactory | None = None,
    ) -> None:
        feature_factory = feature_run_repository_factory or SqlFeatureRunRepository
        self.calendar_service = calendar_service
        self.backfill_service = MarketRsBackfillService(
            calendar_service=calendar_service,
            input_loader=input_loader,
            snapshot_service=market_rs_snapshot_service,
            repository=market_rs_repository,
            group_service=canonical_group_service,
        )
        self.validator = MarketRsActivationValidator(
            backfill_service=self.backfill_service,
            repository=market_rs_repository,
            feature_run_repository_factory=feature_factory,
        )
        self.activator = MarketRsActivator(
            repository=market_rs_repository,
            feature_run_repository_factory=feature_factory,
            validator=self.validator,
        )

    def earliest_backfillable_date(
        self,
        db: Session,
        *,
        market: str,
        through_date: date,
        probe_start_date: date | None = None,
    ) -> date | None:
        return self.backfill_service.earliest_backfillable_date(
            db,
            market=market,
            through_date=through_date,
            probe_start_date=probe_start_date,
        )

    def candidate_dates(
        self,
        db: Session,
        *,
        market: str,
        through_date: date,
        first_valid_date: date | None = None,
    ) -> tuple[date, ...]:
        return self.backfill_service.candidate_dates(
            db,
            market=market,
            through_date=through_date,
            first_valid_date=first_valid_date,
        )

    def backfill(
        self,
        db: Session,
        *,
        market: str,
        through_date: date,
        start_date: date | None = None,
    ) -> BackfillReport:
        return self.backfill_service.backfill(
            db,
            market=market,
            through_date=through_date,
            start_date=start_date,
        )

    def backfill_activation(
        self,
        db: Session,
        *,
        coverage: MarketRsActivationCoverage,
    ) -> BackfillReport:
        return self.backfill_service.backfill_activation(db, coverage=coverage)

    def activation_coverage(
        self,
        *,
        market: str,
        through_date: date,
    ) -> MarketRsActivationCoverage:
        return MarketRsActivationCoverage.build(
            calendar_service=self.calendar_service,
            market=market,
            through_date=through_date,
        )

    def resolve_bootstrap_through_date(
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
        benchmark_through_date = (
            self._latest_benchmark_ready_date(
                db,
                market=normalized,
                requested_through_date=requested_through_date,
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
        candidates: tuple[str, ...],
    ) -> date | None:
        current_dates = self._usable_benchmark_current_dates(
            db,
            candidates=candidates,
            requested_through_date=requested_through_date,
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
    ) -> tuple[date, ...]:
        rows = (
            db.query(StockPrice.date, StockPrice.adj_close)
            .filter(
                StockPrice.symbol.in_(candidates),
                StockPrice.date <= requested_through_date,
            )
            .order_by(StockPrice.date.desc())
            .all()
        )
        usable_dates: list[date] = []
        seen: set[date] = set()
        for row in rows:
            if row.date in seen or not MarketRsInputLoader._valid_price(row.adj_close):
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
            if not MarketRsInputLoader._valid_price(row.adj_close):
                continue
            valid_dates_by_symbol.setdefault(row.symbol, set()).add(row.date)
        return any(
            anchor_dates.issubset(valid_dates)
            for valid_dates in valid_dates_by_symbol.values()
        )

    def validate_activation(
        self,
        db: Session,
        *,
        coverage: MarketRsActivationCoverage,
        feature_run_id: int,
        static_staging_dir: Path,
    ) -> ActivationValidationReport:
        return self.validator.validate(
            db,
            coverage=coverage,
            feature_run_id=feature_run_id,
            static_staging_dir=static_staging_dir,
        )

    def revalidate_static(
        self,
        db: Session,
        *,
        market: str,
        through_date: date,
        feature_run_id: int,
        static_staging_dir: Path,
    ) -> tuple[str, ...]:
        return self.validator.revalidate_static(
            db,
            market=market,
            through_date=through_date,
            feature_run_id=feature_run_id,
            static_staging_dir=static_staging_dir,
        )

    def activate(
        self,
        db: Session,
        *,
        market: str,
        formula_version: str,
        feature_run_id: int,
        validation: ActivationValidationReport,
        static_staging_dir: Path,
    ) -> None:
        self.activator.activate(
            db,
            market=market,
            formula_version=formula_version,
            feature_run_id=feature_run_id,
            validation=validation,
            static_staging_dir=static_staging_dir,
        )


__all__ = [
    "ActivationValidationReport",
    "BackfillDateResult",
    "BackfillReport",
    "MarketRsActivationRejected",
    "MarketRsBootstrapThroughDateResolution",
    "MarketRsRolloutService",
]
