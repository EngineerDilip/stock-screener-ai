"""Tests for shared static-market publication policy."""

from __future__ import annotations

from app.domain.relative_strength import BALANCED_RS_FORMULA_VERSION
from app.services.static_market_publish_policy import (
    StaticMarketRsArtifactState,
    classify_static_market_rs_artifact_result,
    collect_static_no_current_artifact_failures,
)


def test_market_rs_price_coverage_gap_is_no_current_artifact():
    result = {
        "status": "failed",
        "market": "DE",
        "as_of_date": "2026-07-27",
        "formula_version": BALANCED_RS_FORMULA_VERSION,
        "reason_code": "current_adjusted_price_coverage_below_threshold",
        "diagnostics": {
            "current_price_coverage": 0.8434065934065934,
            "minimum_current_price_coverage": 0.88,
        },
    }

    assert (
        classify_static_market_rs_artifact_result(
            result,
            market="DE",
            as_of_date="2026-07-27",
            formula_version=BALANCED_RS_FORMULA_VERSION,
        )
        is StaticMarketRsArtifactState.NO_CURRENT_ARTIFACT
    )


def test_market_rs_unexpected_failure_is_hard_failure():
    result = {
        "status": "failed",
        "market": "DE",
        "as_of_date": "2026-07-27",
        "formula_version": BALANCED_RS_FORMULA_VERSION,
        "reason_code": "calculation_failed",
        "diagnostics": {"error": "database invariant failed"},
    }

    assert (
        classify_static_market_rs_artifact_result(
            result,
            market="DE",
            as_of_date="2026-07-27",
            formula_version=BALANCED_RS_FORMULA_VERSION,
        )
        is StaticMarketRsArtifactState.HARD_FAILURE
    )


def test_collect_static_no_current_artifact_failures_filters_known_snapshot_reasons():
    failures = collect_static_no_current_artifact_failures(
        {
            "feature_snapshots": {
                "DE": {
                    "status": "skipped",
                    "reason": "market_rs_not_ready",
                    "market": "DE",
                },
                "IN": {
                    "status": "skipped",
                    "reason": "market_exposure_not_ready",
                    "market": "IN",
                },
                "US": {
                    "status": "published",
                    "run_id": 91,
                    "market": "US",
                },
            }
        },
        market=None,
    )

    assert [failure.market for failure in failures] == ["DE", "IN"]
    assert [failure.reason for failure in failures] == [
        "market_rs_not_ready",
        "market_exposure_not_ready",
    ]
