from __future__ import annotations

from types import SimpleNamespace


def test_group_history_coordinator_uses_fallback_resolver_and_real_legacy_service():
    from app.services.group_history_snapshot_coordinator import (
        build_group_history_snapshot_coordinator,
    )

    resolver = object()
    legacy_service = SimpleNamespace(calculate_group_rankings=lambda *_a, **_k: None)

    coordinator = build_group_history_snapshot_coordinator(
        universe_resolver=resolver,
        legacy_group_service=legacy_service,
        calendar_service=SimpleNamespace(),
        market_rs_repository=SimpleNamespace(),
    )

    assert (
        coordinator.market_rs_snapshot_service.input_loader._point_in_time_universe
        is resolver
    )
    assert coordinator.legacy_group_service is legacy_service
