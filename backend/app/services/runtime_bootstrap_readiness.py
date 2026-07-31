"""Readiness evaluation for one completed runtime bootstrap workflow."""

from __future__ import annotations

from dataclasses import dataclass

from app.tasks.market_queues import normalize_market


@dataclass(frozen=True)
class ReadinessFailure:
    stage_key: str
    activity_message: str
    result_reason: str


@dataclass(frozen=True)
class MarketReadinessCompletion:
    market: str
    ready: bool
    failure: ReadinessFailure | None = None


def readiness_failure(market_result) -> ReadinessFailure:
    if market_result is None or not market_result.core_ready:
        return ReadinessFailure(
            stage_key="core",
            activity_message="Bootstrap core data incomplete",
            result_reason="missing core market data",
        )
    if not market_result.scan_ready:
        return ReadinessFailure(
            stage_key="scan",
            activity_message="Bootstrap scan did not publish",
            result_reason="missing published auto scan",
        )
    return ReadinessFailure(
        stage_key="market_rs",
        activity_message="Balanced Market RS activation incomplete",
        result_reason="balanced market rs formula not active",
    )


def evaluate_market_readiness(
    db,
    *,
    market: str,
    bootstrap_started_at=None,
    expected_formula_version: str | None = None,
) -> MarketReadinessCompletion:
    from app.services.bootstrap_readiness_service import BootstrapReadinessService

    market_code = normalize_market(market)
    readiness_kwargs = {
        "enabled_markets": [market_code],
        "bootstrap_started_at": bootstrap_started_at,
    }
    if expected_formula_version is not None:
        readiness_kwargs["expected_formula_versions"] = {
            market_code: expected_formula_version,
        }
    readiness = BootstrapReadinessService().evaluate(db, **readiness_kwargs)
    market_result = readiness.market_results.get(market_code)
    result_market = market_result.market if market_result else market_code
    if market_result and market_result.ready:
        return MarketReadinessCompletion(market=result_market, ready=True)
    return MarketReadinessCompletion(
        market=result_market,
        ready=False,
        failure=readiness_failure(market_result),
    )


__all__ = [
    "MarketReadinessCompletion",
    "ReadinessFailure",
    "evaluate_market_readiness",
    "readiness_failure",
]
