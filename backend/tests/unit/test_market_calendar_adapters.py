from datetime import date

import pandas as pd

from app.services import market_calendar_adapters as module
from app.services.market_calendar_adapters import RawMarketCalendarAdapter


class _FakeRedis:
    def __init__(self):
        self.values = {}
        self.setex_calls = []

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, ttl, value):
        self.setex_calls.append((key, ttl, value))
        self.values[key] = value


class _ScheduleCalendar:
    def __init__(self):
        self.calls = []

    def schedule(self, *, start_date: pd.Timestamp, end_date: pd.Timestamp):
        start = start_date.date()
        end = end_date.date()
        self.calls.append((start, end))
        sessions = [
            pd.Timestamp(session_day)
            for session_day in (date(2026, 1, 2), date(2026, 1, 5))
            if start <= session_day <= end
        ]
        return pd.DataFrame(index=sessions)


def test_sessions_in_range_uses_redis_cache(monkeypatch):
    redis = _FakeRedis()
    calendar = _ScheduleCalendar()
    monkeypatch.setattr(module, "get_redis_client", lambda: redis)
    adapter = RawMarketCalendarAdapter(
        calendar,
        cache_namespace="exchange_calendars:XSES:XSES",
    )

    first = adapter.sessions_in_range(date(2026, 1, 1), date(2026, 1, 7))
    second = adapter.sessions_in_range(date(2026, 1, 1), date(2026, 1, 7))

    assert first == (date(2026, 1, 2), date(2026, 1, 5))
    assert second == first
    assert calendar.calls == [(date(2026, 1, 1), date(2026, 1, 7))]
    assert redis.setex_calls == [
        (
            "calendar:sessions:v1:exchange_calendars:XSES:XSES:2026-01-01:2026-01-07",
            module.SESSION_RANGE_CACHE_TTL_SECONDS,
            '["2026-01-02","2026-01-05"]',
        )
    ]
