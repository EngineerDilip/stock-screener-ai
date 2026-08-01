"""Resolve the publication date shared by implicit bootstrap stages."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from sqlalchemy.orm import Session

from app.config import settings
from app.domain.relative_strength import BALANCED_RS_FORMULA_VERSION
from app.infra.db.repositories.market_rs_repo import (
    MarketRsFormulaNotConfigured,
    MarketRsRunRepository,
)


@dataclass(frozen=True)
class BootstrapPublicationDateResolution:
    market: str
    requested_date: date
    selected_date: date
    formula_version: str | None
    market_rs_run_id: int | None
    lag_days: int | None
    reason_code: str


def _max_lag_days(configured: int | None) -> int:
    if configured is None:
        configured = settings.market_rs_bootstrap_benchmark_max_lag_days
    return max(0, int(configured))


def _resolution(
    *,
    market: str,
    requested_date: date,
    selected_date: date,
    formula_version: str | None,
    reason_code: str,
    market_rs_run_id: int | None = None,
    lag_days: int | None = None,
) -> BootstrapPublicationDateResolution:
    return BootstrapPublicationDateResolution(
        market=market,
        requested_date=requested_date,
        selected_date=selected_date,
        formula_version=formula_version,
        market_rs_run_id=market_rs_run_id,
        lag_days=lag_days,
        reason_code=reason_code,
    )


def resolve_bootstrap_publication_date(
    db: Session,
    *,
    market: str,
    requested_date: date,
    formula_version: str | None = None,
    repository: MarketRsRunRepository | None = None,
    max_lag_days: int | None = None,
) -> BootstrapPublicationDateResolution:
    """Align implicit bootstrap stages to the active balanced RS publication.

    Fresh bootstrap activation may intentionally publish balanced Market RS one
    or two sessions behind the calendar while benchmark data catches up. Later
    bootstrap stages that did not receive an explicit date must use that same
    publication date instead of independently resolving the calendar date.
    """

    normalized_market = str(market).strip().upper()
    repo = repository or MarketRsRunRepository()
    try:
        active_formula = formula_version or repo.active_formula(
            db,
            market=normalized_market,
        )
    except MarketRsFormulaNotConfigured:
        return _resolution(
            market=normalized_market,
            requested_date=requested_date,
            selected_date=requested_date,
            formula_version=None,
            reason_code="active_formula_unconfigured",
        )

    if active_formula != BALANCED_RS_FORMULA_VERSION:
        return _resolution(
            market=normalized_market,
            requested_date=requested_date,
            selected_date=requested_date,
            formula_version=active_formula,
            reason_code="active_formula_not_balanced",
        )

    run = repo.get_latest_completed(
        db,
        market=normalized_market,
        formula_version=active_formula,
        through_date=requested_date,
    )
    if run is None:
        return _resolution(
            market=normalized_market,
            requested_date=requested_date,
            selected_date=requested_date,
            formula_version=active_formula,
            reason_code="balanced_run_unavailable",
        )

    selected_date = run.as_of_date
    lag_days = (requested_date - selected_date).days
    if lag_days < 0 or lag_days > _max_lag_days(max_lag_days):
        return _resolution(
            market=normalized_market,
            requested_date=requested_date,
            selected_date=requested_date,
            formula_version=active_formula,
            market_rs_run_id=getattr(run, "id", None),
            lag_days=lag_days,
            reason_code="balanced_run_lag_exceeds_policy",
        )

    return _resolution(
        market=normalized_market,
        requested_date=requested_date,
        selected_date=selected_date,
        formula_version=active_formula,
        market_rs_run_id=getattr(run, "id", None),
        lag_days=lag_days,
        reason_code=(
            "requested_date_ready" if lag_days == 0 else "balanced_run_selected"
        ),
    )


def resolve_bootstrap_publication_date_with_session(
    session_factory: Callable[[], Session],
    *,
    market: str,
    requested_date: date,
) -> BootstrapPublicationDateResolution:
    db = session_factory()
    try:
        return resolve_bootstrap_publication_date(
            db,
            market=market,
            requested_date=requested_date,
        )
    finally:
        db.close()


__all__ = [
    "BootstrapPublicationDateResolution",
    "resolve_bootstrap_publication_date",
    "resolve_bootstrap_publication_date_with_session",
]
