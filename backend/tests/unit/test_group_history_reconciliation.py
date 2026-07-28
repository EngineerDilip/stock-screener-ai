from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

from sqlalchemy.orm import sessionmaker


def _target():
    from app.services.group_history_reconciliation import GroupHistoryTarget

    return GroupHistoryTarget(
        market="US",
        formula_version="balanced-v1",
        through_date=date(2026, 6, 30),
    )


def test_reconciliation_marker_reservation_is_idempotent_and_resumable(db_session):
    from app.services.group_history_reconciliation import (
        GroupHistoryReconciliationRepository,
        GroupHistoryReconciliationStatus,
    )

    repository = GroupHistoryReconciliationRepository()
    target = _target()

    assert repository.reserve(db_session, target=target) is True
    assert repository.reserve(db_session, target=target) is False

    repository.mark(
        db_session,
        target=target,
        status=GroupHistoryReconciliationStatus.INCOMPLETE,
        error="worker interrupted",
    )

    assert repository.reserve(db_session, target=target) is True
    marker = repository.load(db_session, market="US")
    assert marker.status is GroupHistoryReconciliationStatus.QUEUED
    assert marker.error is None


def test_existing_marker_compare_and_swap_allows_only_one_stale_reservation(
    db_session,
):
    from app.models.app_settings import AppSetting
    from app.services.group_history_reconciliation import (
        GroupHistoryReconciliationRepository,
        GroupHistoryReconciliationStatus,
        GroupHistoryTarget,
    )

    repository = GroupHistoryReconciliationRepository()
    target = GroupHistoryTarget(
        market="US",
        formula_version="balanced-v1",
        through_date=date(2026, 6, 30),
    )
    repository.mark(
        db_session,
        target=target,
        status=GroupHistoryReconciliationStatus.INCOMPLETE,
    )
    setting = (
        db_session.query(AppSetting)
        .filter(AppSetting.key == repository.key("US"))
        .one()
    )
    observed_value = setting.value
    observed_marker = repository.load(db_session, market="US")

    class _StaleObservationRepository(GroupHistoryReconciliationRepository):
        def load(self, _db, *, market):
            assert market == "US"
            return observed_marker

        def _load_record(self, _db, *, market):
            assert market == "US"
            return observed_marker, observed_value

    Session = sessionmaker(bind=db_session.get_bind())
    first = Session()
    second = Session()
    try:
        contenders = _StaleObservationRepository()
        assert contenders.reserve(first, target=target) is True
        assert contenders.reserve(second, target=target) is False
    finally:
        first.close()
        second.close()


def test_stale_active_marker_can_be_reserved_after_interrupted_worker(db_session):
    from app.models.app_settings import AppSetting
    from app.services.group_history_reconciliation import (
        GroupHistoryReconciliationRepository,
    )

    repository = GroupHistoryReconciliationRepository()
    target = _target()
    assert repository.reserve(db_session, target=target)
    setting = (
        db_session.query(AppSetting)
        .filter(AppSetting.key == repository.key("US"))
        .one()
    )
    payload = json.loads(setting.value)
    payload["updated_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=7)
    ).isoformat()
    setting.value = json.dumps(payload)
    db_session.commit()

    assert repository.reserve(db_session, target=target) is True


def _readiness(*, ready: bool, formula: str = "balanced-v1"):
    return SimpleNamespace(
        ready=ready,
        supported=True,
        formula_version=formula,
        as_dict=lambda: {
            "ready": ready,
            "formula_version": formula,
            "missing_dates": [] if ready else ["2026-05-01"],
        },
    )


def test_celery_discovery_queues_incomplete_enabled_group_market(monkeypatch):
    from app.tasks import group_history_tasks as module
    from app.services.group_history_reconciliation import GroupHistoryTarget

    db = Mock()
    db.close = Mock()
    repository = Mock()
    repository.reserve.return_value = True
    dispatched = []
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        module,
        "get_runtime_preferences",
        lambda _db: SimpleNamespace(
            enabled_markets=["US", "SG"],
            bootstrap_state="ready",
        ),
    )
    monkeypatch.setattr(
        module,
        "_resolve_current_group_history_target",
        lambda _db, *, market: GroupHistoryTarget(
            market=market,
            formula_version="balanced-v1",
            through_date=date(2026, 6, 30),
        ),
    )
    monkeypatch.setattr(
        module,
        "_evaluate_group_history_readiness",
        lambda _db, *, target: _readiness(
            ready=False,
            formula=target.formula_version,
        ),
    )
    monkeypatch.setattr(
        module,
        "get_market_calendar_service",
        lambda: SimpleNamespace(
            last_completed_trading_day=lambda _market: date(2026, 6, 30)
        ),
    )
    monkeypatch.setattr(
        module,
        "GroupHistoryReconciliationRepository",
        lambda: repository,
    )
    monkeypatch.setattr(
        module,
        "_dispatch_group_history_reconciliation",
        lambda **kwargs: dispatched.append(kwargs) or "repair-us-1",
    )

    result = module.discover_group_history_reconciliation.run()

    assert result == {"US": "queued", "SG": "skipped"}
    assert dispatched == [
        {
            "market": "US",
            "formula_version": "balanced-v1",
            "through_date": date(2026, 6, 30),
        }
    ]
    db.close.assert_called_once()


def test_startup_reconciliation_skips_while_runtime_bootstrap_is_running(
    monkeypatch,
):
    from app.tasks import group_history_tasks as module

    db = Mock()
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        module,
        "get_runtime_preferences",
        lambda _db: SimpleNamespace(
            enabled_markets=["US"],
            bootstrap_state="running",
        ),
    )
    dispatch = Mock()
    monkeypatch.setattr(module, "_dispatch_group_history_reconciliation", dispatch)

    assert module.discover_group_history_reconciliation.run() == {
        "US": "bootstrap_running"
    }
    dispatch.assert_not_called()


def test_startup_reconciliation_database_ready_is_verified_noop(monkeypatch):
    from app.tasks import group_history_tasks as module
    from app.services.group_history_reconciliation import GroupHistoryTarget

    db = Mock()
    repository = Mock()
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        module,
        "get_runtime_preferences",
        lambda _db: SimpleNamespace(
            enabled_markets=["US"],
            bootstrap_state="ready",
        ),
    )
    monkeypatch.setattr(
        module,
        "get_market_calendar_service",
        lambda: SimpleNamespace(
            last_completed_trading_day=lambda _market: date(2026, 6, 30)
        ),
    )
    monkeypatch.setattr(
        module,
        "_resolve_current_group_history_target",
        lambda _db, *, market: GroupHistoryTarget(
            market=market,
            formula_version="balanced-v1",
            through_date=date(2026, 6, 30),
        ),
    )
    monkeypatch.setattr(
        module,
        "_evaluate_group_history_readiness",
        lambda _db, *, target: _readiness(
            ready=True,
            formula=target.formula_version,
        ),
    )
    monkeypatch.setattr(
        module,
        "GroupHistoryReconciliationRepository",
        lambda: repository,
    )
    dispatch = Mock()
    monkeypatch.setattr(module, "_dispatch_group_history_reconciliation", dispatch)

    assert module.discover_group_history_reconciliation.run() == {"US": "ready"}
    dispatch.assert_not_called()
    assert repository.mark.call_args.kwargs["status"].value == "ready"


def test_dispatch_failure_returns_marker_to_incomplete(monkeypatch):
    from app.tasks import group_history_tasks as module
    from app.services.group_history_reconciliation import GroupHistoryTarget

    db = Mock()
    repository = Mock()
    repository.reserve.return_value = True
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        module,
        "get_runtime_preferences",
        lambda _db: SimpleNamespace(
            enabled_markets=["US"],
            bootstrap_state="ready",
        ),
    )
    monkeypatch.setattr(
        module,
        "get_market_calendar_service",
        lambda: SimpleNamespace(
            last_completed_trading_day=lambda _market: date(2026, 6, 30)
        ),
    )
    monkeypatch.setattr(
        module,
        "_resolve_current_group_history_target",
        lambda _db, *, market: GroupHistoryTarget(
            market=market,
            formula_version="balanced-v1",
            through_date=date(2026, 6, 30),
        ),
    )
    monkeypatch.setattr(
        module,
        "_evaluate_group_history_readiness",
        lambda _db, *, target: _readiness(
            ready=False,
            formula=target.formula_version,
        ),
    )
    monkeypatch.setattr(
        module,
        "GroupHistoryReconciliationRepository",
        lambda: repository,
    )
    monkeypatch.setattr(
        module,
        "_dispatch_group_history_reconciliation",
        Mock(side_effect=RuntimeError("broker unavailable")),
    )

    assert module.discover_group_history_reconciliation.run() == {
        "US": "dispatch_failed"
    }
    assert repository.mark.call_args.kwargs["status"].value == "incomplete"
    assert repository.mark.call_args.kwargs["error"] == "broker unavailable"


def test_reconciliation_dispatches_price_repair_and_verification_to_expected_queues(
    monkeypatch,
):
    from app.tasks import group_history_tasks as module
    from app.tasks.market_queues import (
        data_fetch_queue_for_market,
        market_jobs_queue_for_market,
    )

    captured = {}

    class _Workflow:
        def apply_async(self, **kwargs):
            captured["apply_kwargs"] = kwargs
            return SimpleNamespace(id="repair-us-1")

    def _chain(*signatures):
        captured["signatures"] = signatures
        return _Workflow()

    monkeypatch.setattr(module, "chain", _chain)

    task_id = module._dispatch_group_history_reconciliation(
        market="US",
        formula_version="balanced-v1",
        through_date=date(2026, 6, 30),
    )

    assert task_id == "repair-us-1"
    signatures = captured["signatures"]
    assert [signature.task for signature in signatures] == [
        "app.tasks.cache_tasks.smart_refresh_cache",
        "app.tasks.group_history_tasks.repair_group_history_reconciliation",
    ]
    assert [signature.options["queue"] for signature in signatures] == [
        data_fetch_queue_for_market("US"),
        market_jobs_queue_for_market("US"),
    ]
    assert signatures[0].kwargs["ensure_group_history"] is True
    assert signatures[1].kwargs == {
        "market": "US",
        "formula_version": "balanced-v1",
        "through_date": "2026-06-30",
    }
    errback = captured["apply_kwargs"]["link_error"]
    assert errback.task == (
        "app.tasks.group_history_tasks.fail_group_history_reconciliation"
    )
    assert errback.options["queue"] == "celery"


def test_reconciliation_repair_executes_the_captured_target(monkeypatch):
    from app.services.group_history_reconciliation import GroupHistoryTarget
    from app.tasks import group_history_tasks as module

    db = Mock()
    executor = Mock()
    executor.execute.return_value = {"status": "ready"}
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(module, "_build_group_history_execution_service", lambda: executor)

    result = module.repair_group_history_reconciliation.run.__wrapped__(
        module.repair_group_history_reconciliation,
        market="us",
        formula_version="captured-v1",
        through_date="2026-06-30",
    )

    assert result == {"status": "ready"}
    assert executor.execute.call_args.kwargs["target"] == GroupHistoryTarget(
        market="US",
        formula_version="captured-v1",
        through_date=date(2026, 6, 30),
    )
    db.close.assert_called_once_with()
