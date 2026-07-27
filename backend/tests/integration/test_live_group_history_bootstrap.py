"""Acceptance coverage for live Group history repair and API reads."""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from app.database import get_db
from app.domain.relative_strength import (
    BALANCED_RS_FORMULA_VERSION,
    LEGACY_RS_FORMULA_VERSION,
)
from app.infra.db.models.relative_strength import (
    MarketRsFormulaPointer,
    MarketRsRun,
)
from app.infra.db.repositories.market_rs_repo import MarketRsRunRepository
from app.main import app
from app.models.industry import IBDGroupRank
from app.models.scan_result import Scan
from app.models.stock import StockPrice
from app.models.watchlist import Watchlist
from app.services.group_history_bootstrap_service import (
    GroupHistoryBootstrapService,
    GroupHistoryBootstrapStatus,
)
from app.services.group_history_readiness_service import (
    GroupHistoryReadinessService,
)
from app.services.group_rank_snapshot_reader import GroupRankSnapshotReader
from app.services.rrg_history_provider import StoredGroupRankHistoryProvider
from app.services.server_auth import require_server_session


class _WeekdayCalendar:
    @staticmethod
    def trading_days(_market: str, start: date, end: date) -> list[date]:
        days = []
        cursor = start
        while cursor <= end:
            if cursor.weekday() < 5:
                days.append(cursor)
            cursor += timedelta(days=1)
        return days


def _store_balanced_snapshot(db, snapshot_date: date) -> None:
    run = MarketRsRun(
        market="US",
        as_of_date=snapshot_date,
        formula_version=BALANCED_RS_FORMULA_VERSION,
        status="completed",
        benchmark_symbol="SPY",
        benchmark_as_of_date=snapshot_date,
        universe_hash=f"acceptance-{snapshot_date.isoformat()}",
        expected_symbol_count=20,
        eligible_symbol_count=20,
        excluded_symbol_count=0,
        diagnostics_json={"price_basis": "adj_close_only"},
    )
    db.add(run)
    db.flush()
    is_current = snapshot_date == date(2026, 6, 30)
    rows = (
        ("Group Alpha", 1 if is_current else 2, 40.0 + snapshot_date.toordinal() % 31),
        ("Group Beta", 2 if is_current else 1, 70.0 - snapshot_date.toordinal() % 29),
    )
    for group, rank, average_rs in rows:
        db.add(
            IBDGroupRank(
                market="US",
                industry_group=group,
                date=snapshot_date,
                rank=rank,
                avg_rs_rating=average_rs,
                avg_rs_rating_1m=average_rs - 1,
                avg_rs_rating_3m=average_rs - 2,
                num_stocks=10,
                num_stocks_rs_above_80=4,
                top_symbol="AAA" if group == "Group Alpha" else "BBB",
                top_rs_rating=95,
                rs_formula_version=BALANCED_RS_FORMULA_VERSION,
                market_rs_run_id=run.id,
            )
        )
    db.commit()


class _SnapshotCoordinator:
    def ensure_snapshot(self, db, *, identity):
        _store_balanced_snapshot(db, identity.as_of_date)

    def repair_snapshot(self, db, *, identity):
        raise AssertionError(f"Unexpected invalid snapshot: {identity}")


class _UniverseResolver:
    @staticmethod
    def policy_for(_market: str, _as_of_date: date) -> str:
        return "current_active_fallback_v1"


@pytest.mark.asyncio
async def test_live_repair_populates_rank_changes_movers_and_rrg_without_data_loss(
    db_session,
    monkeypatch,
):
    through_date = date(2026, 6, 30)
    db_session.add(
        MarketRsFormulaPointer(
            market="US",
            formula_version=BALANCED_RS_FORMULA_VERSION,
        )
    )
    db_session.add_all(
        [
            StockPrice(
                symbol="KEEP",
                date=through_date,
                close=123.0,
                adj_close=123.0,
            ),
            Watchlist(symbol="KEEP", notes="preserve me"),
            Scan(
                scan_id="preserved-scan",
                criteria={"rs_min": 80},
                status="completed",
            ),
            IBDGroupRank(
                market="US",
                industry_group="Legacy Group",
                date=through_date,
                rank=1,
                avg_rs_rating=55,
                num_stocks=3,
                rs_formula_version=LEGACY_RS_FORMULA_VERSION,
            ),
        ]
    )
    db_session.commit()
    _store_balanced_snapshot(db_session, through_date)

    preserved = {
        StockPrice: db_session.query(StockPrice).count(),
        Watchlist: db_session.query(Watchlist).count(),
        Scan: db_session.query(Scan).count(),
    }
    legacy_count = (
        db_session.query(IBDGroupRank)
        .filter(
            IBDGroupRank.rs_formula_version == LEGACY_RS_FORMULA_VERSION
        )
        .count()
    )

    repository = MarketRsRunRepository()
    provider = StoredGroupRankHistoryProvider(
        object(),
        repository,
        snapshot_reader=GroupRankSnapshotReader(),
    )
    readiness = GroupHistoryReadinessService(
        calendar_service=_WeekdayCalendar(),
        snapshot_reader=GroupRankSnapshotReader(),
        market_rs_repository=repository,
        rrg_history_provider=provider,
    )
    repair = GroupHistoryBootstrapService(
        readiness_service=readiness,
        snapshot_coordinator=_SnapshotCoordinator(),
        universe_resolver=_UniverseResolver(),
    ).ensure(
        db_session,
        market="US",
        through_date=through_date,
    )

    assert repair.status is GroupHistoryBootstrapStatus.READY
    assert repair.after.ready is True
    assert repair.skipped_valid == 1
    assert repair.processed_dates
    assert repair.policy_counts == {
        "current_active_fallback_v1": len(repair.processed_dates)
    }
    for model, count in preserved.items():
        assert db_session.query(model).count() == count
    assert (
        db_session.query(IBDGroupRank)
        .filter(
            IBDGroupRank.rs_formula_version == LEGACY_RS_FORMULA_VERSION
        )
        .count()
        == legacy_count
    )

    def _override_get_db():
        yield db_session

    from app.api.v1 import groups as groups_api

    monkeypatch.setattr(
        groups_api,
        "cached_group_payload",
        lambda **kwargs: kwargs["compute"](),
    )
    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[require_server_session] = lambda: True
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            rankings_response = await client.get(
                "/api/v1/groups/rankings/current",
                params={"market": "US", "limit": 10, "as_of_date": through_date},
            )
            assert rankings_response.status_code == 200, rankings_response.text
            rankings = rankings_response.json()["rankings"]
            assert rankings
            assert all(
                row[f"rank_change_{period}"] is not None
                for row in rankings
                for period in ("1w", "1m", "3m", "6m")
            )

            for period in ("1w", "1m", "3m", "6m"):
                movers_response = await client.get(
                    "/api/v1/groups/rankings/movers",
                    params={
                        "market": "US",
                        "period": period,
                        "as_of_date": through_date,
                    },
                )
                assert movers_response.status_code == 200
                movers = movers_response.json()
                assert movers["gainers"]
                assert movers["losers"]

            rrg_response = await client.get(
                "/api/v1/groups/rrg/scopes",
                params={"market": "US", "as_of_date": through_date},
            )
            assert rrg_response.status_code == 200
            rrg = rrg_response.json()
            assert "groups" in rrg["available_scopes"]
            assert rrg["payload"]["groups"]["groups"]
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(require_server_session, None)
