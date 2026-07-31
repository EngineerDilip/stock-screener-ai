"""Operator CLI tests for balanced Market RS rollout."""

from __future__ import annotations

from argparse import Namespace
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.services.market_rs_rollout_executor import (
    MarketRsRolloutExecutionError,
    MarketRsRolloutOutcome,
)


def _options(tmp_path: Path, *, activate: bool) -> Namespace:
    return Namespace(
        market="US",
        through_date=date(2026, 4, 10),
        start_date=None,
        static_staging_dir=tmp_path / "stage",
        activate=activate,
    )


def test_dry_run_prints_report_and_never_activates(monkeypatch, tmp_path):
    from app.scripts import backfill_market_rs as module

    executor = MagicMock()
    executor.execute.return_value = MarketRsRolloutOutcome(
        backfill={"ok": True},
        activated=False,
        market="US",
        formula_version="balanced-percentile-v1",
    )
    monkeypatch.setattr(module, "get_market_rs_rollout_executor", lambda: executor)
    db = MagicMock()
    monkeypatch.setattr(module, "SessionLocal", lambda: db)

    result = module.execute_rollout(_options(tmp_path, activate=False))

    assert result == {"backfill": {"ok": True}, "activated": False}
    request = executor.execute.call_args.kwargs["request"]
    assert request.activate is False
    assert request.static_staging_dir is None
    db.close.assert_called_once_with()


def test_activate_requires_empty_non_serving_absolute_staging_directory(
    monkeypatch,
    tmp_path,
):
    from app.scripts import backfill_market_rs as module

    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "existing.json").write_text("{}", encoding="utf-8")
    options = _options(tmp_path, activate=True)

    with pytest.raises(module.RolloutCommandFailed, match="must be empty"):
        module.execute_rollout(options)


def test_activate_delegates_once_and_preserves_outcome(monkeypatch, tmp_path):
    from app.scripts import backfill_market_rs as module

    executor = MagicMock()
    executor.execute.return_value = MarketRsRolloutOutcome(
        backfill={"ok": True, "failed_count": 0},
        activated=True,
        market="US",
        formula_version="balanced-percentile-v1",
        feature_run_id=99,
        validation={"ok": True},
        static_staging_dir=str((tmp_path / "stage").resolve()),
    )
    monkeypatch.setattr(module, "get_market_rs_rollout_executor", lambda: executor)
    db = MagicMock()
    monkeypatch.setattr(module, "SessionLocal", lambda: db)

    result = module.execute_rollout(_options(tmp_path, activate=True))

    assert result["activated"] is True
    assert result["formula_version"] == "balanced-percentile-v1"
    executor.execute.assert_called_once()
    request = executor.execute.call_args.kwargs["request"]
    assert request.activate is True
    assert request.static_staging_dir == (tmp_path / "stage").resolve()


def test_publish_live_groups_does_not_duplicate_activation_cache_invalidation(
    monkeypatch,
):
    from app.services import market_rs_rollout_executor as module
    from app.services import group_rankings_cache, ui_snapshot_service

    bump_epoch = MagicMock()
    publish_bootstrap = MagicMock()
    monkeypatch.setattr(group_rankings_cache, "bump_group_rankings_epoch", bump_epoch)
    monkeypatch.setattr(
        ui_snapshot_service,
        "safe_publish_groups_bootstrap",
        publish_bootstrap,
    )

    module.publish_live_groups("US")

    bump_epoch.assert_not_called()
    publish_bootstrap.assert_called_once_with()


def test_executor_failure_is_exposed_as_command_failure(monkeypatch, tmp_path):
    from app.scripts import backfill_market_rs as module

    executor = MagicMock()
    executor.execute.side_effect = MarketRsRolloutExecutionError(
        "required backfill dates failed"
    )
    monkeypatch.setattr(module, "get_market_rs_rollout_executor", lambda: executor)
    db = MagicMock()
    monkeypatch.setattr(module, "SessionLocal", lambda: db)

    with pytest.raises(module.RolloutCommandFailed, match="required backfill dates failed"):
        module.execute_rollout(_options(tmp_path, activate=True))

    db.close.assert_called_once_with()
