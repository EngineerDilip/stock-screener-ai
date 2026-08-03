from __future__ import annotations

from contextlib import nullcontext
from datetime import date
from types import SimpleNamespace

from app.domain.relative_strength import BALANCED_RS_FORMULA_VERSION
from app.scripts import export_static_site
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

    def ensure_breadth(*, as_of_date, market):
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
