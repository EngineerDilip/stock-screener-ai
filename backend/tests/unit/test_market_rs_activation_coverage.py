from datetime import date, timedelta
from unittest.mock import MagicMock

from app.services.market_rs_activation_coverage import (
    MARKET_RS_ACTIVATION_LOOKBACK_DAYS,
    MarketRsActivationCoverage,
)


def test_activation_coverage_is_the_exact_bounded_trading_calendar() -> None:
    through_date = date(2026, 7, 29)
    required_dates = (date(2026, 1, 23), date(2026, 7, 29))
    calendar = MagicMock()
    calendar.trading_days.return_value = required_dates

    coverage = MarketRsActivationCoverage.build(
        calendar_service=calendar,
        market="us",
        through_date=through_date,
    )

    assert coverage.market == "US"
    assert coverage.required_dates == required_dates
    assert coverage.start_date == required_dates[0]
    calendar.trading_days.assert_called_once_with(
        "US",
        through_date - timedelta(days=MARKET_RS_ACTIVATION_LOOKBACK_DAYS),
        through_date,
    )
