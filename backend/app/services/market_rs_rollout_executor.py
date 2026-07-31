"""Guarded balanced Market RS activation orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session

from app.config import settings
from app.domain.relative_strength import (
    BALANCED_RS_FORMULA_VERSION,
    GroupSnapshotIdentity,
)
from app.services.market_rs_activation_coverage import MarketRsActivationCoverage
from app.services.market_rs_rollout_contracts import (
    ActivationValidationReport,
    BackfillReport,
    normalize_rollout_market,
)

logger = logging.getLogger(__name__)


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
    def __call__(self, identity: GroupSnapshotIdentity) -> None: ...


class MarketRsRollout(Protocol):
    def activation_coverage(
        self,
        *,
        market: str,
        through_date: date,
    ) -> MarketRsActivationCoverage: ...

    def backfill_activation(
        self,
        db: Session,
        *,
        coverage: MarketRsActivationCoverage,
    ) -> BackfillReport: ...

    def validate_activation(
        self,
        db: Session,
        *,
        coverage: MarketRsActivationCoverage,
        feature_run_id: int,
        static_staging_dir: Path,
    ) -> ActivationValidationReport: ...

    def activate(
        self,
        db: Session,
        *,
        market: str,
        formula_version: str,
        feature_run_id: int,
        validation: ActivationValidationReport,
        static_staging_dir: Path,
    ) -> None: ...


@dataclass(frozen=True)
class MarketRsActivationRequest:
    market: str
    through_date: date
    static_staging_dir: Path


@dataclass(frozen=True)
class MarketRsActivationOutcome:
    backfill: BackfillReport
    market: str
    formula_version: str
    feature_run_id: int
    validation: ActivationValidationReport
    static_staging_dir: str

    def to_dict(self) -> dict[str, object]:
        return {
            "backfill": self.backfill.to_dict(),
            "activated": True,
            "market": self.market,
            "formula_version": self.formula_version,
            "feature_run_id": self.feature_run_id,
            "validation": self.validation.to_dict(),
            "static_staging_dir": self.static_staging_dir,
        }


def validate_static_staging_directory(path: Path) -> Path:
    if not path.is_absolute():
        raise MarketRsActivationExecutionError(
            "Static staging directory must be an absolute path"
        )
    resolved = path.resolve()
    serving_dir = Path(settings.static_export_output_dir).expanduser().resolve()
    if resolved.is_relative_to(serving_dir) or serving_dir.is_relative_to(resolved):
        raise MarketRsActivationExecutionError(
            "Static staging directory must not overlap the configured serving directory"
        )
    if resolved.exists() and any(resolved.iterdir()):
        raise MarketRsActivationExecutionError("Static staging directory must be empty")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


class MarketRsActivationExecutor:
    def __init__(
        self,
        *,
        rollout_service: MarketRsRollout,
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
        try:
            market = normalize_rollout_market(request.market)
        except ValueError as exc:
            raise MarketRsActivationExecutionError(str(exc)) from exc
        staging_dir = validate_static_staging_directory(request.static_staging_dir)
        coverage = self.rollout_service.activation_coverage(
            market=market,
            through_date=request.through_date,
        )
        report = self.rollout_service.backfill_activation(
            db,
            coverage=coverage,
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
            coverage=coverage,
            feature_run_id=feature_run_id,
            static_staging_dir=staging_dir,
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
        identity = GroupSnapshotIdentity(
            market=market,
            as_of_date=request.through_date,
            formula_version=BALANCED_RS_FORMULA_VERSION,
        )
        try:
            self.live_group_publisher(identity)
        except Exception:
            logger.exception(
                "Best-effort live Group snapshot publication failed after activation",
                extra={
                    "market": identity.market,
                    "as_of_date": identity.as_of_date.isoformat(),
                    "formula_version": identity.formula_version,
                },
            )
        return MarketRsActivationOutcome(
            backfill=report,
            market=market,
            formula_version=BALANCED_RS_FORMULA_VERSION,
            feature_run_id=feature_run_id,
            validation=validation,
            static_staging_dir=str(staging_dir),
        )


__all__ = [
    "MarketRsActivationExecutionError",
    "MarketRsActivationExecutor",
    "MarketRsActivationOutcome",
    "MarketRsActivationRequest",
    "MarketRsRollout",
    "validate_static_staging_directory",
]
