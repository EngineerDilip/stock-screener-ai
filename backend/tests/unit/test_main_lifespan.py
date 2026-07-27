from __future__ import annotations

from unittest.mock import Mock

import pytest


@pytest.mark.asyncio
async def test_group_history_startup_trigger_runs_reconciliation_off_event_loop(
    monkeypatch,
):
    from app import main as module

    queued = Mock(return_value={"US": "queued"})
    calls = []

    async def fake_to_thread(function):
        calls.append(function)
        return function()

    monkeypatch.setattr(
        "app.tasks.group_history_tasks.queue_group_history_reconciliation",
        queued,
    )
    monkeypatch.setattr(module.asyncio, "to_thread", fake_to_thread)

    result = await module.trigger_group_history_reconciliation_on_startup()

    assert result == {"US": "queued"}
    assert calls == [queued]
    queued.assert_called_once_with()
