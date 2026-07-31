"""Canonical Market RS Celery task tests."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.domain.relative_strength import BALANCED_RS_FORMULA_VERSION
from app.services.market_rs_inputs import MarketRsInputUnavailable
from app.services.market_rs_rollout_executor import (
    MarketRsActivationExecutionError,
    MarketRsActivationOutcome,
)


def _patch_task_dependencies(monkeypatch):
    from app.tasks import market_rs_tasks as module

    fake_db = MagicMock()
    fake_calendar = MagicMock()
    fake_calendar.is_trading_day.return_value = True
    fake_service = MagicMock()
    monkeypatch.setattr(module, "SessionLocal", lambda: fake_db)
    monkeypatch.setattr(module, "get_market_calendar_service", lambda: fake_calendar)
    monkeypatch.setattr(module, "get_market_rs_snapshot_service", lambda: fake_service)
    return module, fake_db, fake_calendar, fake_service


def test_calculate_market_rs_snapshot_returns_stable_completed_shape(monkeypatch):
    module, fake_db, fake_calendar, fake_service = _patch_task_dependencies(monkeypatch)
    fake_service.calculate.return_value = SimpleNamespace(
        id=42,
        status="completed",
        market="US",
        as_of_date=date(2026, 4, 10),
        formula_version=BALANCED_RS_FORMULA_VERSION,
        eligible_symbol_count=5000,
    )

    result = module.calculate_market_rs_snapshot.run(
        market="us",
        calculation_date="2026-04-10",
    )

    assert result == {
        "status": "completed",
        "market": "US",
        "as_of_date": "2026-04-10",
        "formula_version": BALANCED_RS_FORMULA_VERSION,
        "market_rs_run_id": 42,
        "eligible_symbol_count": 5000,
    }
    fake_calendar.is_trading_day.assert_called_once_with("US", date(2026, 4, 10))
    fake_service.calculate.assert_called_once_with(
        fake_db,
        market="US",
        as_of_date=date(2026, 4, 10),
        formula_version=BALANCED_RS_FORMULA_VERSION,
        rebuild_incompatible=False,
    )
    fake_db.close.assert_called_once_with()


def test_calculate_market_rs_snapshot_resolves_bootstrap_date_when_omitted(monkeypatch):
    module, fake_db, fake_calendar, fake_service = _patch_task_dependencies(monkeypatch)
    fake_calendar.last_completed_trading_day.return_value = date(2026, 4, 10)
    fake_service.calculate.return_value = SimpleNamespace(
        id=43,
        status="completed",
        market="HK",
        as_of_date=date(2026, 4, 10),
        formula_version=BALANCED_RS_FORMULA_VERSION,
        eligible_symbol_count=800,
    )

    result = module.calculate_market_rs_snapshot.run(
        market="HK",
        activity_lifecycle="bootstrap",
    )

    assert result["status"] == "completed"
    assert result["as_of_date"] == "2026-04-10"
    fake_calendar.last_completed_trading_day.assert_called_once_with("HK")
    fake_service.calculate.assert_called_once_with(
        fake_db,
        market="HK",
        as_of_date=date(2026, 4, 10),
        formula_version=BALANCED_RS_FORMULA_VERSION,
        rebuild_incompatible=False,
    )


def test_calculate_market_rs_snapshot_returns_input_diagnostics(monkeypatch):
    module, fake_db, _fake_calendar, fake_service = _patch_task_dependencies(
        monkeypatch
    )
    fake_service.calculate.side_effect = MarketRsInputUnavailable(
        "benchmark missing",
        reason_code="benchmark_anchor_missing",
        diagnostics={"missing_anchor_dates": {"SPY": ["2025-04-10"]}},
        benchmark_symbol="SPY",
        universe_hash="abc123",
        expected_symbol_count=5000,
    )

    result = module.calculate_market_rs_snapshot.run(
        market="US",
        calculation_date="2026-04-10",
    )

    assert result == {
        "status": "failed",
        "market": "US",
        "as_of_date": "2026-04-10",
        "formula_version": BALANCED_RS_FORMULA_VERSION,
        "reason_code": "benchmark_anchor_missing",
        "diagnostics": {
            "missing_anchor_dates": {"SPY": ["2025-04-10"]},
            "benchmark_symbol": "SPY",
            "universe_hash": "abc123",
            "expected_symbol_count": 5000,
        },
    }
    fake_db.close.assert_called_once_with()


def test_calculate_market_rs_snapshot_rejects_non_trading_date(monkeypatch):
    module, fake_db, fake_calendar, fake_service = _patch_task_dependencies(monkeypatch)
    fake_calendar.is_trading_day.return_value = False

    result = module.calculate_market_rs_snapshot.run(
        market="US",
        calculation_date="2026-04-11",
    )

    assert result["status"] == "failed"
    assert result["reason_code"] == "not_trading_day"
    fake_service.calculate.assert_not_called()
    fake_db.close.assert_not_called()


def test_calculate_market_rs_snapshot_rejects_shared_market():
    from app.tasks import market_rs_tasks as module

    result = module.calculate_market_rs_snapshot.run(
        market="SHARED",
        calculation_date="2026-04-10",
    )

    assert result["status"] == "failed"
    assert result["reason_code"] == "invalid_market"


def _patch_bootstrap_rollout_dependencies(monkeypatch):
    from app.tasks import market_rs_tasks as module

    db = MagicMock()
    calendar = MagicMock()
    calendar.last_completed_trading_day.return_value = date(2026, 7, 29)
    executor = MagicMock()
    started = MagicMock()
    completed = MagicMock()
    failed = MagicMock()
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(module, "get_market_calendar_service", lambda: calendar)
    monkeypatch.setattr(module, "get_market_rs_activation_executor", lambda: executor)
    monkeypatch.setattr(module, "mark_market_activity_started", started)
    monkeypatch.setattr(module, "mark_market_activity_completed", completed)
    monkeypatch.setattr(module, "mark_market_activity_failed", failed)
    return module, db, calendar, executor, started, completed, failed


def test_bootstrap_balanced_market_rs_requires_successful_activation(monkeypatch):
    (
        module,
        db,
        calendar,
        executor,
        started,
        completed,
        failed,
    ) = _patch_bootstrap_rollout_dependencies(monkeypatch)
    executor.execute.return_value = MarketRsActivationOutcome(
        backfill={"failed_count": 0},
        market="US",
        formula_version=BALANCED_RS_FORMULA_VERSION,
        feature_run_id=99,
        validation={"ok": True},
        static_staging_dir="/tmp/stage",
    )

    result = module.bootstrap_balanced_market_rs.run(
        market="us",
        activity_lifecycle="bootstrap",
    )

    assert result["status"] == "activated"
    assert result["formula_version"] == BALANCED_RS_FORMULA_VERSION
    assert executor.execute.call_args.kwargs["request"].market == "US"
    calendar.last_completed_trading_day.assert_called_once_with("US")
    started.assert_called_once()
    completed.assert_called_once()
    failed.assert_not_called()
    db.close.assert_called_once_with()


@pytest.mark.parametrize(
    "failure",
    [
        MarketRsActivationExecutionError("static validation failed"),
        RuntimeError("adapter failed"),
    ],
)
def test_bootstrap_balanced_market_rs_stops_chain_on_rollout_failure(
    monkeypatch,
    failure,
):
    module, db, _calendar, executor, _started, completed, failed = (
        _patch_bootstrap_rollout_dependencies(monkeypatch)
    )
    executor.execute.side_effect = failure

    with pytest.raises(type(failure), match=str(failure)):
        module.bootstrap_balanced_market_rs.run(market="US")

    db.rollback.assert_called_once_with()
    completed.assert_not_called()
    failed.assert_called_once()
    db.close.assert_called_once_with()


def test_bootstrap_balanced_market_rs_retries_transient_connection_failure(
    monkeypatch,
):
    module, db, _calendar, executor, _started, _completed, failed = (
        _patch_bootstrap_rollout_dependencies(monkeypatch)
    )
    error = ConnectionError("database unavailable")
    executor.execute.side_effect = error
    retry = MagicMock(side_effect=RuntimeError("retry requested"))
    monkeypatch.setattr(module, "_retry_connection_failure", retry)

    with pytest.raises(RuntimeError, match="retry requested"):
        module.bootstrap_balanced_market_rs.run(market="US")

    db.rollback.assert_called_once_with()
    retry.assert_called_once()
    assert retry.call_args.args[1] is error
    failed.assert_not_called()
    db.close.assert_called_once_with()
