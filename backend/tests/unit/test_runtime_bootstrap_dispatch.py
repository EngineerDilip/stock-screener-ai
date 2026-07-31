"""Runtime bootstrap dispatch and persistence tests."""

from __future__ import annotations

import pytest

from tests.unit.runtime_bootstrap_test_fakes import FakeSignature, FakeTask


@pytest.fixture(autouse=True)
def classify_unit_test_bootstraps_as_non_fresh(monkeypatch):
    from app.tasks import runtime_bootstrap_tasks as module

    monkeypatch.setattr(
        module,
        "_balanced_activation_state_at_dispatch",
        lambda _markets: module.BalancedActivationDispatchState(
            fresh_install=False,
            pending_markets=(),
        ),
    )


def test_queue_local_runtime_bootstrap_splits_primary_and_background_market_chains(
    monkeypatch,
):
    from app.tasks import runtime_bootstrap_tasks as module

    class _FakeAsyncResult:
        def __init__(self, task_id: str) -> None:
            self.id = task_id

    market_chains = []
    applied_chains = []
    recorded_runs = []
    events = []

    class _FakeChain:
        def __init__(self, *signatures) -> None:
            self.signatures = signatures
            market_chains.append([signature.task for signature in signatures])

        def apply_async(self, **kwargs):
            events.append(("apply", self.signatures[0].task))
            applied_chains.append(
                {
                    "tasks": [signature.task for signature in self.signatures],
                    "errback": kwargs.get("link_error"),
                }
            )
            return _FakeAsyncResult(
                "primary-task-123"
                if len(applied_chains) == 1
                else f"background-task-{len(applied_chains)}"
            )

    monkeypatch.setattr(
        module,
        "chain",
        lambda *signatures: _FakeChain(*signatures),
    )
    monkeypatch.setattr(
        module,
        "complete_local_runtime_bootstrap",
        FakeTask("app.tasks.runtime_bootstrap_tasks.complete_local_runtime_bootstrap"),
    )
    monkeypatch.setattr(
        module,
        "complete_background_market_bootstrap",
        FakeTask(
            "app.tasks.runtime_bootstrap_tasks.complete_background_market_bootstrap"
        ),
    )
    monkeypatch.setattr(
        module,
        "fail_local_runtime_bootstrap",
        FakeTask("app.tasks.runtime_bootstrap_tasks.fail_local_runtime_bootstrap"),
    )
    monkeypatch.setattr(
        module,
        "fail_background_market_bootstrap",
        FakeTask("app.tasks.runtime_bootstrap_tasks.fail_background_market_bootstrap"),
    )
    monkeypatch.setattr(
        module,
        "_build_market_bootstrap_signatures",
        lambda market_plan: [FakeSignature(f"task:{market_plan.market}")],
    )
    monkeypatch.setattr(
        module,
        "record_runtime_bootstrap_run",
        lambda *, primary_market, enabled_markets, dispatch_id, fresh_install, primary_task_id, market_task_ids, queue_state: (
            events.append(
                ("record", queue_state, primary_task_id, dict(market_task_ids))
            ),
            recorded_runs.append(
                {
                    "primary_market": primary_market,
                    "enabled_markets": tuple(enabled_markets),
                    "fresh_install": fresh_install,
                    "primary_task_id": primary_task_id,
                    "market_task_ids": dict(market_task_ids),
                    "queue_state": queue_state,
                }
            ),
        ),
    )

    result = module.queue_local_runtime_bootstrap(
        primary_market="US",
        enabled_markets=["HK", "US", "TW"],
    )

    assert result == "primary-task-123"
    assert events[0] == ("record", "queueing", None, {})
    assert events[-1] == (
        "record",
        "queued",
        "primary-task-123",
        {
            "US": "primary-task-123",
            "HK": "background-task-2",
            "TW": "background-task-3",
        },
    )
    assert market_chains == [
        [
            "task:US",
            "app.tasks.runtime_bootstrap_tasks.complete_local_runtime_bootstrap",
        ],
        [
            "task:HK",
            "app.tasks.runtime_bootstrap_tasks.complete_background_market_bootstrap",
        ],
        [
            "task:TW",
            "app.tasks.runtime_bootstrap_tasks.complete_background_market_bootstrap",
        ],
    ]
    assert (
        applied_chains[0]["errback"].task
        == "app.tasks.runtime_bootstrap_tasks.fail_local_runtime_bootstrap"
    )
    assert applied_chains[0]["errback"].kwargs == {
        "primary_market": "US",
        "dispatch_id": applied_chains[0]["errback"].kwargs["dispatch_id"],
    }
    dispatch_id = applied_chains[0]["errback"].kwargs["dispatch_id"]
    assert dispatch_id
    assert [call["errback"].task for call in applied_chains[1:]] == [
        "app.tasks.runtime_bootstrap_tasks.fail_background_market_bootstrap",
        "app.tasks.runtime_bootstrap_tasks.fail_background_market_bootstrap",
    ]
    assert [call["errback"].kwargs["dispatch_id"] for call in applied_chains[1:]] == [
        dispatch_id,
        dispatch_id,
    ]
    assert recorded_runs == [
        {
            "primary_market": "US",
            "enabled_markets": ("US", "HK", "TW"),
            "fresh_install": False,
            "primary_task_id": None,
            "market_task_ids": {},
            "queue_state": "queueing",
        },
        {
            "primary_market": "US",
            "enabled_markets": ("US", "HK", "TW"),
            "fresh_install": False,
            "primary_task_id": "primary-task-123",
            "market_task_ids": {
                "US": "primary-task-123",
            },
            "queue_state": "partial",
        },
        {
            "primary_market": "US",
            "enabled_markets": ("US", "HK", "TW"),
            "fresh_install": False,
            "primary_task_id": "primary-task-123",
            "market_task_ids": {
                "US": "primary-task-123",
                "HK": "background-task-2",
            },
            "queue_state": "partial",
        },
        {
            "primary_market": "US",
            "enabled_markets": ("US", "HK", "TW"),
            "fresh_install": False,
            "primary_task_id": "primary-task-123",
            "market_task_ids": {
                "US": "primary-task-123",
                "HK": "background-task-2",
                "TW": "background-task-3",
            },
            "queue_state": "partial",
        },
        {
            "primary_market": "US",
            "enabled_markets": ("US", "HK", "TW"),
            "fresh_install": False,
            "primary_task_id": "primary-task-123",
            "market_task_ids": {
                "US": "primary-task-123",
                "HK": "background-task-2",
                "TW": "background-task-3",
            },
            "queue_state": "queued",
        },
    ]


def test_queue_local_runtime_bootstrap_does_not_dispatch_when_initial_manifest_fails(
    monkeypatch,
):
    from app.tasks import runtime_bootstrap_tasks as module

    applied = []

    class _FakeAsyncResult:
        def __init__(self, task_id: str) -> None:
            self.id = task_id

    def _queue(market_plan, **_kwargs):
        applied.append(market_plan.market)
        return _FakeAsyncResult(f"task-{market_plan.market.lower()}")

    monkeypatch.setattr(module, "_queue_market_bootstrap_workflow", _queue)
    monkeypatch.setattr(
        module,
        "record_runtime_bootstrap_run",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("manifest write failed")),
    )

    with pytest.raises(RuntimeError, match="manifest write failed"):
        module.queue_local_runtime_bootstrap(
            primary_market="US",
            enabled_markets=["US", "HK"],
        )

    assert applied == []


def test_queue_local_runtime_bootstrap_logs_late_manifest_update_failure(monkeypatch):
    from app.tasks import runtime_bootstrap_tasks as module

    class _FakeAsyncResult:
        def __init__(self, task_id: str) -> None:
            self.id = task_id

    recorded_runs = []

    def _queue(market_plan, **_kwargs):
        return _FakeAsyncResult(f"task-{market_plan.market.lower()}")

    def _record(
        *,
        primary_market,
        enabled_markets,
        dispatch_id,
        fresh_install,
        primary_task_id,
        market_task_ids,
        queue_state,
    ):
        recorded_runs.append(
            {
                "primary_market": primary_market,
                "enabled_markets": tuple(enabled_markets),
                "fresh_install": fresh_install,
                "primary_task_id": primary_task_id,
                "market_task_ids": dict(market_task_ids),
                "queue_state": queue_state,
            }
        )
        if queue_state == "queued":
            raise RuntimeError("late manifest write failed")

    monkeypatch.setattr(module, "_queue_market_bootstrap_workflow", _queue)
    monkeypatch.setattr(module, "record_runtime_bootstrap_run", _record)

    result = module.queue_local_runtime_bootstrap(
        primary_market="US",
        enabled_markets=["US", "HK"],
    )

    assert result == "task-us"
    assert recorded_runs == [
        {
            "primary_market": "US",
            "enabled_markets": ("US", "HK"),
            "fresh_install": False,
            "primary_task_id": None,
            "market_task_ids": {},
            "queue_state": "queueing",
        },
        {
            "primary_market": "US",
            "enabled_markets": ("US", "HK"),
            "fresh_install": False,
            "primary_task_id": "task-us",
            "market_task_ids": {"US": "task-us"},
            "queue_state": "partial",
        },
        {
            "primary_market": "US",
            "enabled_markets": ("US", "HK"),
            "fresh_install": False,
            "primary_task_id": "task-us",
            "market_task_ids": {"US": "task-us", "HK": "task-hk"},
            "queue_state": "partial",
        },
        {
            "primary_market": "US",
            "enabled_markets": ("US", "HK"),
            "fresh_install": False,
            "primary_task_id": "task-us",
            "market_task_ids": {"US": "task-us", "HK": "task-hk"},
            "queue_state": "queued",
        },
    ]


def test_queue_local_runtime_bootstrap_records_partial_manifest_when_background_queue_fails(
    monkeypatch,
):
    from app.tasks import runtime_bootstrap_tasks as module

    class _FakeAsyncResult:
        def __init__(self, task_id: str) -> None:
            self.id = task_id

    recorded_runs = []

    def _queue(market_plan, **_kwargs):
        if market_plan.market == "US":
            return _FakeAsyncResult("primary-task-123")
        raise RuntimeError(f"queue failed for {market_plan.market}")

    monkeypatch.setattr(module, "_queue_market_bootstrap_workflow", _queue)
    monkeypatch.setattr(
        module,
        "record_runtime_bootstrap_run",
        lambda *, primary_market, enabled_markets, dispatch_id, fresh_install, primary_task_id, market_task_ids, queue_state: (
            recorded_runs.append(
                {
                    "primary_market": primary_market,
                    "enabled_markets": tuple(enabled_markets),
                    "fresh_install": fresh_install,
                    "primary_task_id": primary_task_id,
                    "market_task_ids": dict(market_task_ids),
                    "queue_state": queue_state,
                }
            )
        ),
    )

    with pytest.raises(RuntimeError, match="queue failed for HK"):
        module.queue_local_runtime_bootstrap(
            primary_market="US",
            enabled_markets=["US", "HK"],
        )

    assert recorded_runs == [
        {
            "primary_market": "US",
            "enabled_markets": ("US", "HK"),
            "fresh_install": False,
            "primary_task_id": None,
            "market_task_ids": {},
            "queue_state": "queueing",
        },
        {
            "primary_market": "US",
            "enabled_markets": ("US", "HK"),
            "fresh_install": False,
            "primary_task_id": "primary-task-123",
            "market_task_ids": {"US": "primary-task-123"},
            "queue_state": "partial",
        },
        {
            "primary_market": "US",
            "enabled_markets": ("US", "HK"),
            "fresh_install": False,
            "primary_task_id": "primary-task-123",
            "market_task_ids": {"US": "primary-task-123"},
            "queue_state": "dispatch_failed",
        },
    ]


def test_queue_local_runtime_bootstrap_surfaces_manifest_recording_failure(monkeypatch):
    from app.tasks import runtime_bootstrap_tasks as module

    class _FakeAsyncResult:
        def __init__(self, task_id: str) -> None:
            self.id = task_id

    def _queue(market_plan, **_kwargs):
        return _FakeAsyncResult(f"task-{market_plan.market.lower()}")

    monkeypatch.setattr(module, "_queue_market_bootstrap_workflow", _queue)
    monkeypatch.setattr(
        module,
        "record_runtime_bootstrap_run",
        lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("manifest write failed")),
    )

    with pytest.raises(RuntimeError, match="manifest write failed"):
        module.queue_local_runtime_bootstrap(
            primary_market="US",
            enabled_markets=["US", "HK"],
        )


def test_apply_bootstrap_workflow_does_not_retry_without_errback_on_type_error():
    from app.tasks import runtime_bootstrap_tasks as module

    calls = []

    class _BrokenWorkflow:
        def apply_async(self, **kwargs):
            calls.append(kwargs)
            raise TypeError("bad celery signature")

    errback = object()

    with pytest.raises(TypeError, match="bad celery signature"):
        module._apply_bootstrap_workflow(_BrokenWorkflow(), errback)

    assert calls == [{"link_error": errback}]
