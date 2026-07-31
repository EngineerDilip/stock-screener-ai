"""Fresh-install balanced Market RS bootstrap lifecycle tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.domain.relative_strength import BALANCED_RS_FORMULA_VERSION
from app.services.bootstrap_run_manifest import BootstrapRunManifest


def test_fresh_dispatch_identity_survives_partial_bootstrap(monkeypatch) -> None:
    from app.tasks import runtime_bootstrap_tasks as module

    db = MagicMock()
    manifest_repository = MagicMock()
    manifest_repository.load.return_value = SimpleNamespace(
        fresh_install=True,
        pending_balanced_activation_markets=("US",),
    )
    readiness = MagicMock()
    readiness.is_pristine_installation.return_value = False
    monkeypatch.setattr(module, "SessionLocal", lambda: db)
    monkeypatch.setattr(
        module,
        "BootstrapRunManifestRepository",
        lambda: manifest_repository,
    )
    monkeypatch.setattr(
        "app.services.bootstrap_readiness_service.BootstrapReadinessService",
        lambda: readiness,
    )

    state = module._balanced_activation_state_at_dispatch(("US",))

    assert state.fresh_install is True
    assert state.pending_markets == ("US",)
    manifest_repository.load.assert_called_once_with(db)
    readiness.is_pristine_installation.assert_not_called()
    db.close.assert_called_once_with()


def test_completed_market_is_removed_from_pending_activation_set() -> None:
    from app.services.fresh_balanced_rs_bootstrap_lifecycle import (
        FreshBalancedRsBootstrapLifecycle,
    )

    manifest_repository = MagicMock()
    manifest_repository.load.return_value = BootstrapRunManifest.create(
        primary_market="US",
        enabled_markets=("US", "HK"),
        fresh_install=True,
        dispatch_id="dispatch-a",
    )
    formula_repository = MagicMock()
    formula_repository.active_formula.return_value = BALANCED_RS_FORMULA_VERSION
    lifecycle = FreshBalancedRsBootstrapLifecycle(
        manifest_repository=manifest_repository,
        formula_repository=formula_repository,
    )
    db = MagicMock()

    updated_manifests = []

    def _update(_db, *, dispatch_id, transform):
        assert dispatch_id == "dispatch-a"
        updated = transform(manifest_repository.load.return_value)
        updated_manifests.append(updated)
        return updated

    manifest_repository.update_dispatch.side_effect = _update

    assert lifecycle.complete_market(db, market="US", dispatch_id="dispatch-a")

    updated = updated_manifests[0]
    assert updated.fresh_install is True
    assert updated.pending_balanced_activation_markets == ("HK",)
    formula_repository.active_formula.assert_called_once_with(db, market="US")


def test_market_remains_pending_when_balanced_formula_is_not_active() -> None:
    from app.services.fresh_balanced_rs_bootstrap_lifecycle import (
        FreshBalancedRsBootstrapLifecycle,
    )

    manifest_repository = MagicMock()
    manifest_repository.load.return_value = BootstrapRunManifest.create(
        primary_market="US",
        enabled_markets=("US", "HK"),
        fresh_install=True,
    )
    formula_repository = MagicMock()
    formula_repository.active_formula.return_value = "legacy-linear-v1"
    lifecycle = FreshBalancedRsBootstrapLifecycle(
        manifest_repository=manifest_repository,
        formula_repository=formula_repository,
    )

    assert (
        lifecycle.complete_market(MagicMock(), market="HK", dispatch_id="dispatch-a")
        is False
    )
    manifest_repository.update_dispatch.assert_not_called()


@pytest.mark.parametrize("fresh_install", [True, False])
def test_queue_bootstrap_captures_pristine_installation_once(
    monkeypatch,
    fresh_install,
):
    from app.tasks import runtime_bootstrap_tasks as module

    class _FakeAsyncResult:
        def __init__(self, task_id: str) -> None:
            self.id = task_id

    classifications = []
    saved = []
    queued_operations = []
    completion_payloads = []

    def _classify(_markets):
        classifications.append(fresh_install)
        return module.BalancedActivationDispatchState(
            fresh_install=fresh_install,
            pending_markets=("US", "HK") if fresh_install else (),
        )

    def _queue(market_plan, **kwargs):
        queued_operations.append([stage.operation for stage in market_plan.stages])
        completion_payloads.append(dict(kwargs["completion_kwargs"]))
        return _FakeAsyncResult(f"task-{market_plan.market.lower()}")

    monkeypatch.setattr(module, "_balanced_activation_state_at_dispatch", _classify)
    monkeypatch.setattr(
        module,
        "record_runtime_bootstrap_run",
        lambda **payload: saved.append(payload) or payload,
    )
    monkeypatch.setattr(module, "_queue_market_bootstrap_workflow", _queue)

    module.queue_local_runtime_bootstrap(
        primary_market="US",
        enabled_markets=("US", "HK"),
    )

    assert classifications == [fresh_install]
    assert saved
    assert {record["fresh_install"] for record in saved} == {fresh_install}
    expected_operation = (
        module.BootstrapOperation.BOOTSTRAP_BALANCED_MARKET_RS
        if fresh_install
        else module.BootstrapOperation.CALCULATE_MARKET_RS_SNAPSHOT
    )
    assert queued_operations
    assert all(expected_operation in operations for operations in queued_operations)
    if fresh_install:
        assert all(
            payload["expected_formula_version"] == BALANCED_RS_FORMULA_VERSION
            for payload in completion_payloads
        )
    else:
        assert all(
            "expected_formula_version" not in payload for payload in completion_payloads
        )


def test_partial_retry_activates_only_the_pending_market(monkeypatch) -> None:
    from app.tasks import runtime_bootstrap_tasks as module

    class _FakeAsyncResult:
        def __init__(self, task_id: str) -> None:
            self.id = task_id

    queued = {}
    recorded = []

    monkeypatch.setattr(
        module,
        "_balanced_activation_state_at_dispatch",
        lambda _markets: module.BalancedActivationDispatchState(
            fresh_install=True,
            pending_markets=("HK",),
        ),
    )
    monkeypatch.setattr(
        module,
        "record_runtime_bootstrap_run",
        lambda **payload: recorded.append(payload) or payload,
    )

    def _queue(market_plan, **kwargs):
        queued[market_plan.market] = {
            "operations": tuple(stage.operation for stage in market_plan.stages),
            "completion": dict(kwargs["completion_kwargs"]),
        }
        return _FakeAsyncResult(f"task-{market_plan.market.lower()}")

    monkeypatch.setattr(module, "_queue_market_bootstrap_workflow", _queue)

    module.queue_local_runtime_bootstrap(
        primary_market="US",
        enabled_markets=("US", "HK"),
    )

    assert (
        module.BootstrapOperation.CALCULATE_MARKET_RS_SNAPSHOT
        in queued["US"]["operations"]
    )
    assert "expected_formula_version" not in queued["US"]["completion"]
    assert (
        module.BootstrapOperation.BOOTSTRAP_BALANCED_MARKET_RS
        in queued["HK"]["operations"]
    )
    assert (
        queued["HK"]["completion"]["expected_formula_version"]
        == BALANCED_RS_FORMULA_VERSION
    )
    assert recorded[0]["pending_balanced_activation_markets"] == ("HK",)


def test_fresh_bootstrap_signature_routes_activation_to_market_queue() -> None:
    from app.domain.bootstrap.plan import build_bootstrap_plan
    from app.tasks.runtime_bootstrap_tasks import _build_market_bootstrap_signatures

    market_plan = build_bootstrap_plan(
        primary_market="HK",
        enabled_markets=("HK",),
        balanced_activation_markets=("HK",),
    ).market_plans[0]

    signatures = _build_market_bootstrap_signatures(market_plan)
    activation = next(
        signature
        for signature in signatures
        if signature.task == "app.tasks.market_rs_tasks.bootstrap_balanced_market_rs"
    )

    assert activation.kwargs == {
        "market": "HK",
        "activity_lifecycle": "bootstrap",
    }
    assert activation.options["queue"] == "market_jobs_hk"


@pytest.mark.parametrize(
    ("completion_task_name", "task_kwargs", "expected_result"),
    [
        (
            "complete_local_runtime_bootstrap",
            {"primary_market": "US"},
            {
                "status": "failed",
                "primary_market": "US",
                "market": "US",
                "reason": "balanced market rs formula not active",
            },
        ),
        (
            "complete_background_market_bootstrap",
            {"market": "US"},
            {
                "status": "failed",
                "market": "US",
                "reason": "balanced market rs formula not active",
            },
        ),
    ],
)
def test_fresh_bootstrap_completion_rejects_legacy_formula_pointer(
    monkeypatch,
    completion_task_name,
    task_kwargs,
    expected_result,
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
        def evaluate(
            self,
            db,
            *,
            enabled_markets,
            bootstrap_started_at=None,
            expected_formula_versions=None,
        ):
            calls["expectations"] = expected_formula_versions
            return BootstrapReadiness(
                empty_system=False,
                market_results={
                    "US": MarketBootstrapReadiness(
                        market="US",
                        core_ready=True,
                        scan_ready=True,
                        rs_ready=False,
                    )
                },
            )

    calls = {}
    bootstrap_states = []
    failed_markets = []
    monkeypatch.setattr(module, "SessionLocal", lambda: _FakeSession())
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
        lambda _db, state: bootstrap_states.append(state),
    )
    monkeypatch.setattr(
        module,
        "mark_market_activity_failed",
        lambda _db, **kwargs: failed_markets.append(kwargs),
    )

    task = getattr(module, completion_task_name)
    result = task.run(
        **task_kwargs,
        expected_formula_version=BALANCED_RS_FORMULA_VERSION,
    )

    assert result == expected_result
    assert calls["expectations"] == {
        "US": BALANCED_RS_FORMULA_VERSION,
    }
    assert failed_markets[0]["stage_key"] == "market_rs"
    assert failed_markets[0]["message"] == ("Balanced Market RS activation incomplete")
    if completion_task_name == "complete_local_runtime_bootstrap":
        assert bootstrap_states == ["failed"]
    else:
        assert bootstrap_states == []
