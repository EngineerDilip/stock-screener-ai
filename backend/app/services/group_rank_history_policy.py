"""Calendar-day group-rank history windows.

These windows drive ``rank_change_*`` lookups that target
``current_date - N calendar days`` and accept a nearby stored snapshot. They
are distinct from the FeatureRun offset policy in ``group_ranking_history``.
"""

from __future__ import annotations

CALENDAR_DAY_GROUP_RANK_CHANGE_WINDOWS = {
    "1w": 7,
    "1m": 30,
    "3m": 90,
    "6m": 180,
}

CALENDAR_DAY_GROUP_RANK_LOOKUP_TOLERANCE_DAYS = 7

DEFAULT_CALENDAR_DAY_GROUP_RANK_HISTORY_LOOKBACK_DAYS = (
    max(CALENDAR_DAY_GROUP_RANK_CHANGE_WINDOWS.values())
    + CALENDAR_DAY_GROUP_RANK_LOOKUP_TOLERANCE_DAYS
)


__all__ = [
    "CALENDAR_DAY_GROUP_RANK_CHANGE_WINDOWS",
    "CALENDAR_DAY_GROUP_RANK_LOOKUP_TOLERANCE_DAYS",
    "DEFAULT_CALENDAR_DAY_GROUP_RANK_HISTORY_LOOKBACK_DAYS",
]
