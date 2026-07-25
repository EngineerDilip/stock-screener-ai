from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.domain.relative_strength import BALANCED_RS_FORMULA_VERSION
from app.models.industry import IBDGroupRank
from app.models.stock_universe import StockUniverse
from app.services.group_rank_history_backfill_service import (
    DEFAULT_GROUP_RANK_HISTORY_LOOKBACK_DAYS,
)
from app.services.rrg_service import MIN_TAIL_WEEKS
from app.services.static_rrg_bootstrap_backfill_service import (
    STATIC_RRG_BOOTSTRAP_UNIVERSE_POLICY,
    StaticRRGBootstrapBackfillService,
)
from app.services.static_rrg_bootstrap_universe import StaticRRGBootstrapUniverse


def _session_factory():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(
        engine,
        tables=[StockUniverse.__table__, IBDGroupRank.__table__],
    )
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def test_bootstrap_universe_uses_current_active_market_rows_for_historical_date():
    engine, factory = _session_factory()
    try:
        with factory() as db:
            db.add_all(
                [
                    StockUniverse(symbol="AAPL", market="US", is_active=True),
                    StockUniverse(symbol="MSFT", market="US", is_active=True),
                    StockUniverse(symbol="ZZZ", market="US", is_active=False),
                    StockUniverse(symbol="0700.HK", market="HK", is_active=True),
                ]
            )
            db.commit()

            universe = StaticRRGBootstrapUniverse().resolve(
                db,
                market="US",
                as_of_date=date(2026, 4, 17),
            )

        assert universe.market == "US"
        assert universe.as_of_date == date(2026, 4, 17)
        assert universe.symbols == ("AAPL", "MSFT")
        assert len(universe.universe_hash) == 64
    finally:
        engine.dispose()


def test_bootstrap_backfill_materializes_min_tail_weekly_targets():
    engine, factory = _session_factory()
    through_date = date(2026, 7, 24)
    start = through_date - timedelta(days=DEFAULT_GROUP_RANK_HISTORY_LOOKBACK_DAYS)
    trading_days = [
        start + timedelta(days=offset)
        for offset in range((through_date - start).days + 1)
        if (start + timedelta(days=offset)).weekday() < 5
    ]
    snapshot_calls: list[date] = []
    group_calls: list[date] = []

    class FakeCalendar:
        def trading_days(self, market, range_start, range_end):
            assert market == "US"
            assert range_start == start
            assert range_end == through_date
            return trading_days

    class FakeSnapshotService:
        def calculate(self, db, *, market, as_of_date, formula_version):
            snapshot_calls.append(as_of_date)
            return SimpleNamespace(id=len(snapshot_calls))

    class FakeGroupService:
        def calculate_and_store(self, db, *, market, as_of_date, formula_version):
            group_calls.append(as_of_date)
            return [{"industry_group": "Software", "rank": 1}]

    try:
        with factory() as db:
            result = StaticRRGBootstrapBackfillService(
                calendar_service=FakeCalendar(),
                market_rs_snapshot_service=FakeSnapshotService(),
                canonical_group_service=FakeGroupService(),
            ).backfill(
                db,
                market="US",
                through_date=through_date,
                formula_version=BALANCED_RS_FORMULA_VERSION,
            )

        expected_weekly_targets = (
            date(2026, 5, 8),
            date(2026, 5, 15),
            date(2026, 5, 22),
            date(2026, 5, 29),
            date(2026, 6, 5),
            date(2026, 6, 12),
            date(2026, 6, 19),
            date(2026, 6, 26),
            date(2026, 7, 3),
            date(2026, 7, 10),
            date(2026, 7, 17),
            date(2026, 7, 24),
        )
        assert tuple(snapshot_calls) == expected_weekly_targets
        assert tuple(group_calls) == expected_weekly_targets
        assert result.as_dict() == {
            "status": "completed",
            "market": "US",
            "as_of_date": "2026-07-24",
            "formula_version": BALANCED_RS_FORMULA_VERSION,
            "policy": STATIC_RRG_BOOTSTRAP_UNIVERSE_POLICY,
            "lookback_start_date": "2026-04-15",
            "target_dates": [day.isoformat() for day in expected_weekly_targets],
            "existing": 0,
            "processed": MIN_TAIL_WEEKS,
            "errors": 0,
            "total_dates": MIN_TAIL_WEEKS,
        }
    finally:
        engine.dispose()
