"""Shared execution boundary for guarded balanced Market RS rollout."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.config import settings
from app.domain.relative_strength import BALANCED_RS_FORMULA_VERSION
from app.services.market_rs_rollout_contracts import normalize_rollout_market
from app.services.market_rs_rollout_service import MarketRsRolloutService


class MarketRsRolloutExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class MarketRsRolloutRequest:
    market: str
    through_date: date
    start_date: date | None = None
    activate: bool = False
    static_staging_dir: Path | None = None


@dataclass(frozen=True)
class MarketRsRolloutOutcome:
    backfill: dict[str, Any]
    activated: bool
    market: str
    formula_version: str
    feature_run_id: int | None = None
    validation: dict[str, Any] | None = None
    static_staging_dir: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "backfill": dict(self.backfill),
            "activated": self.activated,
        }
        if self.activated:
            payload.update(
                market=self.market,
                formula_version=self.formula_version,
                feature_run_id=self.feature_run_id,
                validation=dict(self.validation or {}),
                static_staging_dir=self.static_staging_dir,
            )
        return payload


def validate_static_staging_directory(path: Path | None) -> Path:
    if path is None:
        raise MarketRsRolloutExecutionError(
            "--activate requires --static-staging-dir"
        )
    if not path.is_absolute():
        raise MarketRsRolloutExecutionError(
            "--static-staging-dir must be an absolute path"
        )
    resolved = path.resolve()
    serving_dir = Path(settings.static_export_output_dir).expanduser().resolve()
    if resolved == serving_dir:
        raise MarketRsRolloutExecutionError(
            "--static-staging-dir must not be the configured serving directory"
        )
    if resolved.exists() and any(resolved.iterdir()):
        raise MarketRsRolloutExecutionError(
            "--static-staging-dir must be empty"
        )
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def build_balanced_feature_snapshot(*, market: str, through_date: date) -> int:
    from app.interfaces.tasks.feature_store_tasks import build_daily_snapshot

    result = build_daily_snapshot.run(
        market=market,
        as_of_date_str=through_date.isoformat(),
        universe_name=f"market:{market}",
        publish_pointer_key=f"rollout_rs:{BALANCED_RS_FORMULA_VERSION}:{market}",
        static_daily_mode=True,
        ignore_runtime_market_gate=True,
        skip_if_published=False,
        rs_formula_version_override=BALANCED_RS_FORMULA_VERSION,
    )
    if not isinstance(result, dict) or result.get("status") != "published":
        raise MarketRsRolloutExecutionError(
            f"Balanced Feature snapshot did not publish: {result}"
        )
    run_id = result.get("run_id")
    if run_id is None:
        raise MarketRsRolloutExecutionError(
            "Balanced Feature snapshot returned no run ID"
        )
    return int(run_id)


def export_static_v3(
    *,
    market: str,
    feature_run_id: int,
    static_staging_dir: Path,
) -> None:
    from app.database import SessionLocal
    from app.services.static_site_export_service import StaticSiteExportService

    StaticSiteExportService(SessionLocal).export(
        static_staging_dir,
        clean=True,
        markets=(market,),
        rs_formula_version_overrides={market: BALANCED_RS_FORMULA_VERSION},
        feature_run_ids_by_market={market: feature_run_id},
    )


def publish_live_groups(market: str) -> None:
    from app.services.ui_snapshot_service import safe_publish_groups_bootstrap

    if market == "US":
        safe_publish_groups_bootstrap()


class MarketRsRolloutExecutor:
    def __init__(
        self,
        *,
        rollout_service: MarketRsRolloutService,
        feature_snapshot_builder: Callable[..., int],
        static_exporter: Callable[..., None],
        live_group_publisher: Callable[[str], None],
    ) -> None:
        self.rollout_service = rollout_service
        self.feature_snapshot_builder = feature_snapshot_builder
        self.static_exporter = static_exporter
        self.live_group_publisher = live_group_publisher

    def execute(
        self,
        db: Session,
        *,
        request: MarketRsRolloutRequest,
    ) -> MarketRsRolloutOutcome:
        market = normalize_rollout_market(request.market)
        staging_dir = (
            validate_static_staging_directory(request.static_staging_dir)
            if request.activate
            else None
        )
        report = self.rollout_service.backfill(
            db,
            market=market,
            through_date=request.through_date,
            start_date=request.start_date,
        )
        if not request.activate:
            return MarketRsRolloutOutcome(
                backfill=report.to_dict(),
                activated=False,
                market=market,
                formula_version=BALANCED_RS_FORMULA_VERSION,
            )
        if not report.ok or report.failed_count:
            raise MarketRsRolloutExecutionError(
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
        )
        if not validation.ok:
            raise MarketRsRolloutExecutionError(
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
        return MarketRsRolloutOutcome(
            backfill=report.to_dict(),
            activated=True,
            market=market,
            formula_version=BALANCED_RS_FORMULA_VERSION,
            feature_run_id=feature_run_id,
            validation=validation.to_dict(),
            static_staging_dir=str(staging_dir),
        )
