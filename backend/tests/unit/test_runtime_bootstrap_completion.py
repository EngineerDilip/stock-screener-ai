"""Runtime bootstrap completion and failure tests."""

from __future__ import annotations


def test_fail_local_runtime_bootstrap_preserves_active_task_owner(monkeypatch):
    from app.tasks import runtime_bootstrap_tasks as module

    class _FakeSession:
        def close(self):
            pass

    calls = []

    monkeypatch.setattr(module, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(
        module,
        "_is_current_bootstrap_dispatch",
        lambda _db, *, dispatch_id: dispatch_id == "dispatch-current",
    )
    monkeypatch.setattr(
        module, "_finish_bootstrap_market", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        "app.services.runtime_preferences_service.set_bootstrap_state",
        lambda _db, _state: None,
    )
    monkeypatch.setattr(
        module,
        "mark_current_market_activity_failed",
        lambda _db, **kwargs: calls.append(kwargs),
    )

    result = module.fail_local_runtime_bootstrap.run(
        primary_market="US",
        dispatch_id="dispatch-current",
    )

    assert result["status"] == "failed"
    assert calls == [
        {
            "market": "US",
            "lifecycle": "bootstrap",
            "message": "Bootstrap failed",
        }
    ]


def test_stale_primary_completion_has_no_readiness_or_state_side_effects(monkeypatch):
    from app.tasks import runtime_bootstrap_tasks as module

    class _FakeSession:
        def close(self):
            pass

    monkeypatch.setattr(module, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(
        module,
        "_is_current_bootstrap_dispatch",
        lambda _db, *, dispatch_id: False,
    )
    monkeypatch.setattr(
        module,
        "_evaluate_market_readiness",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("stale callback evaluated readiness")
        ),
    )

    result = module.complete_local_runtime_bootstrap.run(
        primary_market="US",
        dispatch_id="dispatch-old",
    )

    assert result == {
        "status": "stale",
        "primary_market": "US",
        "market": "US",
    }


def test_stale_primary_errback_has_no_activity_or_state_side_effects(monkeypatch):
    from app.tasks import runtime_bootstrap_tasks as module

    class _FakeSession:
        def close(self):
            pass

    monkeypatch.setattr(module, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(
        module,
        "_is_current_bootstrap_dispatch",
        lambda _db, *, dispatch_id: False,
    )
    monkeypatch.setattr(
        module,
        "mark_current_market_activity_failed",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("stale callback changed activity")
        ),
    )

    result = module.fail_local_runtime_bootstrap.run(
        primary_market="US",
        dispatch_id="dispatch-old",
    )

    assert result == {
        "status": "stale",
        "primary_market": "US",
        "market": "US",
    }


def test_complete_local_runtime_bootstrap_evaluates_only_primary_market(monkeypatch):
    from app.services.bootstrap_readiness_service import (
        BootstrapReadiness,
        MarketBootstrapReadiness,
    )
    from app.tasks import runtime_bootstrap_tasks as module

    class _FakeSession:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    class _FakeReadinessService:
        def evaluate(self, db, *, enabled_markets, bootstrap_started_at=None):
            calls["evaluate"] = (db, enabled_markets, bootstrap_started_at)
            return BootstrapReadiness(
                empty_system=False,
                market_results={
                    "US": MarketBootstrapReadiness(
                        market="US",
                        core_ready=True,
                        scan_ready=True,
                    ),
                },
            )

    session = _FakeSession()
    calls = {}
    failed_markets = []

    monkeypatch.setattr(module, "SessionLocal", lambda: session)
    monkeypatch.setattr(
        module, "_is_current_bootstrap_dispatch", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        module, "_finish_bootstrap_market", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        "app.services.bootstrap_readiness_service.BootstrapReadinessService",
        _FakeReadinessService,
    )
    monkeypatch.setattr(
        "app.services.runtime_preferences_service.set_bootstrap_state",
        lambda db, state: calls.setdefault("set_bootstrap_state", (db, state)),
    )
    monkeypatch.setattr(
        "app.services.runtime_preferences_service.get_runtime_preferences",
        lambda _db: type(
            "Prefs",
            (),
            {"bootstrap_started_at": "bootstrap-started-at"},
        )(),
    )
    monkeypatch.setattr(
        module,
        "mark_market_activity_failed",
        lambda _db, **kwargs: failed_markets.append(kwargs),
    )

    result = module.complete_local_runtime_bootstrap.run(
        primary_market="US", dispatch_id="dispatch-current"
    )

    assert calls["evaluate"] == (session, ["US"], "bootstrap-started-at")
    assert calls["set_bootstrap_state"] == (session, "ready")
    assert result == {
        "status": "ready",
        "primary_market": "US",
        "market": "US",
    }
    assert failed_markets == []
    assert session.closed is True


def test_complete_local_runtime_bootstrap_reports_primary_readiness_failure(
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
            return BootstrapReadiness(
                empty_system=False,
                market_results={
                    "HK": MarketBootstrapReadiness(
                        market="HK",
                        core_ready=False,
                        scan_ready=False,
                    ),
                },
            )

    failed_markets = []

    monkeypatch.setattr(module, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(
        module, "_is_current_bootstrap_dispatch", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        module, "_finish_bootstrap_market", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        "app.services.bootstrap_readiness_service.BootstrapReadinessService",
        _FakeReadinessService,
    )
    monkeypatch.setattr(
        "app.services.runtime_preferences_service.get_runtime_preferences",
        lambda _db: type("Prefs", (), {"bootstrap_started_at": None})(),
    )
    monkeypatch.setattr(
        "app.services.runtime_preferences_service.set_bootstrap_state",
        lambda _db, _state: None,
    )
    monkeypatch.setattr(
        module,
        "mark_market_activity_failed",
        lambda _db, **kwargs: failed_markets.append(kwargs),
    )

    result = module.complete_local_runtime_bootstrap.run(
        primary_market="HK", dispatch_id="dispatch-current"
    )

    assert result == {
        "status": "failed",
        "primary_market": "HK",
        "market": "HK",
        "reason": "missing core market data",
    }
    assert failed_markets == [
        {
            "market": "HK",
            "stage_key": "core",
            "lifecycle": "bootstrap",
            "task_name": "runtime_bootstrap",
            "task_id": None,
            "message": "Bootstrap core data incomplete",
        },
    ]


def test_complete_local_runtime_bootstrap_uses_requested_market_readiness(monkeypatch):
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
                        core_ready=False,
                        scan_ready=False,
                    ),
                    "US": MarketBootstrapReadiness(
                        market="US",
                        core_ready=True,
                        scan_ready=True,
                    ),
                },
            )

    calls = {}

    monkeypatch.setattr(module, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(
        module, "_is_current_bootstrap_dispatch", lambda *_args, **_kwargs: True
    )
    monkeypatch.setattr(
        module, "_finish_bootstrap_market", lambda *_args, **_kwargs: True
    )
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
        lambda db, state: calls.setdefault("set_bootstrap_state", (db, state)),
    )
    monkeypatch.setattr(
        module,
        "mark_market_activity_failed",
        lambda _db, **kwargs: calls.setdefault("mark_failed", kwargs),
    )

    result = module.complete_local_runtime_bootstrap.run(
        primary_market="us", dispatch_id="dispatch-current"
    )

    assert calls["evaluate"][1] == ["US"]
    assert calls["set_bootstrap_state"][1] == "ready"
    assert "mark_failed" not in calls
    assert result == {
        "status": "ready",
        "primary_market": "US",
        "market": "US",
    }
