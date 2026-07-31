"""Typed queue-manifest recorder tests."""

from __future__ import annotations

from app.services.bootstrap_queue_manifest_recorder import (
    BootstrapQueueManifestRecorder,
)
from app.services.bootstrap_run_manifest import BootstrapRunManifest


class _RecordingStore:
    def __init__(self) -> None:
        self.claimed: list[BootstrapRunManifest] = []
        self.updated: list[BootstrapRunManifest] = []

    def claim(self, manifest: BootstrapRunManifest) -> BootstrapRunManifest:
        self.claimed.append(manifest)
        return manifest

    def update(self, manifest: BootstrapRunManifest) -> BootstrapRunManifest:
        self.updated.append(manifest)
        return manifest


def test_recorder_persists_typed_manifest_transitions() -> None:
    store = _RecordingStore()
    recorder = BootstrapQueueManifestRecorder.create(
        primary_market="US",
        enabled_markets=("US", "HK"),
        store=store,
    )

    recorder.record_queueing()
    recorder.record_dispatched_market(market="US", task_id="task-us")
    recorder.record_dispatched_market(market="HK", task_id="task-hk")
    recorder.record_queued()

    assert [manifest.queue_state.value for manifest in store.claimed] == ["queueing"]
    assert [manifest.queue_state.value for manifest in store.updated] == [
        "partial",
        "partial",
        "queued",
    ]
    assert store.updated[-1].market_task_ids == {
        "US": "task-us",
        "HK": "task-hk",
    }


def test_dispatch_failure_without_tasks_is_a_typed_terminal_update() -> None:
    store = _RecordingStore()
    recorder = BootstrapQueueManifestRecorder.create(
        primary_market="US",
        enabled_markets=("US",),
        store=store,
    )
    recorder.record_queueing()

    recorder.record_dispatch_failed_safely()

    assert store.updated[-1].queue_state.value == "failed"
