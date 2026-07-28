"""Shared policy for publishing multi-market static site artifacts."""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Any, Mapping

from app.domain.markets import SUPPORTED_MARKET_CODES
from app.domain.relative_strength import LEGACY_RS_FORMULA_VERSION
from app.services.market_rs_inputs import (
    MARKET_RS_REASON_BENCHMARK_ADJUSTED_ANCHOR_MISSING,
    MARKET_RS_REASON_CURRENT_ADJUSTED_PRICE_COVERAGE_BELOW_THRESHOLD,
)

REQUIRED_STATIC_MARKETS = frozenset({"US"})
OPTIONAL_STATIC_MARKETS = frozenset(
    market for market in SUPPORTED_MARKET_CODES if market not in REQUIRED_STATIC_MARKETS
)
STATIC_MARKET_RS_NO_CURRENT_ARTIFACT_REASON_CODES = frozenset(
    {
        MARKET_RS_REASON_BENCHMARK_ADJUSTED_ANCHOR_MISSING,
        MARKET_RS_REASON_CURRENT_ADJUSTED_PRICE_COVERAGE_BELOW_THRESHOLD,
    }
)


class StaticMarketRsArtifactState(Enum):
    READY = "ready"
    NO_CURRENT_ARTIFACT = "no_current_artifact"
    HARD_FAILURE = "hard_failure"


def _expected_date_value(value: date | str | None) -> str | None:
    if isinstance(value, date):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def _matches_expected(
    result: Mapping[str, Any],
    *,
    market: str | None,
    as_of_date: date | str | None,
    formula_version: str | None,
) -> bool:
    expected_market = market.upper() if market is not None else None
    expected_as_of_date = _expected_date_value(as_of_date)
    return (
        (expected_market is None or result.get("market") == expected_market)
        and (
            expected_as_of_date is None
            or result.get("as_of_date") == expected_as_of_date
        )
        and (
            formula_version is None
            or result.get("formula_version") == formula_version
        )
    )


def classify_static_market_rs_artifact_result(
    result: Any,
    *,
    market: str | None = None,
    as_of_date: date | str | None = None,
    formula_version: str | None = None,
) -> StaticMarketRsArtifactState:
    if not isinstance(result, Mapping):
        return StaticMarketRsArtifactState.HARD_FAILURE
    if not _matches_expected(
        result,
        market=market,
        as_of_date=as_of_date,
        formula_version=formula_version,
    ):
        return StaticMarketRsArtifactState.HARD_FAILURE
    if result.get("formula_version") == LEGACY_RS_FORMULA_VERSION:
        if result.get("status") == "selected":
            return StaticMarketRsArtifactState.READY
        return StaticMarketRsArtifactState.HARD_FAILURE
    if result.get("status") == "completed" and result.get("market_rs_run_id") is not None:
        return StaticMarketRsArtifactState.READY
    if (
        result.get("status") == "failed"
        and result.get("reason_code")
        in STATIC_MARKET_RS_NO_CURRENT_ARTIFACT_REASON_CODES
    ):
        return StaticMarketRsArtifactState.NO_CURRENT_ARTIFACT
    return StaticMarketRsArtifactState.HARD_FAILURE
