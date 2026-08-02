from datetime import date, datetime

import pandas as pd
import pytest

from app.domain.markets.calendar_policy import (
    CalendarProvider,
    CalendarSessionOverride,
)
from app.domain.markets.catalog import get_market_catalog
from app.services import market_calendar_service as calendar_module
from app.services.market_calendar_service import MarketCalendarService


class _FakeCalendar:
    def __init__(self):
        self.sessions = [
            pd.Timestamp("2026-04-09"),
            pd.Timestamp("2026-04-10"),
        ]
        self.schedule = pd.DataFrame(
            {
                "close": [
                    pd.Timestamp("2026-04-09 08:00:00+00:00"),
                    pd.Timestamp("2026-04-10 08:00:00+00:00"),
                ],
            },
            index=self.sessions,
        )

    def is_session(self, session: pd.Timestamp) -> bool:
        return any(s.date() == session.date() for s in self.sessions)

    def previous_session(self, session: pd.Timestamp) -> pd.Timestamp:
        previous = [s for s in self.sessions if s.date() < session.date()]
        return previous[-1]

    def is_open_on_minute(self, ts: pd.Timestamp, ignore_breaks: bool = False) -> bool:
        # Keep this deterministic: only one minute is considered open.
        return ts == pd.Timestamp("2026-04-10 01:30:00+00:00")


class _FallbackCalendar:
    def __init__(self):
        self.sessions = [pd.Timestamp("2026-04-10")]
        self.schedule = pd.DataFrame(
            {
                "market_open": [pd.Timestamp("2026-04-10 03:45:00+00:00")],
                "market_close": [pd.Timestamp("2026-04-10 10:00:00+00:00")],
            },
            index=self.sessions,
        )

    def is_session(self, session: pd.Timestamp) -> bool:
        return any(s.date() == session.date() for s in self.sessions)


class _BreakCalendar:
    def __init__(self):
        self.sessions = [pd.Timestamp("2026-04-10")]
        self.schedule = pd.DataFrame(
            {
                "market_open": [pd.Timestamp("2026-04-10 00:00:00+00:00")],
                "break_start": [pd.Timestamp("2026-04-10 02:30:00+00:00")],
                "break_end": [pd.Timestamp("2026-04-10 03:30:00+00:00")],
                "market_close": [pd.Timestamp("2026-04-10 06:00:00+00:00")],
            },
            index=self.sessions,
        )

    def is_session(self, session: pd.Timestamp) -> bool:
        return any(s.date() == session.date() for s in self.sessions)


class _ProviderCalendar:
    pass


class _RangeScheduleCalendar:
    def __init__(self):
        self.calls = []
        self.session_dates = {
            date(2026, 3, 18),
            date(2026, 3, 19),
            date(2026, 3, 20),
            date(2026, 3, 23),
        }

    def schedule(self, *, start_date: pd.Timestamp, end_date: pd.Timestamp):
        start = start_date.date()
        end = end_date.date()
        self.calls.append((start, end))
        sessions = [
            pd.Timestamp(session_date)
            for session_date in sorted(self.session_dates)
            if start <= session_date <= end
        ]
        return pd.DataFrame(
            {
                "market_open": [
                    pd.Timestamp.combine(
                        session.date(), datetime.min.time()
                    ).tz_localize("UTC")
                    for session in sessions
                ],
                "market_close": [
                    pd.Timestamp.combine(
                        session.date(), datetime.min.time()
                    ).tz_localize("UTC")
                    + pd.Timedelta(hours=6)
                    for session in sessions
                ],
            },
            index=sessions,
        )


class _BoundsCalendar:
    def is_session(self, session: pd.Timestamp) -> bool:
        raise ValueError("Requested date is later than the last session available")


class _EarliestLatestBoundsCalendar:
    def __init__(self, message: str):
        self.message = message

    def is_session(self, session: pd.Timestamp) -> bool:
        raise ValueError(self.message)


class _ScheduleUnavailableCalendar:
    pass


class _BrokenScheduleCalendar:
    def is_session(self, session: pd.Timestamp) -> bool:
        return True

    def schedule(self, *, start_date: pd.Timestamp, end_date: pd.Timestamp):
        raise TypeError("provider schedule bug")


class _SessionCalendar:
    def __init__(self, sessions):
        self.sessions = tuple(pd.Timestamp(session) for session in sessions)
        self._session_dates = {session.date() for session in self.sessions}

    def is_session(self, session: pd.Timestamp) -> bool:
        return session.date() in self._session_dates


class _BoundedSessionCalendar(_SessionCalendar):
    def __init__(self, sessions, last_session, first_session=None):
        super().__init__(sessions)
        self.last_session = pd.Timestamp(last_session)
        self.first_session = pd.Timestamp(first_session or sessions[0])


class _RangeBoundedSessionCalendar(_BoundedSessionCalendar):
    def sessions_in_range(self, start_session: pd.Timestamp, end_session: pd.Timestamp):
        if start_session.date() < self.first_session.date():
            raise ValueError(
                "Requested start is earlier than the first session available"
            )
        if end_session.date() > self.last_session.date():
            raise ValueError("Requested end is later than the last session available")
        return tuple(
            session
            for session in self.sessions
            if start_session.date() <= session.date() <= end_session.date()
        )


def _service_with_provider(provider_factory, **kwargs):
    return MarketCalendarService(
        calendar_providers={
            CalendarProvider.EXCHANGE_CALENDARS: provider_factory,
            CalendarProvider.PANDAS_MARKET_CALENDARS: provider_factory,
        },
        **kwargs,
    )


def test_market_calendar_service_uses_canonical_calendar_ids():
    service = _service_with_provider(lambda _: _FakeCalendar())

    assert service.calendar_id("US") == "XNYS"
    assert service.calendar_id("HK") == "XHKG"
    assert service.calendar_id("IN") == "XNSE"
    assert service.calendar_id("JP") == "XTKS"
    assert service.calendar_id("KR") == "XKRX"
    assert service.calendar_id("TW") == "XTAI"
    assert service.calendar_id("CN") == "XSHG"
    assert service.calendar_id("AU") == "XASX"


def test_market_calendar_service_matches_catalog_primary_mic_facts():
    service = _service_with_provider(lambda _: _FakeCalendar())

    catalog = get_market_catalog()
    for market in catalog.supported_market_codes():
        assert (
            service.calendar_id(market)
            == catalog.get(market).primary_mic_facts.calendar_id
        )


def test_market_calendar_service_uses_primary_mic_facts_for_market_level_calls():
    service = _service_with_provider(lambda _: _FakeCalendar())
    india = get_market_catalog().get("IN")
    primary_facts = india.primary_mic_facts

    assert service.calendar_id("IN") == primary_facts.calendar_id
    assert service.provider_calendar_id("IN") == primary_facts.provider_calendar_id
    assert service.market_timezone("IN").key == primary_facts.timezone
    assert service.default_currency("IN") == primary_facts.default_currency


def test_market_calendar_service_supports_mic_specific_fact_lookup():
    service = _service_with_provider(lambda _: _FakeCalendar())
    bombay_facts = get_market_catalog().get("IN").mic_facts_for("XBOM")

    assert service.calendar_id("IN", mic="XBOM") == bombay_facts.calendar_id
    assert (
        service.provider_calendar_id("IN", mic="XBOM")
        == bombay_facts.provider_calendar_id
    )
    assert service.market_timezone("IN", mic="XBOM").key == bombay_facts.timezone
    assert service.default_currency("IN", mic="XBOM") == bombay_facts.default_currency


def test_last_completed_trading_day_before_close_returns_previous_session():
    service = _service_with_provider(lambda _: _FakeCalendar())
    now_hkt = datetime.fromisoformat("2026-04-10T15:30:00+08:00")

    expected = service.last_completed_trading_day("HK", now=now_hkt)

    assert expected == pd.Timestamp("2026-04-09").date()


def test_last_completed_trading_day_after_close_returns_current_session():
    service = _service_with_provider(lambda _: _FakeCalendar())
    now_hkt = datetime.fromisoformat("2026-04-10T16:30:00+08:00")

    expected = service.last_completed_trading_day("HK", now=now_hkt)

    assert expected == pd.Timestamp("2026-04-10").date()


def test_last_completed_trading_day_before_post_close_buffer_returns_previous_session():
    service = _service_with_provider(lambda _: _FakeCalendar())
    now_hkt = datetime.fromisoformat("2026-04-10T16:29:00+08:00")

    expected = service.last_completed_trading_day("HK", now=now_hkt)

    assert expected == pd.Timestamp("2026-04-09").date()


def test_is_market_open_uses_calendar_open_minute():
    service = _service_with_provider(lambda _: _FakeCalendar())
    open_minute_hkt = datetime.fromisoformat("2026-04-10T09:30:00+08:00")
    closed_minute_hkt = datetime.fromisoformat("2026-04-10T09:31:00+08:00")

    assert service.is_market_open("HK", now=open_minute_hkt) is True
    assert service.is_market_open("HK", now=closed_minute_hkt) is False


def test_is_market_open_schedule_fallback_treats_close_minute_as_closed():
    service = _service_with_provider(lambda _: _FallbackCalendar())
    pre_close_ist = datetime.fromisoformat("2026-04-10T15:29:00+05:30")
    close_minute_ist = datetime.fromisoformat("2026-04-10T15:30:00+05:30")

    assert service.is_market_open("IN", now=pre_close_ist) is True
    assert service.is_market_open("IN", now=close_minute_ist) is False


def test_is_market_open_schedule_fallback_excludes_intraday_break():
    service = _service_with_provider(lambda _: _BreakCalendar())
    morning_tokyo = datetime.fromisoformat("2026-04-10T10:00:00+09:00")
    lunch_tokyo = datetime.fromisoformat("2026-04-10T12:00:00+09:00")
    afternoon_tokyo = datetime.fromisoformat("2026-04-10T13:00:00+09:00")

    assert service.is_market_open("JP", now=morning_tokyo) is True
    assert service.is_market_open("JP", now=lunch_tokyo) is False
    assert service.is_market_open("JP", now=afternoon_tokyo) is True


def test_india_pmc_lookup_uses_provider_specific_calendar_id():
    calls = []
    service = MarketCalendarService(
        calendar_providers={
            CalendarProvider.PANDAS_MARKET_CALENDARS: (
                lambda calendar_id: calls.append(calendar_id) or _ProviderCalendar()
            ),
            CalendarProvider.EXCHANGE_CALENDARS: (
                lambda calendar_id: calls.append(calendar_id) or _ProviderCalendar()
            ),
        }
    )

    service._get_calendar("IN")

    assert calls == ["NSE"]


def test_india_secondary_mic_lookup_uses_pmc_provider():
    calls = []
    service = MarketCalendarService(
        calendar_providers={
            CalendarProvider.PANDAS_MARKET_CALENDARS: (
                lambda calendar_id: (
                    calls.append(("pmc", calendar_id)) or _ProviderCalendar()
                )
            ),
            CalendarProvider.EXCHANGE_CALENDARS: (
                lambda calendar_id: (
                    calls.append(("xcals", calendar_id)) or _ProviderCalendar()
                )
            ),
        }
    )

    service._get_calendar("IN", mic="XBOM")

    assert calls == [("pmc", "BSE")]


def test_japan_lookup_uses_jpx_pmc_calendar_provider():
    calls = []
    service = MarketCalendarService(
        calendar_providers={
            CalendarProvider.PANDAS_MARKET_CALENDARS: (
                lambda calendar_id: (
                    calls.append(("pmc", calendar_id)) or _ProviderCalendar()
                )
            ),
            CalendarProvider.EXCHANGE_CALENDARS: (
                lambda calendar_id: (
                    calls.append(("xcals", calendar_id)) or _ProviderCalendar()
                )
            ),
        }
    )

    service._get_calendar("JP")

    assert calls == [("pmc", "JPX")]


def test_singapore_lookup_uses_exchange_calendars_calendar_id():
    calls = []
    service = MarketCalendarService(
        calendar_providers={
            CalendarProvider.PANDAS_MARKET_CALENDARS: (
                lambda calendar_id: (
                    calls.append(("pmc", calendar_id)) or _ProviderCalendar()
                )
            ),
            CalendarProvider.EXCHANGE_CALENDARS: (
                lambda calendar_id: (
                    calls.append(("xcals", calendar_id)) or _ProviderCalendar()
                )
            ),
        }
    )

    service._get_calendar("SG")

    assert calls == [("xcals", "XSES")]


@pytest.mark.skipif(
    calendar_module.pmc is None,
    reason="pandas_market_calendars is required for JP holiday checks",
)
def test_japan_known_2026_weekday_holidays_are_closed():
    service = MarketCalendarService()

    assert service.is_trading_day("JP", date(2026, 3, 20)) is False
    assert service.is_trading_day("JP", date(2026, 9, 22)) is False
    assert service.is_trading_day("JP", date(2026, 9, 23)) is False
    assert service.trading_days("JP", date(2026, 3, 18), date(2026, 3, 23)) == [
        date(2026, 3, 18),
        date(2026, 3, 19),
        date(2026, 3, 23),
    ]


def test_india_injected_calendar_provider_uses_provider_specific_calendar_id():
    calls = []
    service = _service_with_provider(
        lambda calendar_id: calls.append(calendar_id) or _ProviderCalendar()
    )

    service._get_calendar("IN")

    assert calls == ["NSE"]


def test_injected_calendar_provider_uses_mic_specific_provider_calendar_id():
    calls = []
    service = _service_with_provider(
        lambda calendar_id: calls.append(calendar_id) or _ProviderCalendar()
    )

    service._get_calendar("IN", mic="XBOM")

    assert calls == ["BSE"]


def test_trading_day_lookup_uses_mic_specific_calendar_id():
    calls = []
    service = _service_with_provider(
        lambda calendar_id: calls.append(calendar_id) or _FakeCalendar()
    )

    assert service.is_trading_day("IN", date(2026, 4, 10), mic="XBOM") is True
    assert calls == ["BSE"]


def test_closed_date_overrides_hide_provider_sessions_from_anchors():
    sessions = [
        pd.Timestamp("2026-03-18"),
        pd.Timestamp("2026-03-19"),
        pd.Timestamp("2026-03-20"),
        pd.Timestamp("2026-03-23"),
    ]
    service = _service_with_provider(
        lambda _name: _SessionCalendar(sessions),
        session_overrides=(CalendarSessionOverride("JP", date(2026, 3, 20), False),),
    )

    assert service.is_trading_day("JP", date(2026, 3, 20)) is False
    assert service.trading_days("JP", date(2026, 3, 18), date(2026, 3, 23)) == [
        date(2026, 3, 18),
        date(2026, 3, 19),
        date(2026, 3, 23),
    ]
    assert service.session_anchors("JP", date(2026, 3, 23), offsets=(1,)) == {
        0: date(2026, 3, 23),
        1: date(2026, 3, 19),
    }


def test_positive_session_overrides_are_rejected():
    with pytest.raises(ValueError, match="positive trading-day overrides"):
        _service_with_provider(
            lambda _name: _FakeCalendar(),
            session_overrides=(CalendarSessionOverride("JP", date(2026, 3, 20), True),),
        )


def test_last_completed_trading_day_respects_closed_date_overrides():
    sessions = [
        pd.Timestamp("2026-03-19"),
        pd.Timestamp("2026-03-20"),
    ]
    service = _service_with_provider(
        lambda _name: _SessionCalendar(sessions),
        session_overrides=(CalendarSessionOverride("JP", date(2026, 3, 20), False),),
    )
    noon_tokyo = datetime.fromisoformat("2026-03-20T12:00:00+09:00")

    assert service.last_completed_trading_day("JP", now=noon_tokyo) == date(2026, 3, 19)


@pytest.mark.parametrize("market", ["CN", "SG"])
def test_calendar_bounds_fallback_uses_weekdays(market):
    service = _service_with_provider(lambda _: _BoundsCalendar())

    assert service.is_trading_day(market, date(2026, 4, 10)) is True
    assert service.is_trading_day(market, date(2026, 4, 11)) is False


@pytest.mark.parametrize(
    "message",
    [
        "The requested date is before the earliest date supported by the calendar",
        "The requested date is after the latest date supported by the calendar",
    ],
)
def test_calendar_bounds_fallback_recognizes_earliest_latest_messages(message):
    service = _service_with_provider(
        lambda _: _EarliestLatestBoundsCalendar(message),
    )

    assert service.is_trading_day("CN", date(2026, 4, 10)) is True


@pytest.mark.parametrize("market", ["CN", "SG"])
def test_calendar_schedule_unavailable_fallback_uses_weekdays(market):
    service = _service_with_provider(lambda _: _ScheduleUnavailableCalendar())

    assert service.is_trading_day(market, date(2026, 4, 10)) is True
    assert service.is_trading_day(market, date(2026, 4, 11)) is False


def test_weekday_bounds_fallback_does_not_readd_provider_known_holidays():
    service = _service_with_provider(
        lambda _: _BoundedSessionCalendar(
            sessions=[date(2026, 4, 10), date(2026, 12, 31)],
            last_session=date(2026, 12, 31),
        ),
    )

    assert service.trading_days("CN", date(2026, 4, 10), date(2026, 4, 13)) == [
        date(2026, 4, 10),
    ]


def test_weekday_bounds_fallback_extends_after_provider_last_session():
    service = _service_with_provider(
        lambda _: _BoundedSessionCalendar(
            sessions=[date(2025, 12, 31)],
            last_session=date(2025, 12, 31),
        ),
    )

    assert service.trading_days("CN", date(2026, 1, 1), date(2026, 1, 5)) == [
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 1, 5),
    ]


def test_weekday_bounds_fallback_preserves_provider_prefix_when_range_exceeds_bounds():
    service = _service_with_provider(
        lambda _: _RangeBoundedSessionCalendar(
            sessions=[date(2025, 12, 29), date(2025, 12, 31)],
            last_session=date(2025, 12, 31),
        ),
    )

    assert service.trading_days("CN", date(2025, 12, 29), date(2026, 1, 5)) == [
        date(2025, 12, 29),
        date(2025, 12, 31),
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 1, 5),
    ]


def test_weekday_bounds_fallback_preserves_provider_suffix_when_range_starts_before_bounds():
    service = _service_with_provider(
        lambda _: _RangeBoundedSessionCalendar(
            sessions=[date(2026, 1, 2), date(2026, 1, 6)],
            first_session=date(2026, 1, 2),
            last_session=date(2026, 12, 31),
        ),
    )

    assert service.trading_days("CN", date(2025, 12, 29), date(2026, 1, 6)) == [
        date(2025, 12, 29),
        date(2025, 12, 30),
        date(2025, 12, 31),
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 1, 6),
    ]


@pytest.mark.parametrize("market", ["CN", "SG"])
def test_last_completed_trading_day_bounds_fallback(market):
    service = _service_with_provider(lambda _: _BoundsCalendar())

    before_close = datetime.fromisoformat("2026-04-10T15:00:00+08:00")
    after_close = datetime.fromisoformat("2026-04-10T16:00:00+08:00")

    assert service.last_completed_trading_day(market, now=before_close) == date(
        2026, 4, 9
    )
    assert service.last_completed_trading_day(market, now=after_close) == date(
        2026, 4, 10
    )


@pytest.mark.parametrize("market", ["CN", "SG"])
def test_last_completed_trading_day_does_not_mask_provider_type_errors(market):
    service = _service_with_provider(lambda _: _BrokenScheduleCalendar())

    after_close = datetime.fromisoformat("2026-04-10T16:30:00+08:00")

    with pytest.raises(TypeError, match="provider schedule bug"):
        service.last_completed_trading_day(market, now=after_close)


def test_trading_days_uses_range_schedule_and_applies_overrides():
    calendar = _RangeScheduleCalendar()
    service = _service_with_provider(lambda _: calendar)

    assert service.trading_days("JP", date(2026, 3, 18), date(2026, 3, 23)) == [
        date(2026, 3, 18),
        date(2026, 3, 19),
        date(2026, 3, 23),
    ]
    assert calendar.calls == [(date(2026, 3, 18), date(2026, 3, 23))]


def test_session_anchors_return_exact_market_session_offsets():
    sessions = pd.date_range("2025-01-01", periods=260, freq="B")
    service = _service_with_provider(lambda _name: _SessionCalendar(sessions))
    as_of = sessions[-1].date()

    anchors = service.session_anchors("US", as_of, offsets=(21, 63, 126, 189, 252))

    assert anchors[0] == as_of
    assert anchors[21] == sessions[-22].date()
    assert anchors[252] == sessions[-253].date()


def test_session_anchors_reject_a_non_session_as_of_date():
    sessions = pd.date_range("2025-01-01", periods=260, freq="B")
    service = _service_with_provider(lambda _name: _SessionCalendar(sessions))
    non_session = sessions[-1].date()
    while non_session.weekday() < 5:
        non_session += pd.Timedelta(days=1)

    with pytest.raises(ValueError, match="not a US trading session"):
        service.session_anchors("US", non_session, offsets=(21,))


def test_session_anchors_require_enough_history():
    sessions = pd.date_range("2026-01-01", periods=100, freq="B")
    service = _service_with_provider(lambda _name: _SessionCalendar(sessions))

    with pytest.raises(ValueError, match="253 required"):
        service.session_anchors("US", sessions[-1].date(), offsets=(252,))
