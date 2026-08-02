"""Adapters that normalize third-party market calendar APIs."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

import pandas as pd


class CalendarScheduleUnavailable(RuntimeError):
    """Raised when a calendar object cannot expose a session schedule."""


class MarketCalendarAdapter(Protocol):
    """Provider-neutral calendar operations used by MarketCalendarService."""

    def is_session(self, session: pd.Timestamp) -> bool: ...

    def sessions_in_range(self, start_day: date, end_day: date) -> tuple[date, ...]: ...

    def session_close(self, day: date) -> pd.Timestamp | None: ...

    def session_open_ranges(
        self, day: date
    ) -> tuple[tuple[pd.Timestamp, pd.Timestamp], ...] | None: ...

    def is_open_on_minute(self, minute_utc: pd.Timestamp) -> bool | None: ...


@dataclass(slots=True)
class RawMarketCalendarAdapter:
    """Normalize exchange_calendars and pandas_market_calendars objects."""

    raw_calendar: object
    _session_range_cache: dict[tuple[date, date], tuple[date, ...]] = field(
        default_factory=dict,
        init=False,
    )

    def is_session(self, session: pd.Timestamp) -> bool:
        is_session = getattr(self.raw_calendar, "is_session", None)
        if callable(is_session):
            return bool(is_session(session))
        return session.date() in self.sessions_in_range(
            session.date(),
            session.date(),
        )

    def sessions_in_range(self, start_day: date, end_day: date) -> tuple[date, ...]:
        if start_day > end_day:
            return ()
        cache_key = (start_day, end_day)
        if cache_key not in self._session_range_cache:
            sessions_attr = getattr(self.raw_calendar, "sessions", None)
            if sessions_attr is not None:
                sessions = tuple(
                    pd.Timestamp(session).date()
                    for session in sessions_attr
                    if start_day <= pd.Timestamp(session).date() <= end_day
                )
            else:
                schedule = self._schedule_for_range(
                    start_day=start_day,
                    end_day=end_day,
                )
                sessions = tuple(
                    pd.Timestamp(session).date() for session in schedule.index
                )
            self._session_range_cache[cache_key] = sessions
        return self._session_range_cache[cache_key]

    def session_close(self, day: date) -> pd.Timestamp | None:
        schedule = self._schedule_for_range(start_day=day, end_day=day)
        if schedule.empty:
            return None
        session_row = schedule.iloc[0]
        return self._timestamp_field(
            session_row,
            ("close", "market_close"),
            day=day,
        )

    def session_open_ranges(
        self,
        day: date,
    ) -> tuple[tuple[pd.Timestamp, pd.Timestamp], ...] | None:
        schedule = self._schedule_for_range(start_day=day, end_day=day)
        if schedule.empty:
            return None
        session_row = schedule.iloc[0]
        market_open = self._timestamp_field(
            session_row,
            ("open", "market_open"),
            day=day,
        )
        market_close = self._timestamp_field(
            session_row,
            ("close", "market_close"),
            day=day,
        )
        break_start = self._optional_timestamp_field(
            session_row,
            ("break_start", "market_break_start"),
        )
        break_end = self._optional_timestamp_field(
            session_row,
            ("break_end", "market_break_end"),
        )
        if (
            break_start is not None
            and break_end is not None
            and market_open < break_start < break_end < market_close
        ):
            return (
                (market_open, break_start),
                (break_end, market_close),
            )
        return ((market_open, market_close),)

    def is_open_on_minute(self, minute_utc: pd.Timestamp) -> bool | None:
        is_open_on_minute = getattr(self.raw_calendar, "is_open_on_minute", None)
        if not callable(is_open_on_minute):
            return None
        return bool(is_open_on_minute(minute_utc, ignore_breaks=False))

    def _schedule_for_range(
        self,
        *,
        start_day: date,
        end_day: date,
    ) -> pd.DataFrame:
        schedule_attr = getattr(self.raw_calendar, "schedule", None)
        start_ts = pd.Timestamp(start_day)
        end_ts = pd.Timestamp(end_day)
        if callable(schedule_attr):
            return schedule_attr(start_date=start_ts, end_date=end_ts)
        if hasattr(schedule_attr, "loc"):
            return schedule_attr.loc[start_ts:end_ts]
        raise CalendarScheduleUnavailable(
            "Calendar object does not expose a usable schedule"
        )

    @classmethod
    def _timestamp_field(
        cls,
        session_row: pd.Series,
        field_names: tuple[str, ...],
        *,
        day: date,
    ) -> pd.Timestamp:
        for field_name in field_names:
            if field_name in session_row.index:
                return cls._utc_timestamp(session_row[field_name])
        raise CalendarScheduleUnavailable(
            f"Calendar schedule for {day.isoformat()} is missing all of {field_names}"
        )

    @classmethod
    def _optional_timestamp_field(
        cls,
        session_row: pd.Series,
        field_names: tuple[str, ...],
    ) -> pd.Timestamp | None:
        for field_name in field_names:
            if field_name in session_row.index:
                value = session_row[field_name]
                if pd.isna(value):
                    return None
                return cls._utc_timestamp(value)
        return None

    @staticmethod
    def _utc_timestamp(value: object) -> pd.Timestamp:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            return timestamp.tz_localize("UTC")
        return timestamp.tz_convert("UTC")
