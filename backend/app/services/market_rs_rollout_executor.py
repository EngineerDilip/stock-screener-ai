"""Guarded balanced Market RS activation orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.config import settings
from app.domain.relative_strength import BALANCED_RS_FORMULA_VERSION
from app.services.market_rs_activation_coverage import (
    market_rs_activation_start_date,
)
from app.services.market_rs_rollout_contracts import normalize_rollout_market
from app.services.market_rs_rollout_service import MarketRsRolloutService


class MarketRsActivationExecutionError(RuntimeError):
    pass


class FeatureSnapshotBuilder(Protocol):
    def __call__(self, *, market: str, through_date: date) -> int: ...


class StaticExporter(Protocol):
    def __call__(
        self,
        *,
        market: str,
        feature_run_id: int,
        static_staging_dir: Path,
    ) -> None: ...


class LiveGroupPublisher(Protocol):
    def __call__(self, market: str) -> None: ...


@dataclass(frozen=True)
class MarketRsActivationRequest:
    market: str
    through_date: date
    static_staging_dir: Path


@dataclass(frozen=True)
class MarketRsActivationOutcome:
    backfill: dict[str, Any]
    market: str
    formula_version: str
    feature_run_id: int
    validation: dict[str, Any]
    static_staging_dir: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "backfill": dict(self.backfill),
            "activated": True,
            "market": self.market,
            "formula_version": self.formula_version,
            "feature_run_id": self.feature_run_id,
            "validation": dict(self.validation),
            "static_staging_dir": self.static_staging_dir,
        }


def validate_static_staging_directory(path: Path) -> Path:
    if not path.is_absolute():
        raise MarketRsActivationExecutionError(
            "--static-staging-dir must be an absolute path"
        )
    resolved = path.resolve()
    serving_dir = Path(settings.static_export_output_dir).expanduser().resolve()
    if resolved == serving_dir:
        raise MarketRsActivationExecutionError(
            "--static-staging-dir must not be the configured serving directory"
        )
    if resolved.exists() and any(resolved.iterdir()):
        raise MarketRsActivationExecutionError("--static-staging-dir must be empty")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


class MarketRsActivationExecutor:
    def __init__(
        self,
        *,
        rollout_service: MarketRsRolloutService,
        feature_snapshot_builder: FeatureSnapshotBuilder,
        static_exporter: StaticExporter,
        live_group_publisher: LiveGroupPublisher,
    ) -> None:
        self.rollout_service = rollout_service
        self.feature_snapshot_builder = feature_snapshot_builder
        self.static_exporter = static_exporter
        self.live_group_publisher = live_group_publisher

    def execute(
        self,
        db: Session,
        *,
        request: MarketRsActivationRequest,
    ) -> MarketRsActivationOutcome:
        market = normalize_rollout_market(request.market)
        staging_dir = validate_static_staging_directory(request.static_staging_dir)
        coverage_start_date = market_rs_activation_start_date(request.through_date)
        report = self.rollout_service.backfill(
            db,
            market=market,
            through_date=request.through_date,
            coverage_start_date=coverage_start_date,
        )
        if not report.ok or report.failed_count:
            raise MarketRsActivationExecutionError(
                "One or more required backfill dates failed; repair the reported "
                "dates before activation"
            )

        feature_run_id = self.feature_snapshot_builder(
            market=market,
            through_date=request.through_date,
        )
        self.static_exporter(
            market=market,
            feature_run_id=feature_run_id,
            static_staging_dir=staging_dir,
        )
        db.expire_all()
        validation = self.rollout_service.validate_activation(
            db,
            market=market,
            through_date=request.through_date,
            feature_run_id=feature_run_id,
            static_staging_dir=staging_dir,
            coverage_start_date=coverage_start_date,
        )
        if not validation.ok:
            raise MarketRsActivationExecutionError(
                "Activation validation failed: " + "; ".join(validation.errors)
            )
        self.rollout_service.activate(
            db,
            market=market,
            formula_version=BALANCED_RS_FORMULA_VERSION,
            feature_run_id=feature_run_id,
            validation=validation,
            static_staging_dir=staging_dir,
        )
        self.live_group_publisher(market)
        return MarketRsActivationOutcome(
            backfill=report.to_dict(),
            market=market,
            formula_version=BALANCED_RS_FORMULA_VERSION,
            feature_run_id=feature_run_id,
            validation=validation.to_dict(),
            static_staging_dir=str(staging_dir),
        )


__all__ = [
    "MarketRsActivationExecutionError",
    "MarketRsActivationExecutor",
    "MarketRsActivationOutcome",
    "MarketRsActivationRequest",
    "validate_static_staging_directory",
]
