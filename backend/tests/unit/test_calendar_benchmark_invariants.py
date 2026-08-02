from datetime import date

from app.services.governance import calendar_benchmark_invariants as invariants
from app.services.governance.calendar_benchmark_invariants import (
    EXPECTED_SUNDAY_LAST_COMPLETED,
    SATURDAY_PROBE,
    SUNDAY_ROLLOVER_PROBE,
    collect_calendar_benchmark_invariants,
)


class _RegistryStub:
    @staticmethod
    def get_primary_symbol(market):
        if str(market).strip().upper() == "JP":
            return "^N225"
        raise KeyError(market)


class _CalendarStub:
    @staticmethod
    def normalize_market(market):
        return str(market).strip().upper()

    @staticmethod
    def calendar_id(market):
        assert market == "JP"
        return "XTKS"

    @staticmethod
    def calendar_metadata(market):
        assert market == "JP"
        return {
            "market": "JP",
            "calendar_id": "XTKS",
            "provider_calendar_id": "JPX",
            "calendar_provider": "pandas_market_calendars",
        }

    @staticmethod
    def is_trading_day(market, day):
        assert market == "JP"
        if day == SATURDAY_PROBE:
            return False
        return day not in {
            date(2026, 3, 20),
            date(2026, 9, 22),
            date(2026, 9, 23),
        }

    @staticmethod
    def last_completed_trading_day(market, *, now):
        assert market == "JP"
        assert now == SUNDAY_ROLLOVER_PROBE
        return EXPECTED_SUNDAY_LAST_COMPLETED


def test_collect_calendar_benchmark_invariants_normalizes_market_keys():
    result = collect_calendar_benchmark_invariants(
        (" jp ",),
        registry=_RegistryStub(),
        calendar_service=_CalendarStub(),
    )

    assert result.has_regressions is False
    assert result.weekday_holiday_probes == {
        "JP": {
            "2026-03-20": False,
            "2026-09-22": False,
            "2026-09-23": False,
        }
    }
    assert set(result.calendar_metadata) == {"JP"}


def test_collect_calendar_benchmark_invariants_reports_missing_expected_config(
    monkeypatch,
):
    monkeypatch.delitem(invariants.EXPECTED_BENCHMARK_SYMBOLS, "JP")
    monkeypatch.delitem(invariants.EXPECTED_CALENDAR_IDS, "JP")

    result = collect_calendar_benchmark_invariants(
        ("JP",),
        registry=_RegistryStub(),
        calendar_service=_CalendarStub(),
    )

    assert result.benchmark_mismatches == {
        "JP": "no expected benchmark symbol configured for JP"
    }
    assert result.calendar_id_mismatches == {
        "JP": "no expected calendar ID configured for JP"
    }
