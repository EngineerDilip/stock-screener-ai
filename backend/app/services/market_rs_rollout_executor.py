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
    MarketRsActivationArtifactPolicy,
    normalize_rollout_market,
)
from app.services.runtime_diagnostics import log_runtime_stage, release_session_memory

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
        static_staging_dir: Path | None,
        artifact_policy: MarketRsActivationArtifactPolicy = (
            MarketRsActivationArtifactPolicy.STATIC_SITE
        ),
    ) -> ActivationValidationReport: ...

    def activate(
        self,
        db: Session,
        *,
        market: str,
        formula_version: str,
        feature_run_id: int,
        validation: ActivationValidationReport,
        static_staging_dir: Path | None,
    ) -> None: ...


@dataclass(frozen=True)
class MarketRsActivationRequest:
    market: str
    through_date: date
    static_staging_dir: Path | None = None
    artifact_policy: MarketRsActivationArtifactPolicy = (
        MarketRsActivationArtifactPolicy.STATIC_SITE
    )


@dataclass(frozen=True)
class MarketRsActivationOutcome:
    backfill: BackfillReport
    market: str
    formula_version: str
    feature_run_id: int
    validation: ActivationValidationReport
    static_staging_dir: str | None

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
        if request.artifact_policy.requires_static_artifacts:
            if request.static_staging_dir is None:
                raise MarketRsActivationExecutionError(
                    "Static staging directory is required for static artifact activation"
                )
            staging_dir: Path | None = validate_static_staging_directory(
                request.static_staging_dir
            )
        else:
            staging_dir = None
        try:
            with log_runtime_stage(
                logger,
                "market_rs.activation.coverage",
                market=market,
                through_date=request.through_date.isoformat(),
                artifact_policy=request.artifact_policy.value,
            ):
                coverage = self.rollout_service.activation_coverage(
                    market=market,
                    through_date=request.through_date,
                )
        except ValueError as exc:
            raise MarketRsActivationExecutionError(str(exc)) from exc
        with log_runtime_stage(
            logger,
            "market_rs.activation.backfill",
            market=market,
            through_date=request.through_date.isoformat(),
            artifact_policy=request.artifact_policy.value,
        ):
            report = self.rollout_service.backfill_activation(
                db,
                coverage=coverage,
            )
        if not report.ok or report.failed_count:
            raise MarketRsActivationExecutionError(
                "One or more required backfill dates failed; repair the reported "
                "dates before activation"
            )
        release_session_memory(db, stage="backfill")

        with log_runtime_stage(
            logger,
            "market_rs.activation.feature_snapshot",
            market=market,
            through_date=request.through_date.isoformat(),
            artifact_policy=request.artifact_policy.value,
        ):
            feature_run_id = self.feature_snapshot_builder(
                market=market,
                through_date=request.through_date,
            )
        release_session_memory(db, stage="feature_snapshot")

        if request.artifact_policy.requires_static_artifacts:
            assert staging_dir is not None
            with log_runtime_stage(
                logger,
                "market_rs.activation.static_export",
                market=market,
                through_date=request.through_date.isoformat(),
                feature_run_id=feature_run_id,
                artifact_policy=request.artifact_policy.value,
            ):
                self.static_exporter(
                    market=market,
                    feature_run_id=feature_run_id,
                    static_staging_dir=staging_dir,
                )
            release_session_memory(db, stage="static_export")

        with log_runtime_stage(
            logger,
            "market_rs.activation.validate",
            market=market,
            through_date=request.through_date.isoformat(),
            feature_run_id=feature_run_id,
            artifact_policy=request.artifact_policy.value,
        ):
            validation = self.rollout_service.validate_activation(
                db,
                coverage=coverage,
                feature_run_id=feature_run_id,
                static_staging_dir=staging_dir,
                artifact_policy=request.artifact_policy,
            )
        if not validation.ok:
            raise MarketRsActivationExecutionError(
                "Activation validation failed: " + "; ".join(validation.errors)
            )
        if validation.artifact_policy is not request.artifact_policy:
            raise MarketRsActivationExecutionError(
                "Validation report artifact policy "
                f"{validation.artifact_policy.value} does not match the requested "
                f"policy {request.artifact_policy.value}"
            )
        release_session_memory(db, stage="validation")
        with log_runtime_stage(
            logger,
            "market_rs.activation.commit",
            market=market,
            through_date=request.through_date.isoformat(),
            feature_run_id=feature_run_id,
            artifact_policy=validation.artifact_policy.value,
        ):
            self.rollout_service.activate(
                db,
                market=market,
                formula_version=BALANCED_RS_FORMULA_VERSION,
                feature_run_id=feature_run_id,
                validation=validation,
                static_staging_dir=staging_dir,
            )
        release_session_memory(db, stage="activation")
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
            static_staging_dir=str(staging_dir) if staging_dir is not None else None,
        )


__all__ = [
    "MarketRsActivationExecutionError",
    "MarketRsActivationExecutor",
    "MarketRsActivationOutcome",
    "MarketRsActivationRequest",
    "MarketRsRollout",
    "validate_static_staging_directory",
]
