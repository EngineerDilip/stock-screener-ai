"""Market calendar abstraction for supported market session-aware decisions."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timedelta
from importlib import metadata
from zoneinfo import ZoneInfo

import pandas as pd

from ..domain.markets.calendar_policy import (
    DEFAULT_CALENDAR_SESSION_OVERRIDES,
    CalendarProvider,
    CalendarSessionOverride,
)
from ..domain.markets.catalog import (
    MarketCatalog,
    MarketCatalogError,
    get_market_catalog,
)
from ..domain.markets.mic import MicFacts
from .market_calendar_adapters import (
    CalendarScheduleUnavailable,
    MarketCalendarAdapter,
    RawMarketCalendarAdapter,
)

try:
    import exchange_calendars as xcals  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - runtime guard
    xcals = None  # type: ignore

try:
    import pandas_market_calendars as pmc  # type: ignore
except ModuleNotFoundError:  # pragma: no cover - runtime guard
    pmc = None  # type: ignore


class MarketCalendarService:
    """Unified market calendar contract backed by provider-specific calendars."""

    WEEKDAY_BOUNDS_FALLBACK_MARKETS = frozenset({"CN", "SG"})

    def __init__(
        self,
        calendar_providers: Mapping[CalendarProvider, Callable[[str], object]]
        | None = None,
        market_catalog: MarketCatalog | None = None,
        session_overrides: Iterable[CalendarSessionOverride] | None = None,
    ):
        self._market_catalog = market_catalog or get_market_catalog()
        self._calendar_providers: dict[
            CalendarProvider,
            Callable[[str], object] | None,
        ] = {
            CalendarProvider.EXCHANGE_CALENDARS: (
                xcals.get_calendar if xcals is not None else None
            ),
            CalendarProvider.PANDAS_MARKET_CALENDARS: (
                pmc.get_calendar if pmc is not None else None
            ),
        }
        self._calendar_providers.update(calendar_providers or {})
        self._session_overrides = self._normalize_session_overrides(
            (
                *DEFAULT_CALENDAR_SESSION_OVERRIDES,
                *(session_overrides or ()),
            )
        )
        self._calendar_cache: dict[
            tuple[CalendarProvider, str, str],
            MarketCalendarAdapter,
        ] = {}

    def normalize_market(self, market: str | None) -> str:
        try:
            normalized = self._market_catalog.get(market or "US").code
        except MarketCatalogError as exc:
            raise ValueError(
                f"Unsupported market for calendar service: {market}"
            ) from exc
        return normalized

    def _mic_facts(self, market: str | None, *, mic: str | None = None) -> MicFacts:
        normalized = self.normalize_market(market)
        return self._market_catalog.get(normalized).mic_facts_for(mic)

    def market_timezone(self, market: str, *, mic: str | None = None) -> ZoneInfo:
        return ZoneInfo(self._mic_facts(market, mic=mic).timezone)

    def market_now(
        self,
        market: str,
        now: datetime | None = None,
        *,
        mic: str | None = None,
    ) -> datetime:
        tz = self.market_timezone(market, mic=mic)
        if now is None:
            return datetime.now(tz)
        if now.tzinfo is None:
            return now.replace(tzinfo=tz)
        return now.astimezone(tz)

    def calendar_id(self, market: str, *, mic: str | None = None) -> str:
        return self._mic_facts(market, mic=mic).calendar_id

    def provider_calendar_id(
        self,
        market: str,
        *,
        mic: str | None = None,
    ) -> str | None:
        return self._mic_facts(market, mic=mic).provider_calendar_id

    def calendar_provider(
        self,
        market: str,
        *,
        mic: str | None = None,
    ) -> CalendarProvider:
        return self._calendar_provider_for_facts(self._mic_facts(market, mic=mic))

    def calendar_metadata(
        self,
        market: str,
        *,
        mic: str | None = None,
    ) -> dict[str, str | None]:
        facts = self._mic_facts(market, mic=mic)
        provider = self._calendar_provider_for_facts(facts)
        return {
            "market": self.normalize_market(market),
            "calendar_id": facts.calendar_id,
            "provider_calendar_id": facts.provider_calendar_id or facts.calendar_id,
            "calendar_provider": provider.value,
            "provider_package_version": self._provider_version(provider),
        }

    def default_currency(self, market: str, *, mic: str | None = None) -> str:
        return self._mic_facts(market, mic=mic).default_currency

    def _get_calendar(self, market: str, *, mic: str | None = None) -> MarketCalendarAdapter:
        normalized = self.normalize_market(market)
        facts = self._mic_facts(normalized, mic=mic)
        calendar_id = facts.calendar_id
        provider_calendar_id = facts.provider_calendar_id or calendar_id
        provider_engine = self._calendar_provider_for_facts(facts)
        provider = self._calendar_providers.get(provider_engine)
        if provider is None:
            raise RuntimeError(
                f"{provider_engine.value} is required for MarketCalendarService"
            )
        cache_key = (provider_engine, calendar_id, provider_calendar_id)
        if cache_key not in self._calendar_cache:
            self._calendar_cache[cache_key] = RawMarketCalendarAdapter(
                provider(provider_calendar_id)
            )
        return self._calendar_cache[cache_key]

    @staticmethod
    def _normalize_session_overrides(
        overrides: Iterable[CalendarSessionOverride],
    ) -> dict[str, dict[date, bool]]:
        normalized: dict[str, dict[date, bool]] = {}
        for override in overrides:
            normalized.setdefault(override.normalized_market(), {})[
                override.day
            ] = override.is_trading_day
        return normalized

    @staticmethod
    def _calendar_provider_for_facts(facts: MicFacts) -> CalendarProvider:
        return facts.calendar_provider

    @staticmethod
    def _provider_version(provider_engine: CalendarProvider) -> str | None:
        try:
            return metadata.version(provider_engine.package_name)
        except metadata.PackageNotFoundError:
            return None

    def _override_for_day(self, market: str, day: date) -> bool | None:
        normalized = self.normalize_market(market)
        return self._session_overrides.get(normalized, {}).get(day)

    def _previous_effective_session_date(
        self,
        market: str,
        day: date,
        *,
        mic: str | None = None,
    ) -> date:
        candidate = day - timedelta(days=1)
        for _ in range(370):
            if self.is_trading_day(market, candidate, mic=mic):
                return candidate
            candidate -= timedelta(days=1)
        raise ValueError(
            f"No previous effective trading session available before {day.isoformat()}"
        )

    @staticmethod
    def _is_calendar_bounds_error(exc: Exception) -> bool:
        class_name = exc.__class__.__name__.lower()
        message = str(exc).lower()
        return (
            "outofbounds" in class_name
            or "out of bounds" in message
            or "last session" in message
            or "first session" in message
        )

    @staticmethod
    def _is_weekday(day: date) -> bool:
        return day.weekday() < 5

    @classmethod
    def _previous_weekday(cls, day: date) -> date:
        candidate = day - timedelta(days=1)
        while not cls._is_weekday(candidate):
            candidate -= timedelta(days=1)
        return candidate

    def _last_completed_from_weekday_bounds_fallback(
        self,
        market: str,
        current_day: date,
        market_now: datetime,
        exc: Exception,
    ) -> date | None:
        if market not in self.WEEKDAY_BOUNDS_FALLBACK_MARKETS:
            return None
        if not (
            self._is_calendar_bounds_error(exc)
            or isinstance(exc, CalendarScheduleUnavailable)
        ):
            return None
        if self._is_weekday(current_day) and market_now.time().hour >= 16:
            return current_day
        return self._previous_weekday(current_day)

    def is_trading_day(
        self,
        market: str,
        day: date | None = None,
        *,
        mic: str | None = None,
    ) -> bool:
        normalized = self.normalize_market(market)
        candidate_day = day or self.market_now(normalized, mic=mic).date()
        override = self._override_for_day(normalized, candidate_day)
        if override is not None:
            return override
        try:
            calendar = self._get_calendar(normalized, mic=mic)
            return calendar.is_session(pd.Timestamp(candidate_day))
        except Exception as exc:
            if (
                normalized in self.WEEKDAY_BOUNDS_FALLBACK_MARKETS
                and self._is_calendar_bounds_error(exc)
            ):
                return self._is_weekday(candidate_day)
            raise

    def trading_days(
        self,
        market: str,
        start: date,
        end: date,
        *,
        mic: str | None = None,
    ) -> list[date]:
        """Trading days in ``[start, end]`` (inclusive), chronological order.

        The canonical way to enumerate sessions in a range, so callers don't
        reimplement a day-by-day loop. Preserves the per-market fallbacks in
        ``is_trading_day``.
        """
        normalized = self.normalize_market(market)
        days: list[date] = []
        day = start
        while day <= end:
            if self.is_trading_day(normalized, day, mic=mic):
                days.append(day)
            day += timedelta(days=1)
        return days

    def session_anchors(
        self,
        market: str,
        as_of_date: date,
        *,
        offsets: tuple[int, ...],
        mic: str | None = None,
    ) -> dict[int, date]:
        """Resolve exact prior Market sessions for fixed lookback offsets."""
        normalized = self.normalize_market(market)
        if not offsets or min(offsets) < 1:
            raise ValueError("session offsets must be positive")
        if not self.is_trading_day(normalized, as_of_date, mic=mic):
            raise ValueError(
                f"{as_of_date.isoformat()} is not a {normalized} trading session"
            )
        maximum = max(offsets)
        start = as_of_date - timedelta(days=maximum * 2 + 30)
        sessions = self.trading_days(normalized, start, as_of_date, mic=mic)
        if len(sessions) <= maximum:
            raise ValueError(
                f"{normalized} calendar has {len(sessions)} sessions; "
                f"{maximum + 1} required"
            )
        return {
            0: sessions[-1],
            **{offset: sessions[-1 - offset] for offset in offsets},
        }

    def is_market_open(
        self,
        market: str,
        now: datetime | None = None,
        *,
        mic: str | None = None,
    ) -> bool:
        market_now = self.market_now(market, now=now, mic=mic)
        if not self.is_trading_day(market, market_now.date(), mic=mic):
            return False
        calendar = self._get_calendar(market, mic=mic)
        current_session = pd.Timestamp(market_now.date())
        minute_utc = pd.Timestamp(market_now).tz_convert("UTC").floor("min")
        open_on_minute = calendar.is_open_on_minute(minute_utc)
        if open_on_minute is not None:
            return open_on_minute

        session_bounds = calendar.session_open_close(current_session.date())
        if session_bounds is None:
            return False
        market_open, market_close = session_bounds
        return bool(market_open.floor("min") <= minute_utc < market_close.floor("min"))

    def last_completed_trading_day(
        self,
        market: str,
        now: datetime | None = None,
        *,
        mic: str | None = None,
    ) -> date:
        """Return the latest trading day that should already have end-of-day bars."""
        normalized = self.normalize_market(market)
        market_now = self.market_now(normalized, now=now, mic=mic)
        current_day = market_now.date()

        try:
            if not self.is_trading_day(normalized, current_day, mic=mic):
                return self._previous_effective_session_date(
                    normalized,
                    current_day,
                    mic=mic,
                )
            calendar = self._get_calendar(normalized, mic=mic)

            try:
                market_close = calendar.session_close(current_day)
            except Exception as schedule_exc:
                fallback_day = self._last_completed_from_weekday_bounds_fallback(
                    normalized,
                    current_day,
                    market_now,
                    schedule_exc,
                )
                if fallback_day is not None:
                    return fallback_day
                raise
            if market_close is None:
                return self._previous_effective_session_date(
                    normalized,
                    current_day,
                    mic=mic,
                )
            close_with_buffer = (
                market_close.tz_convert(
                    self.market_timezone(normalized, mic=mic)
                ).to_pydatetime()
                + timedelta(minutes=30)
            )
            if market_now >= close_with_buffer:
                return current_day
            return self._previous_effective_session_date(
                normalized,
                current_day,
                mic=mic,
            )
        except Exception as exc:
            fallback_day = self._last_completed_from_weekday_bounds_fallback(
                normalized,
                current_day,
                market_now,
                exc,
            )
            if fallback_day is not None:
                return fallback_day
            raise
