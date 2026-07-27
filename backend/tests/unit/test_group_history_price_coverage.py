from __future__ import annotations

from datetime import date

from app.models.stock import StockPrice


class _Calendar:
    @staticmethod
    def trading_days(market, start, end):
        assert market == "US"
        assert start < end
        return [date(2026, 3, 2), end]

    @staticmethod
    def session_anchors(market, as_of_date, *, offsets):
        assert market == "US"
        assert tuple(offsets) == (21, 63, 126, 189, 252)
        return {
            0: as_of_date,
            21: date(2026, 1, 30),
            63: date(2025, 12, 1),
            126: date(2025, 9, 1),
            189: date(2025, 6, 2),
            252: date(2025, 3, 3),
        }


def _price(symbol: str, day: date, adjusted_close: float | None) -> StockPrice:
    return StockPrice(
        symbol=symbol,
        date=day,
        close=adjusted_close or 1.0,
        adj_close=adjusted_close,
    )


def test_group_history_price_coverage_requires_every_usable_adjusted_anchor(
    universe_session,
) -> None:
    from app.services.group_history_price_coverage import (
        GroupHistoryPriceCoverageService,
    )

    service = GroupHistoryPriceCoverageService(calendar_service=_Calendar())
    through_date = date(2026, 6, 4)
    anchors = service.required_anchor_dates(market="US", through_date=through_date)

    universe_session.add_all(
        [
            *[_price("SPY", day, 100.0) for day in anchors],
            *[
                _price("AAA", day, 50.0)
                for day in anchors
                if day != date(2025, 9, 1)
            ],
            *[
                _price("BAD", day, 0.0 if day == date(2025, 12, 1) else 25.0)
                for day in anchors
            ],
        ]
    )
    universe_session.commit()

    coverage = service.classify(
        universe_session,
        market="US",
        through_date=through_date,
        symbols=("AAA", "SPY", "BAD", "NONE"),
    )

    assert coverage.required_anchor_count == len(anchors)
    assert coverage.complete_symbols == ("SPY",)
    assert coverage.incomplete_symbols == ("AAA", "BAD", "NONE")
    assert coverage.available_anchor_counts["AAA"] == len(anchors) - 1
    assert coverage.available_anchor_counts["BAD"] == len(anchors) - 1
    assert coverage.incomplete_samples == ("AAA", "BAD", "NONE")


def test_group_history_price_coverage_skips_markets_without_group_rankings(
    universe_session,
) -> None:
    from app.services.group_history_price_coverage import (
        GroupHistoryPriceCoverageService,
    )

    service = GroupHistoryPriceCoverageService(calendar_service=_Calendar())

    coverage = service.classify(
        universe_session,
        market="SG",
        through_date=date(2026, 6, 4),
        symbols=("D05.SI",),
    )

    assert coverage.required_anchor_count == 0
    assert coverage.complete_symbols == ("D05.SI",)
    assert coverage.incomplete_symbols == ()
