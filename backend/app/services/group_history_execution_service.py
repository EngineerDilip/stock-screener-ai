"""Single-owner execution and finalization for Group history repair."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Callable

from sqlalchemy.orm import Session

from app.services.group_history_bootstrap_service import (
    GroupHistoryBootstrapService,
    GroupHistoryBootstrapStatus,
)
from app.services.group_history_reconciliation import (
    GroupHistoryReconciliationRepository,
    GroupHistoryReconciliationStatus,
    GroupHistoryTarget,
)


class GroupHistoryCompletionPolicy(StrEnum):
    BOOTSTRAP = "bootstrap"
    RECONCILIATION = "group_history_reconciliation"


class GroupHistoryExecutionService:
    """Repair one immutable target and publish its result exactly once."""

    def __init__(
        self,
        *,
        bootstrap_service: GroupHistoryBootstrapService,
        reconciliation_repository: GroupHistoryReconciliationRepository,
        bump_epoch: Callable[[str], None],
        publish_snapshot: Callable[[], object | None],
        mark_started: Callable[..., object],
        mark_completed: Callable[..., object],
        mark_failed: Callable[..., object],
    ) -> None:
        self._bootstrap_service = bootstrap_service
        self._reconciliation_repository = reconciliation_repository
        self._bump_epoch = bump_epoch
        self._publish_snapshot = publish_snapshot
        self._mark_started = mark_started
        self._mark_completed = mark_completed
        self._mark_failed = mark_failed

    def execute(
        self,
        db: Session,
        *,
        target: GroupHistoryTarget,
        completion_policy: GroupHistoryCompletionPolicy,
        task_name: str,
        task_id: str | None,
        raise_on_incomplete: bool = True,
        activity_lifecycle: str | None = None,
    ) -> dict:
        lifecycle = activity_lifecycle or completion_policy.value
        activity = {
            "market": target.market,
            "stage_key": "group_history",
            "lifecycle": lifecycle,
            "task_name": task_name,
            "task_id": task_id,
        }
        failure_recorded = False
        try:
            if completion_policy is GroupHistoryCompletionPolicy.RECONCILIATION:
                self._reconciliation_repository.mark(
                    db,
                    target=target,
                    status=GroupHistoryReconciliationStatus.REPAIRING,
                )
            self._mark_started(
                db,
                **activity,
                message="Checking Group ranking history",
            )
            result = self._bootstrap_service.ensure(db, target=target)
            payload = result.as_dict()
            payload["timestamp"] = datetime.now().isoformat()
            payload["cache_invalidated"] = False
            payload["ui_snapshot_published"] = target.market != "US"

            if result.status is GroupHistoryBootstrapStatus.INCOMPLETE:
                message = f"Group history remains incomplete for {target.market}"
                if completion_policy is GroupHistoryCompletionPolicy.RECONCILIATION:
                    self._record_incomplete(
                        db,
                        target=target,
                        payload=payload,
                        message=message,
                    )
                self._mark_failure_safely(db, activity=activity, message=message)
                failure_recorded = True
                if raise_on_incomplete:
                    raise RuntimeError(message)
                return payload

            if result.status is GroupHistoryBootstrapStatus.READY:
                self._bump_epoch(target.market)
                payload["cache_invalidated"] = True
                if target.market == "US":
                    published = self._publish_snapshot()
                    payload["ui_snapshot_published"] = published is not None
                    if published is None:
                        message = "US Group bootstrap snapshot publication failed"
                        payload["status"] = "incomplete"
                        if (
                            completion_policy
                            is GroupHistoryCompletionPolicy.RECONCILIATION
                        ):
                            self._record_incomplete(
                                db,
                                target=target,
                                payload=payload,
                                message=message,
                            )
                        self._mark_failure_safely(
                            db,
                            activity=activity,
                            message=message,
                        )
                        failure_recorded = True
                        if raise_on_incomplete:
                            raise RuntimeError(message)
                        return payload

            if completion_policy is GroupHistoryCompletionPolicy.RECONCILIATION:
                self._reconciliation_repository.mark(
                    db,
                    target=target,
                    status=GroupHistoryReconciliationStatus.READY,
                    counts=payload.get("after"),
                )
            self._mark_completed(
                db,
                **activity,
                message="Group ranking history ready",
            )
            return payload
        except Exception as exc:
            db.rollback()
            if completion_policy is GroupHistoryCompletionPolicy.RECONCILIATION:
                self._mark_reconciliation_failed_safely(
                    db,
                    target=target,
                    error=str(exc),
                )
            if not failure_recorded:
                self._mark_failure_safely(
                    db,
                    activity=activity,
                    message=str(exc),
                )
            raise

    def _record_incomplete(
        self,
        db: Session,
        *,
        target: GroupHistoryTarget,
        payload: dict,
        message: str,
    ) -> None:
        self._reconciliation_repository.mark(
            db,
            target=target,
            status=GroupHistoryReconciliationStatus.INCOMPLETE,
            counts=payload.get("after"),
            error=message,
        )

    def _mark_failure_safely(
        self,
        db: Session,
        *,
        activity: dict,
        message: str,
    ) -> None:
        db.rollback()
        try:
            self._mark_failed(db, **activity, message=message)
        except Exception:
            db.rollback()

    def _mark_reconciliation_failed_safely(
        self,
        db: Session,
        *,
        target: GroupHistoryTarget,
        error: str,
    ) -> None:
        try:
            self._reconciliation_repository.mark(
                db,
                target=target,
                status=GroupHistoryReconciliationStatus.FAILED,
                error=error,
            )
        except Exception:
            db.rollback()


__all__ = [
    "GroupHistoryCompletionPolicy",
    "GroupHistoryExecutionService",
]
