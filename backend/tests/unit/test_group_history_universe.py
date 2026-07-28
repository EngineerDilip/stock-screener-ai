from __future__ import annotations

from datetime import date

from app.services.point_in_time_universe_service import (
    PointInTimeUniverse,
    PointInTimeUniverseUnavailable,
    hash_point_in_time_universe_symbols,
)


def _universe(day: date, *symbols: str) -> PointInTimeUniverse:
    values = tuple(symbols)
    return PointInTimeUniverse(
        market="US",
        as_of_date=day,
        symbols=values,
        universe_hash=hash_point_in_time_universe_symbols(values),
    )


class _Source:
    def __init__(self, result=None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = []

    def resolve(self, db, *, market, as_of_date):
        self.calls.append((db, market, as_of_date))
        if self.error is not None:
            raise self.error
        return self.result


def test_group_history_universe_prefers_nonempty_point_in_time_membership() -> None:
    from app.services.group_history_universe import (
        GROUP_HISTORY_POINT_IN_TIME_POLICY,
        GroupHistoryUniverseResolver,
    )

    day = date(2026, 3, 2)
    point_in_time = _Source(_universe(day, "PIT"))
    current = _Source(_universe(day, "CURRENT"))
    resolver = GroupHistoryUniverseResolver(
        point_in_time_universe=point_in_time,
        current_active_universe=current,
    )

    result = resolver.resolve(object(), market="us", as_of_date=day)

    assert result.symbols == ("PIT",)
    assert resolver.policy_for("US", day) == GROUP_HISTORY_POINT_IN_TIME_POLICY
    assert current.calls == []


def test_group_history_universe_falls_back_when_lifecycle_is_unavailable() -> None:
    from app.services.group_history_universe import (
        GROUP_HISTORY_CURRENT_ACTIVE_FALLBACK_POLICY,
        GroupHistoryUniverseResolver,
    )

    day = date(2026, 3, 2)
    point_in_time = _Source(
        error=PointInTimeUniverseUnavailable("missing lifecycle events")
    )
    current = _Source(_universe(day, "CURRENT"))
    resolver = GroupHistoryUniverseResolver(
        point_in_time_universe=point_in_time,
        current_active_universe=current,
    )

    result = resolver.resolve(object(), market="US", as_of_date=day)

    assert result.symbols == ("CURRENT",)
    assert (
        resolver.policy_for("US", day)
        == GROUP_HISTORY_CURRENT_ACTIVE_FALLBACK_POLICY
    )


def test_group_history_universe_falls_back_when_historical_result_is_empty() -> None:
    from app.services.group_history_universe import (
        GROUP_HISTORY_CURRENT_ACTIVE_FALLBACK_POLICY,
        GroupHistoryUniverseResolver,
    )

    day = date(2026, 3, 2)
    resolver = GroupHistoryUniverseResolver(
        point_in_time_universe=_Source(_universe(day)),
        current_active_universe=_Source(_universe(day, "CURRENT")),
    )

    result = resolver.resolve(object(), market="US", as_of_date=day)

    assert result.symbols == ("CURRENT",)
    assert resolver.policy_counts() == {
        GROUP_HISTORY_CURRENT_ACTIVE_FALLBACK_POLICY: 1
    }
