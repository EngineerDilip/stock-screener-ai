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

    db = Mock()
    db.close = Mock()
    service = Mock()
    service.ensure.return_value = _result(ready=True)
    bumped = []
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(module, "_build_group_history_bootstrap_service", lambda: service)
    monkeypatch.setattr(
        module,
        "get_market_calendar_service",
        lambda: SimpleNamespace(
            last_completed_trading_day=lambda _market: date(2026, 6, 30)
        ),
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
    service.ensure.assert_called_once_with(
        db,
        market="US",
        through_date=date(2026, 6, 30),
    )
    module.mark_market_activity_completed.assert_called_once()
    db.close.assert_called_once()


def test_strict_group_history_task_raises_when_readiness_remains_incomplete(
    monkeypatch,
):
    from app.tasks import group_history_tasks as module

    db = Mock()
    service = Mock()
    service.ensure.return_value = _result(ready=False)
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(module, "_build_group_history_bootstrap_service", lambda: service)
    monkeypatch.setattr(
        module,
        "get_market_calendar_service",
        lambda: SimpleNamespace(
            last_completed_trading_day=lambda _market: date(2026, 6, 30)
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

    db = Mock()
    service = Mock()
    service.ensure.return_value = _result(ready=True)
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(module, "_build_group_history_bootstrap_service", lambda: service)
    monkeypatch.setattr(
        module,
        "get_market_calendar_service",
        lambda: SimpleNamespace(
            last_completed_trading_day=lambda _market: date(2026, 6, 30)
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
