"""Tests for the shared guarded Market RS rollout executor."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.domain.relative_strength import (
    BALANCED_RS_FORMULA_VERSION,
    GroupSnapshotIdentity,
)
from app.services.market_rs_activation_coverage import MarketRsActivationCoverage
from app.services.market_rs_rollout_contracts import (
    ActivationValidationReport,
    MarketRsActivationArtifactPolicy,
    BackfillReport,
)
from app.services.market_rs_rollout_executor import (
    MarketRsActivationExecutionError,
    MarketRsActivationExecutor,
    MarketRsActivationRequest,
)


def _report(*, ok: bool = True, failed_count: int = 0):
    validation_errors = () if ok else ("backfill failed",)
    return BackfillReport(
        market="US",
        formula_version=BALANCED_RS_FORMULA_VERSION,
        requested_start_date=date(2026, 1, 23),
        through_date=date(2026, 7, 29),
        first_valid_date=date(2026, 1, 23),
        candidate_count=1,
        completed_count=0 if failed_count else 1,
        failed_count=failed_count,
        latest_run_id=99 if not failed_count else None,
        group_row_count=1 if not failed_count else 0,
        results=(),
        validation_errors=validation_errors,
    )


def _validation(*, ok: bool = True):
    errors = () if ok else ("static mismatch",)
    return ActivationValidationReport(
        market="US",
        formula_version=BALANCED_RS_FORMULA_VERSION,
        through_date=date(2026, 7, 29),
        first_valid_date=date(2026, 1, 23),
        candidate_count=1,
        latest_market_rs_run_id=99,
        latest_universe_hash="universe",
        feature_run_id=99,
        feature_universe_hash="universe",
        static_bundle_sha256="bundle",
        errors=errors,
        artifact_policy=MarketRsActivationArtifactPolicy.STATIC_SITE,
    )


class _RolloutFake:
    def __init__(
        self,
        *,
        report: BackfillReport | None = None,
        validation: ActivationValidationReport | None = None,
        events: list[str] | None = None,
    ) -> None:
        self.coverage = MarketRsActivationCoverage(
            market="US",
            through_date=date(2026, 7, 29),
            required_dates=(date(2026, 1, 23), date(2026, 7, 29)),
        )
        self.report = report or _report()
        self.validation = validation or _validation()
        self.events = events
        self.coverage_requested = False
        self.backfill_requested = False
        self.validation_requested = False
        self.activation_requested = False

    def activation_coverage(self, *, market: str, through_date: date):
        assert market == "US"
        assert through_date == self.coverage.through_date
        self.coverage_requested = True
        return self.coverage

    def backfill_activation(self, _db, *, coverage):
        assert coverage is self.coverage
        self.backfill_requested = True
        if self.events is not None:
            self.events.append("backfill")
        return self.report

    def validate_activation(
        self,
        _db,
        *,
        coverage,
        feature_run_id,
        static_staging_dir,
        artifact_policy=MarketRsActivationArtifactPolicy.STATIC_SITE,
    ):
        assert coverage is self.coverage
        assert feature_run_id == 99
        if artifact_policy.requires_static_artifacts:
            assert static_staging_dir is not None
            assert static_staging_dir.is_absolute()
        else:
            assert static_staging_dir is None
        self.artifact_policy = artifact_policy
        self.validation_requested = True
        if self.events is not None:
            self.events.append("validate")
        if self.validation.artifact_policy is artifact_policy:
            return self.validation
        return replace(self.validation, artifact_policy=artifact_policy)

    def activate(self, _db, **kwargs) -> None:
        self.activation_kwargs = kwargs
        self.activation_requested = True
        if self.events is not None:
            self.events.append("activate")


def test_executor_activates_only_after_bounded_publication_gates(
    tmp_path: Path,
) -> None:
    events: list[str] = []
    report = _report()
    validation = _validation()
    rollout = _RolloutFake(report=report, validation=validation, events=events)
    published_identities: list[GroupSnapshotIdentity] = []
    executor = MarketRsActivationExecutor(
        rollout_service=rollout,
        feature_snapshot_builder=(lambda **kwargs: events.append("feature") or 99),
        static_exporter=lambda **kwargs: events.append("static"),
        live_group_publisher=lambda identity: (
            published_identities.append(identity),
            events.append("publish_live"),
        ),
    )
    db = MagicMock()

    outcome = executor.execute(
        db,
        request=MarketRsActivationRequest(
            market="us",
            through_date=date(2026, 7, 29),
            static_staging_dir=tmp_path / "stage",
        ),
    )

    assert outcome.market == "US"
    assert outcome.formula_version == BALANCED_RS_FORMULA_VERSION
    assert outcome.feature_run_id == 99
    assert outcome.static_staging_dir == str((tmp_path / "stage").resolve())
    assert outcome.backfill is report
    assert outcome.validation is validation
    assert published_identities == [
        GroupSnapshotIdentity(
            market="US",
            as_of_date=date(2026, 7, 29),
            formula_version=BALANCED_RS_FORMULA_VERSION,
        )
    ]
    assert events == [
        "backfill",
        "feature",
        "static",
        "validate",
        "activate",
        "publish_live",
    ]
    assert rollout.coverage_requested
    assert rollout.backfill_requested
    assert rollout.validation_requested
    assert rollout.activation_requested
    assert db.expire_all.call_count >= 1
    assert db.expunge_all.call_count >= 1


def test_live_activation_uses_report_policy_without_static_staging_dir() -> None:
    events: list[str] = []
    rollout = _RolloutFake(events=events)
    static_exporter = MagicMock(side_effect=AssertionError("static export is live-only dead weight"))
    executor = MarketRsActivationExecutor(
        rollout_service=rollout,
        feature_snapshot_builder=(lambda **kwargs: events.append("feature") or 99),
        static_exporter=static_exporter,
        live_group_publisher=lambda identity: events.append("publish_live"),
    )
    db = MagicMock()

    outcome = executor.execute(
        db,
        request=MarketRsActivationRequest(
            market="US",
            through_date=date(2026, 7, 29),
            artifact_policy=MarketRsActivationArtifactPolicy.LIVE_RUNTIME,
        ),
    )

    assert outcome.market == "US"
    assert outcome.static_staging_dir is None
    static_exporter.assert_not_called()
    assert rollout.artifact_policy is MarketRsActivationArtifactPolicy.LIVE_RUNTIME
    assert "require_static_artifacts" not in rollout.activation_kwargs
    assert rollout.activation_kwargs["validation"].artifact_policy is (
        MarketRsActivationArtifactPolicy.LIVE_RUNTIME
    )
    assert events == ["backfill", "feature", "validate", "activate", "publish_live"]
    assert db.expunge_all.call_count >= 1


def test_executor_stops_before_publication_when_backfill_failed(tmp_path: Path) -> None:
    rollout = _RolloutFake(report=_report(ok=False, failed_count=1))
    feature_builder = MagicMock()
    executor = MarketRsActivationExecutor(
        rollout_service=rollout,
        feature_snapshot_builder=feature_builder,
        static_exporter=MagicMock(),
        live_group_publisher=MagicMock(),
    )

    with pytest.raises(
        MarketRsActivationExecutionError,
        match="required backfill dates failed",
    ):
        executor.execute(
            MagicMock(),
            request=MarketRsActivationRequest(
                market="US",
                through_date=date(2026, 7, 29),
                static_staging_dir=tmp_path / "stage",
            ),
        )

    feature_builder.assert_not_called()
    assert not rollout.validation_requested
    assert not rollout.activation_requested


def test_executor_rejects_invalid_staging_before_backfill(tmp_path: Path) -> None:
    rollout = _RolloutFake()
    executor = MarketRsActivationExecutor(
        rollout_service=rollout,
        feature_snapshot_builder=MagicMock(),
        static_exporter=MagicMock(),
        live_group_publisher=MagicMock(),
    )
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "existing.json").write_text("{}", encoding="utf-8")

    with pytest.raises(MarketRsActivationExecutionError, match="must be empty"):
        executor.execute(
            MagicMock(),
            request=MarketRsActivationRequest(
                market="US",
                through_date=date(2026, 7, 29),
                static_staging_dir=stage,
            ),
        )

    assert not rollout.coverage_requested
    assert not rollout.backfill_requested


def test_executor_rejects_staging_that_overlaps_serving_directory(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from app.services import market_rs_rollout_executor as module

    serving_dir = tmp_path / "served"
    monkeypatch.setattr(
        module.settings,
        "static_export_output_dir",
        str(serving_dir),
    )
    executor = MarketRsActivationExecutor(
        rollout_service=_RolloutFake(),
        feature_snapshot_builder=MagicMock(),
        static_exporter=MagicMock(),
        live_group_publisher=MagicMock(),
    )

    for stage in (serving_dir / "stage", tmp_path):
        with pytest.raises(
            MarketRsActivationExecutionError,
            match="must not overlap",
        ):
            executor.execute(
                MagicMock(),
                request=MarketRsActivationRequest(
                    market="US",
                    through_date=date(2026, 7, 29),
                    static_staging_dir=stage,
                ),
            )


def test_executor_wraps_invalid_market_as_activation_error(tmp_path: Path) -> None:
    executor = MarketRsActivationExecutor(
        rollout_service=_RolloutFake(),
        feature_snapshot_builder=MagicMock(),
        static_exporter=MagicMock(),
        live_group_publisher=MagicMock(),
    )

    with pytest.raises(
        MarketRsActivationExecutionError,
        match="Unsupported market",
    ):
        executor.execute(
            MagicMock(),
            request=MarketRsActivationRequest(
                market="INVALID",
                through_date=date(2026, 7, 29),
                static_staging_dir=tmp_path / "stage",
            ),
        )


def test_executor_wraps_invalid_activation_date_as_activation_error(
    tmp_path: Path,
) -> None:
    rollout = MagicMock()
    rollout.activation_coverage.side_effect = ValueError(
        "Guarded activation date must be a completed market trading day."
    )
    executor = MarketRsActivationExecutor(
        rollout_service=rollout,
        feature_snapshot_builder=MagicMock(),
        static_exporter=MagicMock(),
        live_group_publisher=MagicMock(),
    )

    with pytest.raises(
        MarketRsActivationExecutionError,
        match="completed market trading day",
    ):
        executor.execute(
            MagicMock(),
            request=MarketRsActivationRequest(
                market="US",
                through_date=date(2026, 7, 26),
                static_staging_dir=tmp_path / "stage",
            ),
        )

    rollout.backfill_activation.assert_not_called()


def test_executor_stops_before_activation_when_validation_failed(
    tmp_path: Path,
) -> None:
    rollout = _RolloutFake(validation=_validation(ok=False))
    publisher = MagicMock()
    executor = MarketRsActivationExecutor(
        rollout_service=rollout,
        feature_snapshot_builder=lambda **_kwargs: 99,
        static_exporter=lambda **_kwargs: None,
        live_group_publisher=publisher,
    )

    with pytest.raises(
        MarketRsActivationExecutionError,
        match="Activation validation failed: static mismatch",
    ):
        executor.execute(
            MagicMock(),
            request=MarketRsActivationRequest(
                market="US",
                through_date=date(2026, 7, 29),
                static_staging_dir=tmp_path / "stage",
            ),
        )

    assert not rollout.activation_requested
    publisher.assert_not_called()


def test_executor_treats_live_group_snapshot_as_best_effort(
    tmp_path: Path,
) -> None:
    rollout = _RolloutFake()
    executor = MarketRsActivationExecutor(
        rollout_service=rollout,
        feature_snapshot_builder=lambda **_kwargs: 99,
        static_exporter=lambda **_kwargs: None,
        live_group_publisher=MagicMock(side_effect=OSError("snapshot store full")),
    )

    outcome = executor.execute(
        MagicMock(),
        request=MarketRsActivationRequest(
            market="US",
            through_date=date(2026, 7, 29),
            static_staging_dir=tmp_path / "stage",
        ),
    )

    assert outcome.market == "US"
    assert rollout.activation_requested is True
