from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock


def test_group_history_startup_trigger_dispatches_outside_event_loop(monkeypatch):
    from app import main as module

    discovery = Mock()
    discovery.delay.return_value = SimpleNamespace(id="discovery-1")

    monkeypatch.setattr(
        "app.tasks.group_history_tasks.discover_group_history_reconciliation",
        discovery,
    )

    to_thread = Mock(side_effect=lambda fn: asyncio.sleep(0, result=fn()))
    monkeypatch.setattr(module.asyncio, "to_thread", to_thread)

    result = asyncio.run(module.trigger_group_history_reconciliation_on_startup())

    assert result == {"status": "queued", "task_id": "discovery-1"}
    to_thread.assert_called_once_with(discovery.delay)
    discovery.delay.assert_called_once_with()


def test_lifespan_does_not_await_group_history_dispatch(monkeypatch):
    from app import main as module

    dispatch_started = asyncio.Event()
    keep_dispatch_pending = asyncio.Event()

    async def pending_dispatch():
        dispatch_started.set()
        await keep_dispatch_pending.wait()

    monkeypatch.setattr(module, "initialize_runtime", Mock())
    monkeypatch.setattr(
        module,
        "initialize_process_runtime_services",
        Mock(return_value=object()),
    )
    monkeypatch.setattr(module, "clear_runtime_services", Mock())
    monkeypatch.setattr(
        module,
        "trigger_group_history_reconciliation_on_startup",
        pending_dispatch,
    )
    monkeypatch.setattr(module.settings, "mcp_http_enabled", False)
    monkeypatch.setattr(module.engine, "dispose", Mock())
    test_app = SimpleNamespace(state=SimpleNamespace())

    async def run_lifespan():
        async with module.lifespan(test_app):
            await asyncio.wait_for(dispatch_started.wait(), timeout=0.1)

    asyncio.run(asyncio.wait_for(run_lifespan(), timeout=0.2))
