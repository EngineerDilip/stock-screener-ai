"""Tests for the shared guarded Market RS rollout executor."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.domain.relative_strength import BALANCED_RS_FORMULA_VERSION
from app.services.market_rs_rollout_executor import (
    MarketRsRolloutExecutionError,
    MarketRsRolloutExecutor,
    MarketRsRolloutRequest,
)


def _report(*, ok: bool = True, failed_count: int = 0):
    return SimpleNamespace(
        ok=ok,
        failed_count=failed_count,
        to_dict=lambda: {
            "ok": ok,
            "failed_count": failed_count,
        },
    )


def _validation(*, ok: bool = True):
    errors = () if ok else ("static mismatch",)
    return SimpleNamespace(
        ok=ok,
        errors=errors,
        to_dict=lambda: {"ok": ok, "errors": list(errors)},
    )


def test_executor_activates_only_after_all_publication_gates(tmp_path: Path) -> None:
    events: list[str] = []
    rollout = MagicMock()
    rollout.backfill.side_effect = (
        lambda *args, **kwargs: events.append("backfill") or _report()
    )
    rollout.validate_activation.side_effect = (
        lambda *args, **kwargs: events.append("validate") or _validation()
    )
    rollout.activate.side_effect = (
        lambda *args, **kwargs: events.append("activate")
    )
    executor = MarketRsRolloutExecutor(
        rollout_service=rollout,
        feature_snapshot_builder=(
            lambda **kwargs: events.append("feature") or 99
        ),
        static_exporter=lambda **kwargs: events.append("static"),
        live_group_publisher=lambda market: events.append("publish_live"),
    )
    db = MagicMock()

    outcome = executor.execute(
        db,
        request=MarketRsRolloutRequest(
            market="us",
            through_date=date(2026, 7, 29),
            activate=True,
            static_staging_dir=tmp_path / "stage",
        ),
    )

    assert outcome.activated is True
    assert outcome.market == "US"
    assert outcome.formula_version == BALANCED_RS_FORMULA_VERSION
    assert outcome.feature_run_id == 99
    assert outcome.static_staging_dir == str((tmp_path / "stage").resolve())
    assert events == [
        "backfill",
        "feature",
        "static",
        "validate",
        "activate",
        "publish_live",
    ]
    db.expire_all.assert_called_once_with()


def test_executor_stops_before_publication_when_backfill_failed(tmp_path: Path) -> None:
    rollout = MagicMock()
    rollout.backfill.return_value = _report(ok=False, failed_count=1)
    feature_builder = MagicMock()
    executor = MarketRsRolloutExecutor(
        rollout_service=rollout,
        feature_snapshot_builder=feature_builder,
        static_exporter=MagicMock(),
        live_group_publisher=MagicMock(),
    )

    with pytest.raises(
        MarketRsRolloutExecutionError,
        match="required backfill dates failed",
    ):
        executor.execute(
            MagicMock(),
            request=MarketRsRolloutRequest(
                market="US",
                through_date=date(2026, 7, 29),
                activate=True,
                static_staging_dir=tmp_path / "stage",
            ),
        )

    feature_builder.assert_not_called()
    rollout.validate_activation.assert_not_called()
    rollout.activate.assert_not_called()


def test_executor_shadow_backfill_never_runs_activation_gates() -> None:
    rollout = MagicMock()
    rollout.backfill.return_value = _report()
    executor = MarketRsRolloutExecutor(
        rollout_service=rollout,
        feature_snapshot_builder=MagicMock(),
        static_exporter=MagicMock(),
        live_group_publisher=MagicMock(),
    )

    outcome = executor.execute(
        MagicMock(),
        request=MarketRsRolloutRequest(
            market="US",
            through_date=date(2026, 7, 29),
        ),
    )

    assert outcome.to_dict() == {
        "backfill": {"ok": True, "failed_count": 0},
        "activated": False,
    }
    rollout.validate_activation.assert_not_called()
    rollout.activate.assert_not_called()


def test_executor_rejects_invalid_staging_before_backfill(tmp_path: Path) -> None:
    rollout = MagicMock()
    executor = MarketRsRolloutExecutor(
        rollout_service=rollout,
        feature_snapshot_builder=MagicMock(),
        static_exporter=MagicMock(),
        live_group_publisher=MagicMock(),
    )
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "existing.json").write_text("{}", encoding="utf-8")

    with pytest.raises(MarketRsRolloutExecutionError, match="must be empty"):
        executor.execute(
            MagicMock(),
            request=MarketRsRolloutRequest(
                market="US",
                through_date=date(2026, 7, 29),
                activate=True,
                static_staging_dir=stage,
            ),
        )

    rollout.backfill.assert_not_called()
