"""Stable MIC-scoped market facts."""

from __future__ import annotations

from dataclasses import dataclass

from .calendar_policy import CalendarProvider


@dataclass(frozen=True, slots=True)
class MicFacts:
    """Calendar, timezone, and currency facts for one MIC."""

    mic: str
    calendar_id: str
    timezone: str
    default_currency: str
    provider_calendar_id: str | None = None
    calendar_provider: CalendarProvider = CalendarProvider.EXCHANGE_CALENDARS

    def as_runtime_payload(self) -> dict[str, str | None]:
        return {
            "mic": self.mic,
            "calendar_id": self.calendar_id,
            "timezone": self.timezone,
            "default_currency": self.default_currency,
            "provider_calendar_id": self.provider_calendar_id,
            "calendar_provider": self.calendar_provider.value,
        }
