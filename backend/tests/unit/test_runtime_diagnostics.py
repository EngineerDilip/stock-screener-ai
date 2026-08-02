from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


class _FakeLogger:
    def __init__(self) -> None:
        self.messages = []

    def info(self, message, *args, **kwargs):
        self.messages.append({"message": message, "args": args, "kwargs": kwargs})


def test_log_runtime_stage_emits_start_and_finish(monkeypatch):
    import app.services.runtime_diagnostics as module

    logger = _FakeLogger()
    monkeypatch.setattr(module.time, "perf_counter", iter([10.0, 12.5]).__next__)
    monkeypatch.setattr(module, "_max_rss_mb", lambda: 128.0)

    with module.log_runtime_stage(
        logger,
        "price_refresh.load_universe",
        market="US",
        mode="delta",
    ):
        pass

    assert logger.messages == [
        {
            "message": "Runtime stage started: %s",
            "args": ("price_refresh.load_universe",),
            "kwargs": {
                "extra": {
                    "runtime_stage": "price_refresh.load_universe",
                    "market": "US",
                    "mode": "delta",
                }
            },
        },
        {
            "message": "Runtime stage finished: %s",
            "args": ("price_refresh.load_universe",),
            "kwargs": {
                "extra": {
                    "runtime_stage": "price_refresh.load_universe",
                    "elapsed_seconds": 2.5,
                    "max_rss_mb": 128.0,
                    "market": "US",
                    "mode": "delta",
                }
            },
        },
    ]


def test_log_runtime_stage_emits_failure_and_reraises(monkeypatch):
    import app.services.runtime_diagnostics as module

    logger = _FakeLogger()
    monkeypatch.setattr(module.time, "perf_counter", iter([20.0, 22.3456]).__next__)
    monkeypatch.setattr(module, "_max_rss_mb", lambda: 256.0)

    with pytest.raises(RuntimeError, match="planner failed"):
        with module.log_runtime_stage(
            logger,
            "price_refresh.classify_coverage",
            market="US",
            mode="delta",
        ):
            raise RuntimeError("planner failed")

    assert len(logger.messages) == 2
    assert logger.messages[0]["message"] == "Runtime stage started: %s"
    assert logger.messages[1] == {
        "message": "Runtime stage failed: %s",
        "args": ("price_refresh.classify_coverage",),
        "kwargs": {
            "extra": {
                "runtime_stage": "price_refresh.classify_coverage",
                "elapsed_seconds": 2.346,
                "max_rss_mb": 256.0,
                "exception_type": "RuntimeError",
                "market": "US",
                "mode": "delta",
            },
            "exc_info": True,
        },
    }


def test_max_rss_mb_converts_macos_bytes(monkeypatch):
    import app.services.runtime_diagnostics as module

    monkeypatch.setattr(module.sys, "platform", "darwin")
    monkeypatch.setattr(
        module.resource,
        "getrusage",
        lambda _who: SimpleNamespace(ru_maxrss=128 * 1024 * 1024),
    )

    assert module._max_rss_mb() == 128.0


def test_max_rss_mb_converts_linux_kilobytes(monkeypatch):
    import app.services.runtime_diagnostics as module

    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(
        module.resource,
        "getrusage",
        lambda _who: SimpleNamespace(ru_maxrss=128 * 1024),
    )

    assert module._max_rss_mb() == 128.0


def test_release_session_memory_rolls_back_clean_open_transaction(monkeypatch):
    import app.services.runtime_diagnostics as module

    db = MagicMock()
    db.in_transaction.return_value = True
    db.new = set()
    db.dirty = set()
    db.deleted = set()
    monkeypatch.setattr(module.gc, "collect", MagicMock())

    module.release_session_memory(db, stage="validation", end_transaction=True)

    db.rollback.assert_called_once_with()
    db.expire_all.assert_called_once_with()
    db.expunge_all.assert_called_once_with()
    module.gc.collect.assert_called_once_with()


def test_release_session_memory_rejects_open_transaction_with_pending_writes(
    monkeypatch,
):
    import app.services.runtime_diagnostics as module

    db = MagicMock()
    db.in_transaction.return_value = True
    db.new = {"pending row"}
    db.dirty = set()
    db.deleted = set()
    monkeypatch.setattr(module.gc, "collect", MagicMock())

    with pytest.raises(RuntimeError, match="pending ORM changes"):
        module.release_session_memory(db, stage="backfill", end_transaction=True)

    db.rollback.assert_not_called()
    db.expire_all.assert_not_called()
    db.expunge_all.assert_not_called()
