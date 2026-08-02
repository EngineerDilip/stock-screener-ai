"""Benchmark and calendar invariants for launch-gate evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from ...domain.markets.calendar_policy import calendar_session_probe_expectations
from ..benchmark_registry_service import BenchmarkRegistryService, benchmark_registry
from ..market_calendar_service import MarketCalendarService

EXPECTED_BENCHMARK_SYMBOLS = {
    "US": "SPY",
    "HK": "^HSI",
    "IN": "^NSEI",
    "JP": "^N225",
    "KR": "^KS11",
    "TW": "^TWII",
    "CN": "000300.SS",
    "SG": "^STI",
    "MY": "^KLSE",
    "CA": "^GSPTSE",
    "DE": "^GDAXI",
    "AU": "^AXJO",
}

EXPECTED_CALENDAR_IDS = {
    "US": "XNYS",
    "HK": "XHKG",
    "IN": "XNSE",
    "JP": "XTKS",
    "KR": "XKRX",
    "TW": "XTAI",
    "CN": "XSHG",
    "SG": "XSES",
    "MY": "XKLS",
    "CA": "XTSE",
    "DE": "XETR",
    "AU": "XASX",
}

SATURDAY_PROBE = date(2026, 4, 11)
SUNDAY_ROLLOVER_PROBE = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
EXPECTED_SUNDAY_LAST_COMPLETED = date(2026, 4, 10)


@dataclass(frozen=True, slots=True)
class CalendarBenchmarkInvariantResult:
    benchmark_mismatches: dict[str, str]
    calendar_id_mismatches: dict[str, str]
    weekend_regressions: list[str]
    sunday_rollover_regressions: dict[str, str]
    weekday_holiday_probes: dict[str, dict[str, bool]]
    weekday_holiday_regressions: dict[str, dict[str, dict[str, bool]]]
    calendar_metadata: dict[str, dict[str, str | None]]

    @property
    def has_regressions(self) -> bool:
        return any(
            (
                self.benchmark_mismatches,
                self.calendar_id_mismatches,
                self.weekend_regressions,
                self.sunday_rollover_regressions,
                self.weekday_holiday_regressions,
            )
        )

    def failure_detail(self) -> str:
        return (
            "Benchmark/calendar regressions detected: "
            f"benchmark={self.benchmark_mismatches}, "
            f"calendar_ids={self.calendar_id_mismatches}, "
            f"weekend_sessions={self.weekend_regressions}, "
            f"sunday_rollover={self.sunday_rollover_regressions}, "
            f"weekday_holidays={self.weekday_holiday_regressions}"
        )

    def failure_metrics(
        self,
        *,
        markets: tuple[str, ...],
        calendar_dependency_versions: dict[str, str | None],
    ) -> dict[str, Any]:
        return {
            "enabled_markets": list(markets),
            "benchmark_mismatches": self.benchmark_mismatches,
            "calendar_id_mismatches": self.calendar_id_mismatches,
            "weekend_session_regressions": self.weekend_regressions,
            "sunday_rollover_regressions": self.sunday_rollover_regressions,
            "weekday_holiday_probes": self.weekday_holiday_probes,
            "weekday_holiday_regressions": self.weekday_holiday_regressions,
            "calendar_metadata": self.calendar_metadata,
            "calendar_dependency_versions": calendar_dependency_versions,
        }

    def pass_metrics(
        self,
        *,
        markets: tuple[str, ...],
        calendar_dependency_versions: dict[str, str | None],
    ) -> dict[str, Any]:
        return {
            "checked_markets": list(markets),
            "saturday_probe": str(SATURDAY_PROBE),
            "sunday_rollover_probe": str(SUNDAY_ROLLOVER_PROBE.date()),
            "weekday_holiday_probes": self.weekday_holiday_probes,
            "calendar_metadata": self.calendar_metadata,
            "calendar_dependency_versions": calendar_dependency_versions,
        }


def collect_calendar_benchmark_invariants(
    markets: tuple[str, ...],
    *,
    registry: BenchmarkRegistryService = benchmark_registry,
    calendar_service: MarketCalendarService | None = None,
) -> CalendarBenchmarkInvariantResult:
    service = calendar_service or MarketCalendarService()
    benchmark_mismatches: dict[str, str] = {}
    calendar_id_mismatches: dict[str, str] = {}
    weekend_regressions: list[str] = []
    sunday_rollover_regressions: dict[str, str] = {}
    weekday_holiday_probes: dict[str, dict[str, bool]] = {}
    weekday_holiday_regressions: dict[str, dict[str, dict[str, bool]]] = {}
    calendar_metadata: dict[str, dict[str, str | None]] = {}
    session_probe_expectations = calendar_session_probe_expectations()

    for raw_market in markets:
        try:
            market = service.normalize_market(raw_market)
        except ValueError as exc:
            market = str(raw_market)
            benchmark_mismatches[market] = f"unsupported market: {exc}"
            continue
        try:
            got = registry.get_primary_symbol(market)
        except (KeyError, ValueError) as exc:
            benchmark_mismatches[market] = f"lookup failed: {exc}"
            continue
        expected_benchmark = EXPECTED_BENCHMARK_SYMBOLS.get(market)
        if expected_benchmark is None:
            benchmark_mismatches[market] = (
                f"no expected benchmark symbol configured for {market}"
            )
        elif got != expected_benchmark:
            benchmark_mismatches[market] = f"expected {expected_benchmark}, got {got}"

        calendar_id = service.calendar_id(market)
        expected_calendar_id = EXPECTED_CALENDAR_IDS.get(market)
        if expected_calendar_id is None:
            calendar_id_mismatches[market] = (
                f"no expected calendar ID configured for {market}"
            )
        elif calendar_id != expected_calendar_id:
            calendar_id_mismatches[market] = (
                f"expected {expected_calendar_id}, got {calendar_id}"
            )
        calendar_metadata[market] = service.calendar_metadata(market)

        if service.is_trading_day(market, SATURDAY_PROBE):
            weekend_regressions.append(market)

        last_completed = service.last_completed_trading_day(
            market,
            now=SUNDAY_ROLLOVER_PROBE,
        )
        if last_completed != EXPECTED_SUNDAY_LAST_COMPLETED:
            sunday_rollover_regressions[market] = str(last_completed)

        for probe_day, expected_is_trading_day in session_probe_expectations.get(
            market,
            {},
        ).items():
            actual_is_trading_day = service.is_trading_day(market, probe_day)
            weekday_holiday_probes.setdefault(market, {})[probe_day.isoformat()] = (
                actual_is_trading_day
            )
            if actual_is_trading_day != expected_is_trading_day:
                weekday_holiday_regressions.setdefault(market, {})[
                    probe_day.isoformat()
                ] = {
                    "expected": expected_is_trading_day,
                    "actual": actual_is_trading_day,
                }

    return CalendarBenchmarkInvariantResult(
        benchmark_mismatches=benchmark_mismatches,
        calendar_id_mismatches=calendar_id_mismatches,
        weekend_regressions=weekend_regressions,
        sunday_rollover_regressions=sunday_rollover_regressions,
        weekday_holiday_probes=weekday_holiday_probes,
        weekday_holiday_regressions=weekday_holiday_regressions,
        calendar_metadata=calendar_metadata,
    )
