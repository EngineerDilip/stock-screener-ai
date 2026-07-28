from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services.group_history_bootstrap_service import GroupHistoryBootstrapStatus


def _result(*, ready: bool):
    status = (
        GroupHistoryBootstrapStatus.READY
        if ready
        else GroupHistoryBootstrapStatus.INCOMPLETE
    )
    return SimpleNamespace(
        status=status,
        after=SimpleNamespace(ready=ready),
        as_dict=lambda: {"status": status.value, "after": {"ready": ready}},
    )


def test_ensure_group_history_invalidates_cache_and_publishes_us_snapshot(
    monkeypatch,
):
    from app.tasks import group_history_tasks as module
    from app.services.group_history_reconciliation import GroupHistoryTarget

    db = Mock()
    db.close = Mock()
    service = Mock()
    service.ensure.return_value = _result(ready=True)
    bumped = []
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(module, "_build_group_history_bootstrap_service", lambda: service)
    target = GroupHistoryTarget(
        market="US",
        formula_version="balanced-v1",
        through_date=date(2026, 6, 30),
    )
    monkeypatch.setattr(
        module,
        "_resolve_current_group_history_target",
        lambda _db, *, market: target,
    )
    monkeypatch.setattr(module, "bump_group_rankings_epoch", bumped.append)
    monkeypatch.setattr(
        module,
        "safe_publish_groups_bootstrap",
        lambda: {"snapshot_revision": "42"},
    )
    monkeypatch.setattr(module, "mark_market_activity_started", Mock())
    monkeypatch.setattr(module, "mark_market_activity_completed", Mock())
    monkeypatch.setattr(module, "mark_market_activity_failed", Mock())

    result = module.ensure_group_history.run.__wrapped__(
        module.ensure_group_history,
        market="US",
        strict=True,
    )

    assert result["status"] == "ready"
    assert result["cache_invalidated"] is True
    assert result["ui_snapshot_published"] is True
    assert bumped == ["US"]
    service.ensure.assert_called_once_with(db, target=target)
    module.mark_market_activity_completed.assert_called_once()
    db.close.assert_called_once()


def test_strict_group_history_task_raises_when_readiness_remains_incomplete(
    monkeypatch,
):
    from app.tasks import group_history_tasks as module
    from app.services.group_history_reconciliation import GroupHistoryTarget

    db = Mock()
    service = Mock()
    service.ensure.return_value = _result(ready=False)
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(module, "_build_group_history_bootstrap_service", lambda: service)
    monkeypatch.setattr(
        module,
        "_resolve_current_group_history_target",
        lambda _db, *, market: GroupHistoryTarget(
            market=market,
            formula_version="balanced-v1",
            through_date=date(2026, 6, 30),
        ),
    )
    monkeypatch.setattr(module, "mark_market_activity_started", Mock())
    monkeypatch.setattr(module, "mark_market_activity_completed", Mock())
    monkeypatch.setattr(module, "mark_market_activity_failed", Mock())

    with pytest.raises(RuntimeError, match="Group history remains incomplete"):
        module.ensure_group_history.run.__wrapped__(
            module.ensure_group_history,
            market="US",
            strict=True,
        )

    module.mark_market_activity_failed.assert_called_once()
    db.close.assert_called_once()


def test_strict_group_history_task_records_snapshot_publication_failure_once(
    monkeypatch,
):
    from app.tasks import group_history_tasks as module
    from app.services.group_history_reconciliation import GroupHistoryTarget

    db = Mock()
    service = Mock()
    service.ensure.return_value = _result(ready=True)
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(module, "_build_group_history_bootstrap_service", lambda: service)
    monkeypatch.setattr(
        module,
        "_resolve_current_group_history_target",
        lambda _db, *, market: GroupHistoryTarget(
            market=market,
            formula_version="balanced-v1",
            through_date=date(2026, 6, 30),
        ),
    )
    monkeypatch.setattr(module, "bump_group_rankings_epoch", Mock())
    monkeypatch.setattr(module, "safe_publish_groups_bootstrap", lambda: None)
    monkeypatch.setattr(module, "mark_market_activity_started", Mock())
    monkeypatch.setattr(module, "mark_market_activity_completed", Mock())
    monkeypatch.setattr(module, "mark_market_activity_failed", Mock())

    with pytest.raises(RuntimeError, match="snapshot publication failed"):
        module.ensure_group_history.run.__wrapped__(
            module.ensure_group_history,
            market="US",
            strict=True,
        )

    module.mark_market_activity_failed.assert_called_once()


def test_execution_service_finalizes_successful_us_reconciliation_once():
    from app.services.group_history_execution_service import (
        GroupHistoryCompletionPolicy,
        GroupHistoryExecutionService,
    )
    from app.services.group_history_reconciliation import GroupHistoryTarget

    db = Mock()
    bootstrap = Mock()
    bootstrap.ensure.return_value = _result(ready=True)
    repository = Mock()
    bump = Mock()
    publish = Mock(return_value={"snapshot_revision": "42"})
    completed = Mock()
    service = GroupHistoryExecutionService(
        bootstrap_service=bootstrap,
        reconciliation_repository=repository,
        bump_epoch=bump,
        publish_snapshot=publish,
        mark_started=Mock(),
        mark_completed=completed,
        mark_failed=Mock(),
    )
    target = GroupHistoryTarget(
        market="US",
        formula_version="captured-v1",
        through_date=date(2026, 6, 30),
    )

    result = service.execute(
        db,
        target=target,
        completion_policy=GroupHistoryCompletionPolicy.RECONCILIATION,
        task_name="repair_group_history_reconciliation",
        task_id="task-1",
    )

    assert result["status"] == "ready"
    bootstrap.ensure.assert_called_once_with(db, target=target)
    bump.assert_called_once_with("US")
    publish.assert_called_once_with()
    assert repository.mark.call_args.kwargs["target"] == target
    assert repository.mark.call_args.kwargs["status"].value == "ready"
    completed.assert_called_once()


def test_activity_completion_failure_does_not_reclassify_success():
    from app.services.group_history_execution_service import (
        GroupHistoryCompletionPolicy,
        GroupHistoryExecutionService,
    )
    from app.services.group_history_reconciliation import GroupHistoryTarget

    db = Mock()
    bootstrap = Mock()
    bootstrap.ensure.return_value = _result(ready=True)
    repository = Mock()
    service = GroupHistoryExecutionService(
        bootstrap_service=bootstrap,
        reconciliation_repository=repository,
        bump_epoch=Mock(),
        publish_snapshot=Mock(return_value={"snapshot_revision": "42"}),
        mark_started=Mock(),
        mark_completed=Mock(side_effect=RuntimeError("telemetry unavailable")),
        mark_failed=Mock(),
    )

    result = service.execute(
        db,
        target=GroupHistoryTarget(
            market="US",
            formula_version="captured-v1",
            through_date=date(2026, 6, 30),
        ),
        completion_policy=GroupHistoryCompletionPolicy.RECONCILIATION,
        task_name="repair_group_history_reconciliation",
        task_id="task-1",
    )

    assert result["status"] == "ready"
    assert [call.kwargs["status"].value for call in repository.mark.call_args_list] == [
        "repairing",
        "ready",
    ]
