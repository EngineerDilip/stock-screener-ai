from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock


def test_group_history_startup_trigger_only_dispatches_celery_discovery(monkeypatch):
    from app import main as module

    discovery = Mock()
    discovery.delay.return_value = SimpleNamespace(id="discovery-1")

    monkeypatch.setattr(
        "app.tasks.group_history_tasks.discover_group_history_reconciliation",
        discovery,
    )

    result = module.trigger_group_history_reconciliation_on_startup()

    assert result == {"status": "queued", "task_id": "discovery-1"}
    discovery.delay.assert_called_once_with()
