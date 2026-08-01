"""Universe policy for historical Group snapshot repair."""

from __future__ import annotations

from app.services.bounded_history_universe import (
    BOUNDED_HISTORY_CURRENT_ACTIVE_FALLBACK_POLICY,
    BOUNDED_HISTORY_POINT_IN_TIME_POLICY,
    CurrentActiveFallbackUniverseResolver,
    CurrentActiveUniverse,
)


GROUP_HISTORY_POINT_IN_TIME_POLICY = BOUNDED_HISTORY_POINT_IN_TIME_POLICY
GROUP_HISTORY_CURRENT_ACTIVE_FALLBACK_POLICY = (
    BOUNDED_HISTORY_CURRENT_ACTIVE_FALLBACK_POLICY
)


class GroupHistoryUniverseResolver(CurrentActiveFallbackUniverseResolver):
    """Prefer historical lifecycle membership, with the accepted group fallback."""


__all__ = [
    "CurrentActiveUniverse",
    "GROUP_HISTORY_CURRENT_ACTIVE_FALLBACK_POLICY",
    "GROUP_HISTORY_POINT_IN_TIME_POLICY",
    "GroupHistoryUniverseResolver",
]
