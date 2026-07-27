"""Celery orchestration for live Group history readiness and repair."""

from __future__ import annotations

from datetime import datetime

from celery import chain

from app.celery_app import celery_app
from app.database import SessionLocal
from app.services.group_history_bootstrap_service import (
    GroupHistoryBootstrapService,
    GroupHistoryBootstrapStatus,
)
from app.services.group_history_readiness_service import (
    GroupHistoryReadinessService,
)
from app.services.group_history_reconciliation import (
    GroupHistoryReconciliationRepository,
    GroupHistoryReconciliationStatus,
)
from app.services.group_history_snapshot_coordinator import (
    build_group_history_snapshot_coordinator,
)
from app.services.group_history_universe import GroupHistoryUniverseResolver
from app.services.group_rankings_cache import bump_group_rankings_epoch
from app.services.market_activity_service import (
    mark_market_activity_completed,
    mark_market_activity_failed,
    mark_market_activity_started,
)
from app.services.point_in_time_universe_service import PointInTimeUniverseService
from app.services.rrg_history_provider import build_rrg_history_provider
from app.services.ui_snapshot_service import safe_publish_groups_bootstrap
from app.tasks.market_queues import normalize_market
from app.tasks.market_queues import (
    data_fetch_queue_for_market,
    market_jobs_queue_for_market,
)
from app.tasks.workload_coordination import serialized_market_workload
from app.wiring.bootstrap import get_group_rank_service, get_market_calendar_service
from app.domain.markets import get_market_catalog
from app.services.runtime_preferences_service import get_runtime_preferences


def _build_group_history_components():
    from app.infra.db.repositories.market_rs_repo import MarketRsRunRepository

    calendar = get_market_calendar_service()
    repository = MarketRsRunRepository()
    group_rank_service = get_group_rank_service()
    universe_resolver = GroupHistoryUniverseResolver(
        point_in_time_universe=PointInTimeUniverseService(
            market_calendar=calendar
        )
    )
    coordinator = build_group_history_snapshot_coordinator(
        universe_resolver=universe_resolver,
        legacy_group_service=group_rank_service,
        calendar_service=calendar,
        market_rs_repository=repository,
    )
    history_provider = build_rrg_history_provider(
        group_rank_service=group_rank_service,
        market_rs_repository=repository,
    )
    readiness = GroupHistoryReadinessService(
        calendar_service=calendar,
        snapshot_reader=coordinator.reader,
        market_rs_repository=repository,
        rrg_history_provider=history_provider,
    )
    bootstrap = GroupHistoryBootstrapService(
        readiness_service=readiness,
        snapshot_coordinator=coordinator,
        universe_resolver=universe_resolver,
    )
    return readiness, bootstrap


def _build_group_history_bootstrap_service() -> GroupHistoryBootstrapService:
    return _build_group_history_components()[1]


def _evaluate_group_history_readiness(db, *, market, through_date):
    return _build_group_history_components()[0].evaluate(
        db,
        market=market,
        through_date=through_date,
    )


@celery_app.task(
    bind=True,
    name="app.tasks.group_history_tasks.ensure_group_history",
    soft_time_limit=7200,
)
@serialized_market_workload("ensure_group_history")
def ensure_group_history(
    self,
    market: str,
    activity_lifecycle: str | None = None,
    strict: bool = True,
    reconciliation_formula_version: str | None = None,
    reconciliation_through_date: str | None = None,
) -> dict:
    market_code = normalize_market(market)
    lifecycle = activity_lifecycle or "bootstrap"
    task_name = getattr(self, "name", "ensure_group_history")
    task_id = getattr(getattr(self, "request", None), "id", None)
    db = SessionLocal()
    failure_recorded = False
    try:
        if reconciliation_formula_version and reconciliation_through_date:
            from datetime import date

            GroupHistoryReconciliationRepository().mark(
                db,
                market=market_code,
                formula_version=reconciliation_formula_version,
                through_date=date.fromisoformat(reconciliation_through_date),
                status=GroupHistoryReconciliationStatus.REPAIRING,
            )
        mark_market_activity_started(
            db,
            market=market_code,
            stage_key="group_history",
            lifecycle=lifecycle,
            task_name=task_name,
            task_id=task_id,
            message="Checking Group ranking history",
        )
        through_date = get_market_calendar_service().last_completed_trading_day(
            market_code
        )
        result = _build_group_history_bootstrap_service().ensure(
            db,
            market=market_code,
            through_date=through_date,
        )
        payload = result.as_dict()
        payload["timestamp"] = datetime.now().isoformat()
        payload["cache_invalidated"] = False
        payload["ui_snapshot_published"] = market_code != "US"

        if result.status is GroupHistoryBootstrapStatus.INCOMPLETE:
            message = f"Group history remains incomplete for {market_code}"
            mark_market_activity_failed(
                db,
                market=market_code,
                stage_key="group_history",
                lifecycle=lifecycle,
                task_name=task_name,
                task_id=task_id,
                message=message,
            )
            failure_recorded = True
            if strict:
                raise RuntimeError(message)
            return payload

        if result.status is GroupHistoryBootstrapStatus.READY:
            bump_group_rankings_epoch(market_code)
            payload["cache_invalidated"] = True
            if market_code == "US":
                published = safe_publish_groups_bootstrap()
                payload["ui_snapshot_published"] = published is not None
                if published is None:
                    message = "US Group bootstrap snapshot publication failed"
                    mark_market_activity_failed(
                        db,
                        market=market_code,
                        stage_key="group_history",
                        lifecycle=lifecycle,
                        task_name=task_name,
                        task_id=task_id,
                        message=message,
                    )
                    failure_recorded = True
                    payload["status"] = "incomplete"
                    if strict:
                        raise RuntimeError(message)
                    return payload

        mark_market_activity_completed(
            db,
            market=market_code,
            stage_key="group_history",
            lifecycle=lifecycle,
            task_name=task_name,
            task_id=task_id,
            message="Group ranking history ready",
        )
        return payload
    except Exception as exc:
        if not failure_recorded:
            mark_market_activity_failed(
                db,
                market=market_code,
                stage_key="group_history",
                lifecycle=lifecycle,
                task_name=task_name,
                task_id=task_id,
                message=str(exc),
            )
        raise
    finally:
        db.close()


def _dispatch_group_history_reconciliation(
    *,
    market: str,
    formula_version: str,
    through_date,
) -> str:
    from app.tasks.cache_tasks import smart_refresh_cache

    through_iso = through_date.isoformat()
    workflow = chain(
        smart_refresh_cache.si(
            mode="bootstrap",
            market=market,
            activity_lifecycle="group_history_reconciliation",
            ensure_group_history=True,
        ).set(queue=data_fetch_queue_for_market(market)),
        ensure_group_history.si(
            market=market,
            activity_lifecycle="group_history_reconciliation",
            strict=False,
            reconciliation_formula_version=formula_version,
            reconciliation_through_date=through_iso,
        ).set(queue=market_jobs_queue_for_market(market)),
        complete_group_history_reconciliation.si(
            market=market,
            formula_version=formula_version,
            through_date=through_iso,
        ).set(queue="celery"),
    )
    errback = fail_group_history_reconciliation.s(
        market=market,
        formula_version=formula_version,
        through_date=through_iso,
    ).set(queue="celery")
    return workflow.apply_async(link_error=errback).id


def queue_group_history_reconciliation() -> dict[str, str]:
    """Queue nonblocking repairs for enabled markets with incomplete history."""
    db = SessionLocal()
    try:
        preferences = get_runtime_preferences(db)
        if preferences.bootstrap_state == "running":
            return {
                market: "bootstrap_running"
                for market in preferences.enabled_markets
            }

        repository = GroupHistoryReconciliationRepository()
        calendar = get_market_calendar_service()
        outcomes: dict[str, str] = {}
        for market in preferences.enabled_markets:
            market_code = normalize_market(market)
            if not (
                get_market_catalog()
                .get(market_code)
                .capabilities.group_rankings
            ):
                outcomes[market_code] = "skipped"
                continue
            through_date = calendar.last_completed_trading_day(market_code)
            try:
                readiness = _evaluate_group_history_readiness(
                    db,
                    market=market_code,
                    through_date=through_date,
                )
            except Exception as exc:
                outcomes[market_code] = f"readiness_failed:{type(exc).__name__}"
                continue
            formula_version = readiness.formula_version
            if formula_version is None:
                outcomes[market_code] = "formula_unavailable"
                continue
            if readiness.ready:
                repository.mark(
                    db,
                    market=market_code,
                    formula_version=formula_version,
                    through_date=through_date,
                    status=GroupHistoryReconciliationStatus.READY,
                    counts=readiness.as_dict(),
                )
                outcomes[market_code] = "ready"
                continue
            if not repository.reserve(
                db,
                market=market_code,
                formula_version=formula_version,
                through_date=through_date,
            ):
                outcomes[market_code] = "already_queued"
                continue
            try:
                _dispatch_group_history_reconciliation(
                    market=market_code,
                    formula_version=formula_version,
                    through_date=through_date,
                )
            except Exception as exc:
                repository.mark(
                    db,
                    market=market_code,
                    formula_version=formula_version,
                    through_date=through_date,
                    status=GroupHistoryReconciliationStatus.INCOMPLETE,
                    counts=readiness.as_dict(),
                    error=str(exc),
                )
                outcomes[market_code] = "dispatch_failed"
                continue
            outcomes[market_code] = "queued"
        return outcomes
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.group_history_tasks.complete_group_history_reconciliation",
    queue="celery",
)
def complete_group_history_reconciliation(
    *,
    market: str,
    formula_version: str,
    through_date: str,
) -> dict:
    from datetime import date

    market_code = normalize_market(market)
    through = date.fromisoformat(through_date)
    db = SessionLocal()
    try:
        readiness = _evaluate_group_history_readiness(
            db,
            market=market_code,
            through_date=through,
        )
        publication_ready = market_code != "US"
        if readiness.ready and market_code == "US":
            publication_ready = safe_publish_groups_bootstrap() is not None
        ready = readiness.ready and publication_ready
        status = (
            GroupHistoryReconciliationStatus.READY
            if ready
            else GroupHistoryReconciliationStatus.INCOMPLETE
        )
        error = None
        if readiness.ready and not publication_ready:
            error = "US Group bootstrap snapshot publication failed"
        elif not readiness.ready:
            error = "Group history remains incomplete after repair"
        GroupHistoryReconciliationRepository().mark(
            db,
            market=market_code,
            formula_version=formula_version,
            through_date=through,
            status=status,
            counts=readiness.as_dict(),
            error=error,
        )
        return {
            "status": status.value,
            "market": market_code,
            "formula_version": formula_version,
            "through_date": through_date,
            "publication_ready": publication_ready,
            "readiness": readiness.as_dict(),
            "error": error,
        }
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.group_history_tasks.fail_group_history_reconciliation",
    queue="celery",
)
def fail_group_history_reconciliation(
    *_celery_errback_args,
    market: str,
    formula_version: str,
    through_date: str,
) -> dict:
    from datetime import date

    market_code = normalize_market(market)
    through = date.fromisoformat(through_date)
    db = SessionLocal()
    try:
        GroupHistoryReconciliationRepository().mark(
            db,
            market=market_code,
            formula_version=formula_version,
            through_date=through,
            status=GroupHistoryReconciliationStatus.FAILED,
            error="Group history reconciliation task failed",
        )
        return {
            "status": "failed",
            "market": market_code,
            "formula_version": formula_version,
            "through_date": through_date,
        }
    finally:
        db.close()


__all__ = [
    "complete_group_history_reconciliation",
    "ensure_group_history",
    "fail_group_history_reconciliation",
    "queue_group_history_reconciliation",
]
