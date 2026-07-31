"""Background-market runtime bootstrap completion tests."""

from __future__ import annotations

import pytest


def test_background_completion_marks_market_failure_without_global_state(
    monkeypatch,
):
    from app.services.bootstrap_readiness_service import (
        BootstrapReadiness,
        MarketBootstrapReadiness,
    )
    from app.tasks import runtime_bootstrap_tasks as module

    class _FakeSession:
        def close(self):
            pass

    class _FakeReadinessService:
        def evaluate(self, db, *, enabled_markets, bootstrap_started_at=None):
            calls["evaluate"] = (db, enabled_markets, bootstrap_started_at)
            return BootstrapReadiness(
                empty_system=False,
                market_results={
                    "HK": MarketBootstrapReadiness(
                        market="HK",
                        core_ready=True,
                        scan_ready=False,
                    ),
                },
            )

    session = _FakeSession()
    calls = {}
    failed_markets = []

    monkeypatch.setattr(module, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        "app.services.bootstrap_readiness_service.BootstrapReadinessService",
        _FakeReadinessService,
    )
    monkeypatch.setattr(
        "app.services.runtime_preferences_service.get_runtime_preferences",
        lambda _db: type("Prefs", (), {"bootstrap_started_at": "started-at"})(),
    )
    monkeypatch.setattr(
        "app.services.runtime_preferences_service.set_bootstrap_state",
        lambda *_args, **_kwargs: pytest.fail(
            "background completion must not mutate global bootstrap state"
        ),
    )
    monkeypatch.setattr(
        module,
        "mark_market_activity_failed",
        lambda _db, **kwargs: failed_markets.append(kwargs),
    )

    result = module.complete_background_market_bootstrap.run(market="HK")

    assert calls["evaluate"] == (session, ["HK"], "started-at")
    assert result == {
        "status": "failed",
        "market": "HK",
        "reason": "missing published auto scan",
    }
    assert failed_markets == [
        {
            "market": "HK",
            "stage_key": "scan",
            "lifecycle": "bootstrap",
            "task_name": "runtime_bootstrap",
            "task_id": None,
            "message": "Bootstrap scan did not publish",
        }
    ]
