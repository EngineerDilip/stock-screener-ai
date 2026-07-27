"""Celery orchestration for live Group history readiness and repair."""

from __future__ import annotations

from datetime import datetime

from app.celery_app import celery_app
from app.database import SessionLocal
from app.services.group_history_bootstrap_service import (
    GroupHistoryBootstrapService,
    GroupHistoryBootstrapStatus,
)
from app.services.group_history_readiness_service import (
    GroupHistoryReadinessService,
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
from app.tasks.workload_coordination import serialized_market_workload
from app.wiring.bootstrap import get_group_rank_service, get_market_calendar_service


def _build_group_history_bootstrap_service() -> GroupHistoryBootstrapService:
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
    return GroupHistoryBootstrapService(
        readiness_service=GroupHistoryReadinessService(
            calendar_service=calendar,
            snapshot_reader=coordinator.reader,
            market_rs_repository=repository,
            rrg_history_provider=history_provider,
        ),
        snapshot_coordinator=coordinator,
        universe_resolver=universe_resolver,
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
) -> dict:
    market_code = normalize_market(market)
    lifecycle = activity_lifecycle or "bootstrap"
    task_name = getattr(self, "name", "ensure_group_history")
    task_id = getattr(getattr(self, "request", None), "id", None)
    db = SessionLocal()
    try:
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
        if not isinstance(exc, RuntimeError) or "remains incomplete" not in str(exc):
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


__all__ = ["ensure_group_history"]
