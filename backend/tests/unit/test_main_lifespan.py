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
