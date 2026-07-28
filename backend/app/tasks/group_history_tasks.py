"""Celery orchestration for live Group history readiness and repair."""

from __future__ import annotations

from datetime import date

from celery import chain

from app.celery_app import celery_app
from app.database import SessionLocal
from app.domain.markets import get_market_catalog
from app.services.group_history_bootstrap_service import GroupHistoryBootstrapService
from app.services.group_history_execution_service import (
    GroupHistoryCompletionPolicy,
    GroupHistoryExecutionService,
)
from app.services.group_history_readiness_service import GroupHistoryReadinessService
from app.services.group_history_reconciliation import (
    GroupHistoryReconciliationRepository,
    GroupHistoryReconciliationStatus,
    GroupHistoryTarget,
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
from app.services.runtime_preferences_service import get_runtime_preferences
from app.services.ui_snapshot_service import safe_publish_groups_bootstrap
from app.tasks.market_queues import (
    data_fetch_queue_for_market,
    market_jobs_queue_for_market,
    normalize_market,
)
from app.tasks.workload_coordination import serialized_market_workload
from app.wiring.bootstrap import get_group_rank_service, get_market_calendar_service


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


def _build_group_history_execution_service() -> GroupHistoryExecutionService:
    return GroupHistoryExecutionService(
        bootstrap_service=_build_group_history_bootstrap_service(),
        reconciliation_repository=GroupHistoryReconciliationRepository(),
        bump_epoch=bump_group_rankings_epoch,
        publish_snapshot=safe_publish_groups_bootstrap,
        mark_started=mark_market_activity_started,
        mark_completed=mark_market_activity_completed,
        mark_failed=mark_market_activity_failed,
    )


def _resolve_current_group_history_target(db, *, market: str) -> GroupHistoryTarget:
    from app.infra.db.repositories.market_rs_repo import MarketRsRunRepository

    market_code = normalize_market(market)
    formula_version = MarketRsRunRepository().active_formula(
        db,
        market=market_code,
    )
    if not formula_version:
        raise RuntimeError(f"Active RS formula unavailable for {market_code}")
    through_date = get_market_calendar_service().last_completed_trading_day(
        market_code
    )
    return GroupHistoryTarget(
        market=market_code,
        formula_version=formula_version,
        through_date=through_date,
    )


def _evaluate_group_history_readiness(
    db,
    *,
    target: GroupHistoryTarget,
):
    return _build_group_history_components()[0].evaluate(db, target=target)


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
) -> dict:
    market_code = normalize_market(market)
    task_name = getattr(self, "name", "ensure_group_history")
    task_id = getattr(getattr(self, "request", None), "id", None)
    db = SessionLocal()
    try:
        target = _resolve_current_group_history_target(db, market=market_code)
        return _build_group_history_execution_service().execute(
            db,
            target=target,
            completion_policy=GroupHistoryCompletionPolicy.BOOTSTRAP,
            task_name=task_name,
            task_id=task_id,
            raise_on_incomplete=strict,
            activity_lifecycle=activity_lifecycle,
        )
    finally:
        db.close()


@celery_app.task(
    bind=True,
    name="app.tasks.group_history_tasks.repair_group_history_reconciliation",
    soft_time_limit=7200,
)
@serialized_market_workload("repair_group_history_reconciliation")
def repair_group_history_reconciliation(
    self,
    *,
    market: str,
    formula_version: str,
    through_date: str,
) -> dict:
    target = GroupHistoryTarget(
        market=market,
        formula_version=formula_version,
        through_date=date.fromisoformat(through_date),
    )
    task_name = getattr(self, "name", "repair_group_history_reconciliation")
    task_id = getattr(getattr(self, "request", None), "id", None)
    db = SessionLocal()
    try:
        return _build_group_history_execution_service().execute(
            db,
            target=target,
            completion_policy=GroupHistoryCompletionPolicy.RECONCILIATION,
            task_name=task_name,
            task_id=task_id,
            raise_on_incomplete=False,
        )
    finally:
        db.close()


def _dispatch_group_history_reconciliation(
    *,
    market: str,
    formula_version: str,
    through_date: date,
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
        repair_group_history_reconciliation.si(
            market=market,
            formula_version=formula_version,
            through_date=through_iso,
        ).set(queue=market_jobs_queue_for_market(market)),
    )
    errback = fail_group_history_reconciliation.s(
        market=market,
        formula_version=formula_version,
        through_date=through_iso,
    ).set(queue="celery")
    return workflow.apply_async(link_error=errback).id


@celery_app.task(
    name="app.tasks.group_history_tasks.discover_group_history_reconciliation",
    queue="celery",
)
def discover_group_history_reconciliation() -> dict[str, str]:
    """Reserve and queue nonblocking repairs for enabled live markets."""
    db = SessionLocal()
    try:
        preferences = get_runtime_preferences(db)
        if preferences.bootstrap_state == "running":
            return {
                market: "bootstrap_running"
                for market in preferences.enabled_markets
            }

        repository = GroupHistoryReconciliationRepository()
        outcomes: dict[str, str] = {}
        for market in preferences.enabled_markets:
            market_code = normalize_market(market)
            if not get_market_catalog().get(
                market_code
            ).capabilities.group_rankings:
                outcomes[market_code] = "skipped"
                continue
            try:
                target = _resolve_current_group_history_target(
                    db,
                    market=market_code,
                )
            except Exception as exc:
                outcomes[market_code] = f"target_failed:{type(exc).__name__}"
                continue
            if not repository.reserve(db, target=target):
                outcomes[market_code] = "already_queued"
                continue
            try:
                readiness = _evaluate_group_history_readiness(db, target=target)
            except Exception as exc:
                repository.mark(
                    db,
                    target=target,
                    status=GroupHistoryReconciliationStatus.INCOMPLETE,
                    error=str(exc),
                )
                outcomes[market_code] = f"readiness_failed:{type(exc).__name__}"
                continue
            if readiness.ready:
                repository.mark(
                    db,
                    target=target,
                    status=GroupHistoryReconciliationStatus.READY,
                    counts=readiness.as_dict(),
                )
                outcomes[market_code] = "ready"
                continue
            try:
                _dispatch_group_history_reconciliation(
                    market=target.market,
                    formula_version=target.formula_version,
                    through_date=target.through_date,
                )
            except Exception as exc:
                repository.mark(
                    db,
                    target=target,
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
    name="app.tasks.group_history_tasks.fail_group_history_reconciliation",
    queue="celery",
)
def fail_group_history_reconciliation(
    *_celery_errback_args,
    market: str,
    formula_version: str,
    through_date: str,
) -> dict:
    target = GroupHistoryTarget(
        market=market,
        formula_version=formula_version,
        through_date=date.fromisoformat(through_date),
    )
    db = SessionLocal()
    try:
        GroupHistoryReconciliationRepository().mark(
            db,
            target=target,
            status=GroupHistoryReconciliationStatus.FAILED,
            error="Group history reconciliation task failed",
        )
        return {
            "status": "failed",
            "market": target.market,
            "formula_version": target.formula_version,
            "through_date": target.through_date.isoformat(),
        }
    finally:
        db.close()


__all__ = [
    "discover_group_history_reconciliation",
    "ensure_group_history",
    "fail_group_history_reconciliation",
    "repair_group_history_reconciliation",
]
