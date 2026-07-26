from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.stock_universe import StockUniverse


def test_static_group_snapshot_coordinator_uses_current_universe_for_history():
    from app.services.static_group_snapshot_coordinator import (
        build_static_group_snapshot_coordinator,
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[StockUniverse.__table__])
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory() as db:
            db.add_all(
                [
                    StockUniverse(
                        symbol="AAPL",
                        market="US",
                        is_active=True,
                        first_seen_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
                    ),
                    StockUniverse(
                        symbol="MSFT",
                        market="US",
                        is_active=True,
                        first_seen_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
                    ),
                    StockUniverse(
                        symbol="ZZZ",
                        market="US",
                        is_active=False,
                        first_seen_at=datetime(2026, 7, 24, tzinfo=timezone.utc),
                    ),
                ]
            )
            db.commit()

            coordinator = build_static_group_snapshot_coordinator()
            universe_resolver = (
                coordinator.market_rs_snapshot_service.input_loader
                ._point_in_time_universe
            )
            universe = universe_resolver.resolve(
                db,
                market="US",
                as_of_date=date(2026, 1, 23),
            )

        assert universe.symbols == ("AAPL", "MSFT")
    finally:
        engine.dispose()
