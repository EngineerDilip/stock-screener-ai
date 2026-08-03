from __future__ import annotations

from contextlib import nullcontext
from datetime import date
from types import SimpleNamespace

from app.domain.relative_strength import BALANCED_RS_FORMULA_VERSION
from app.scripts import export_static_site
from app.services.market_exposure_service import EXPOSURE_BACKFILL_DAYS
from app.services.static_market_publish_policy import StaticMarketRsArtifactState


class _FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _ReadyGroupRankBackfill:
    ready_for_enrichment = True

    def as_dict(self) -> dict[str, str]:
        return {"status": "completed"}


def test_static_daily_refresh_ensures_market_breadth_before_exposure(monkeypatch):
    events: list[tuple[str, str]] = []
    breadth_call: dict[str, object] = {}

    monkeypatch.setattr(export_static_site, "STATIC_EXPORT_MARKETS", ("HK",))
    monkeypatch.setattr(export_static_site, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(export_static_site, "disable_serialized_data_fetch_lock", nullcontext)
    monkeypatch.setattr(export_static_site, "disable_serialized_market_workload", nullcontext)
    monkeypatch.setattr(export_static_site, "_tracked_ibd_csv_path", lambda: "ibd.csv")
    monkeypatch.setattr(
        export_static_site.IBDIndustryService,
        "load_from_csv",
        lambda _db, csv_path: 0,
    )
    monkeypatch.setattr(
        export_static_site,
        "_resolve_latest_completed_trading_date",
        lambda market: date(2026, 7, 31),
    )
    monkeypatch.setattr(
        export_static_site,
        "_refresh_static_daily_prices",
        lambda *, as_of_date, market: {"status": "completed", "market": market},
    )
    monkeypatch.setattr(
        export_static_site,
        "_prepare_static_rs_formula",
        lambda *, market, as_of_date, formula_version: {
            "status": "completed",
            "market": market,
            "as_of_date": as_of_date.isoformat(),
            "formula_version": formula_version,
            "market_rs_run_id": 42,
        },
    )
    monkeypatch.setattr(
        export_static_site,
        "classify_static_market_rs_artifact_result",
        lambda *args, **kwargs: StaticMarketRsArtifactState.READY,
    )

    def ensure_breadth(*, as_of_date, market, min_trading_days=None, lookback_days=None):
        breadth_call.update(
            as_of_date=as_of_date,
            market=market,
            min_trading_days=min_trading_days,
            lookback_days=lookback_days,
        )
        events.append(("breadth", market))
        return {"status": "completed", "market": market, "as_of_date": as_of_date.isoformat()}

    def compute_exposure(*, as_of_date, market):
        events.append(("exposure", market))
        return {"market": market, "date": as_of_date.isoformat(), "status": "stored"}

    monkeypatch.setattr(export_static_site, "_ensure_breadth_history", ensure_breadth)
    monkeypatch.setattr(export_static_site, "_compute_static_market_exposure", compute_exposure)

    import app.interfaces.tasks.feature_store_tasks as feature_store_tasks

    monkeypatch.setattr(
        feature_store_tasks,
        "build_daily_snapshot",
        SimpleNamespace(
            run=lambda **kwargs: {
                "status": "published",
                "run_id": 7,
                "market": kwargs["market"],
            }
        ),
    )
    monkeypatch.setattr(
        feature_store_tasks,
        "_enrich_feature_run_with_ibd_metadata",
        lambda **kwargs: {"status": "completed"},
    )
    monkeypatch.setattr(
        export_static_site,
        "_ensure_group_rank_history",
        lambda **kwargs: _ReadyGroupRankBackfill(),
    )

    results, warnings = export_static_site._run_daily_refresh(
        market="HK",
        skip_universe_refresh=True,
        skip_fundamentals_refresh=True,
        rs_formula_version=BALANCED_RS_FORMULA_VERSION,
    )

    assert warnings == []
    assert results["market_exposure"]["HK"]["status"] == "stored"
    assert events.index(("breadth", "HK")) < events.index(("exposure", "HK"))
    assert breadth_call == {
        "as_of_date": date(2026, 7, 31),
        "market": "HK",
        "min_trading_days": 0,
        "lookback_days": EXPOSURE_BACKFILL_DAYS,
    }


def test_static_daily_refresh_skips_exposure_when_breadth_history_errors(monkeypatch):
    monkeypatch.setattr(export_static_site, "STATIC_EXPORT_MARKETS", ("HK",))
    monkeypatch.setattr(export_static_site, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(export_static_site, "disable_serialized_data_fetch_lock", nullcontext)
    monkeypatch.setattr(export_static_site, "disable_serialized_market_workload", nullcontext)
    monkeypatch.setattr(export_static_site, "_tracked_ibd_csv_path", lambda: "ibd.csv")
    monkeypatch.setattr(
        export_static_site.IBDIndustryService,
        "load_from_csv",
        lambda _db, csv_path: 0,
    )
    monkeypatch.setattr(
        export_static_site,
        "_resolve_latest_completed_trading_date",
        lambda market: date(2026, 7, 31),
    )
    monkeypatch.setattr(
        export_static_site,
        "_refresh_static_daily_prices",
        lambda *, as_of_date, market: {"status": "completed", "market": market},
    )
    monkeypatch.setattr(
        export_static_site,
        "_prepare_static_rs_formula",
        lambda *, market, as_of_date, formula_version: {
            "status": "completed",
            "market": market,
            "as_of_date": as_of_date.isoformat(),
            "formula_version": formula_version,
            "market_rs_run_id": 42,
        },
    )
    monkeypatch.setattr(
        export_static_site,
        "classify_static_market_rs_artifact_result",
        lambda *args, **kwargs: StaticMarketRsArtifactState.READY,
    )
    monkeypatch.setattr(
        export_static_site,
        "_ensure_breadth_history",
        lambda **kwargs: {
            "status": "errored",
            "market": kwargs["market"],
            "as_of_date": kwargs["as_of_date"].isoformat(),
            "errors": 1,
            "error_dates": [kwargs["as_of_date"].isoformat()],
        },
    )
    monkeypatch.setattr(
        export_static_site,
        "_compute_static_market_exposure",
        lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("exposure must not compute when breadth is incomplete")
        ),
    )

    import app.interfaces.tasks.feature_store_tasks as feature_store_tasks

    monkeypatch.setattr(
        feature_store_tasks,
        "build_daily_snapshot",
        SimpleNamespace(
            run=lambda **kwargs: (_ for _ in ()).throw(
                AssertionError("snapshot must not publish after exposure is skipped")
            )
        ),
    )
    monkeypatch.setattr(
        feature_store_tasks,
        "_enrich_feature_run_with_ibd_metadata",
        lambda **kwargs: {"status": "completed"},
    )

    results, warnings = export_static_site._run_daily_refresh(
        market="HK",
        skip_universe_refresh=True,
        skip_fundamentals_refresh=True,
        rs_formula_version=BALANCED_RS_FORMULA_VERSION,
    )

    assert results["market_exposure"]["HK"]["error"] == "market_breadth_not_ready"
    assert results["feature_snapshots"]["HK"]["reason"] == "market_exposure_not_ready"
    assert (
        "Static export market HK exposure not stored for 2026-07-31: "
        "market_breadth_not_ready."
    ) in warnings


def test_ensure_breadth_history_marks_backfill_errors_not_completed(monkeypatch):
    as_of_date = date(2026, 7, 31)
    backfill_kwargs: dict[str, object] = {}

    class _FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return []

    class _FakeDb(_FakeSession):
        def query(self, *args, **kwargs):
            return _FakeQuery()

    class _FakeBreadthCalculator:
        def __init__(self, db, price_cache, *, market):
            self.market = market

        def backfill_range(self, **kwargs):
            backfill_kwargs.update(kwargs)
            return {
                "total_dates": 1,
                "processed": 0,
                "errors": 1,
                "error_dates": [as_of_date.isoformat()],
            }

    monkeypatch.setattr(export_static_site, "SessionLocal", lambda: _FakeDb())
    monkeypatch.setattr(export_static_site, "_generate_trading_dates", lambda *args, **kwargs: [as_of_date])
    monkeypatch.setattr(export_static_site, "get_price_cache", lambda: object())
    monkeypatch.setattr(export_static_site, "BreadthCalculatorService", _FakeBreadthCalculator)

    result = export_static_site._ensure_breadth_history(
        as_of_date=as_of_date,
        market="HK",
        min_trading_days=0,
    )

    assert result["status"] == "errored"
    assert result["errors"] == 1
    assert result["error_dates"] == ["2026-07-31"]
    assert backfill_kwargs["exclude_unsupported_price_symbols"] is True


def test_ensure_breadth_history_marks_calculation_errors_not_completed(monkeypatch):
    as_of_date = date(2026, 7, 31)

    class _FakeQuery:
        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return []

    class _FakeDb(_FakeSession):
        def query(self, *args, **kwargs):
            return _FakeQuery()

    class _FakeBreadthCalculator:
        def __init__(self, db, price_cache, *, market):
            self.market = market

        def backfill_range(self, **kwargs):
            return {
                "total_dates": 1,
                "processed": 1,
                "errors": 0,
                "error_dates": [],
                "target_symbols": 2,
                "symbols_with_cached_history": 2,
                "cache_miss_stocks": 0,
                "error_stocks": 1,
                "cache_coverage_ratio": 1.0,
            }

    monkeypatch.setattr(export_static_site, "SessionLocal", lambda: _FakeDb())
    monkeypatch.setattr(export_static_site, "_generate_trading_dates", lambda *args, **kwargs: [as_of_date])
    monkeypatch.setattr(export_static_site, "get_price_cache", lambda: object())
    monkeypatch.setattr(export_static_site, "BreadthCalculatorService", _FakeBreadthCalculator)

    result = export_static_site._ensure_breadth_history(
        as_of_date=as_of_date,
        market="HK",
        min_trading_days=0,
    )

    assert result["status"] == "errored"
    assert result["error_stocks"] == 1
    assert result["error"] == (
        "Cache-only breadth backfill has calculation errors "
        "(error_stocks=1)"
    )
