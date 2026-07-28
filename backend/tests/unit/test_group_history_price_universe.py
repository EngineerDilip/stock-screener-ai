from __future__ import annotations

from datetime import date
from unittest.mock import Mock

from app.services.point_in_time_universe_service import PointInTimeUniverse


def test_group_history_price_universe_unions_historical_members() -> None:
    from app.services.group_history_price_universe import (
        GroupHistoryPriceUniverseService,
    )

    first = date(2026, 1, 2)
    second = date(2026, 6, 8)
    calendar = Mock()
    calendar.trading_days.return_value = [first, second]
    resolver = Mock()
    resolver.resolve.side_effect = [
        PointInTimeUniverse("US", first, ("OLD", "SHARED"), "first"),
        PointInTimeUniverse("US", second, ("NEW", "SHARED"), "second"),
    ]
    db = Mock()

    symbols = GroupHistoryPriceUniverseService(
        calendar_service=calendar,
        universe_resolver=resolver,
    ).symbols(
        db,
        market="US",
        through_date=second,
    )

    assert symbols == ("OLD", "SHARED", "NEW")
    assert [call.kwargs["as_of_date"] for call in resolver.resolve.call_args_list] == [
        first,
        second,
    ]
