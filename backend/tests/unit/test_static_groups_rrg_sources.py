"""Tests for explicit static RRG history source modes."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain.relative_strength import BALANCED_RS_FORMULA_VERSION
from app.services import static_groups_rrg_export as rrg_export
from app.services.static_groups_rrg_export import (
    StaticGroupsRRGDatabasePayloadSource,
    StaticGroupsRRGRollingHistoryExportSession,
    StaticGroupsRRGUnavailableError,
)
from app.services.rrg_service import MIN_TAIL_WEEKS
from app.services.static_rrg_bootstrap_backfill_service import (
    StaticRRGBootstrapBackfillResult,
    StaticRRGBootstrapBackfillStatus,
)
from app.services.static_rrg_history_bundle import (
    StaticRRGHistoryPreparation,
    StaticRRGHistoryUnavailableReason,
)
from app.services.static_rrg_history_contract import (
    STATIC_RRG_HISTORY_SCHEMA_VERSION,
    StaticRRGGroupPoint,
    StaticRRGHistoryPlan,
    StaticRRGHistoryState,
    StaticRRGWeek,
)


def _state(
    *,
    market: str,
    source_date: date,
    weeks: int = MIN_TAIL_WEEKS,
) -> StaticRRGHistoryState:
    group = StaticRRGGroupPoint(
        industry_group="Semiconductors",
        rank=1,
        avg_rs_rating=88.0,
        num_stocks=12,
    )
    return StaticRRGHistoryState(
        schema_version=STATIC_RRG_HISTORY_SCHEMA_VERSION,
        market=market,
        rs_formula_version=BALANCED_RS_FORMULA_VERSION,
        weeks=(
            StaticRRGWeek(
                source_date=source_date - timedelta(weeks=weeks - index - 1),
                groups=(group,),
            )
            for index in range(weeks)
        ),
    )


def _plan(tmp_path: Path, market: str) -> StaticRRGHistoryPlan:
    asset_name = f"rrg-history-{market.lower()}.json.gz"
    return StaticRRGHistoryPlan(
        enabled=True,
        market=market,
        asset_name=asset_name,
        source_path=tmp_path / asset_name,
        output_path=tmp_path / "current" / asset_name,
    )


def test_rolling_source_uses_export_date_for_prepare_and_persist(monkeypatch, tmp_path):
    export_date = date(2026, 4, 18)
    state = _state(market="HK", source_date=export_date)
    preparation = StaticRRGHistoryPreparation(
        plan=_plan(tmp_path, "HK"),
        state=state,
    )
    calls = []

    class _HistoryService:
        def prepare(
            self,
            db,
            *,
            market,
            through_date,
            directory,
            formula_version,
        ):
            calls.append(
                (
                    "prepare",
                    db,
                    market,
                    through_date,
                    directory,
                    formula_version,
                )
            )
            return preparation

        def persist(self, prepared, *, exported_as_of_date):
            calls.append(("persist", prepared, exported_as_of_date))
            return {"weeks": 1}

    monkeypatch.setattr(
        rrg_export,
        "_build_payload_from_state",
        lambda **kwargs: {"as_of_date": kwargs["expected_as_of_date"].isoformat()},
    )
    source = StaticGroupsRRGRollingHistoryExportSession(
        schema_version="static-site-v3",
        market="HK",
        directory=tmp_path,
        history_service=_HistoryService(),
    )
    db = object()

    payload = source.build(
        db=db,
        generated_at="2026-04-18T22:00:00Z",
        expected_as_of_date=export_date,
        market="HK",
        formula_version=BALANCED_RS_FORMULA_VERSION,
    )
    persisted = source.persist(exported_as_of_date=export_date)

    assert payload == {"as_of_date": "2026-04-18"}
    assert persisted == {"weeks": 1}
    assert calls == [
        (
            "prepare",
            db,
            "HK",
            export_date,
            tmp_path,
            BALANCED_RS_FORMULA_VERSION,
        ),
        ("persist", preparation, export_date),
    ]
    with pytest.raises(RuntimeError, match="already been built"):
        source.build(
            db=db,
            generated_at="2026-04-18T22:00:00Z",
            expected_as_of_date=export_date,
            market="HK",
            formula_version=BALANCED_RS_FORMULA_VERSION,
        )
    with pytest.raises(RuntimeError, match="already been persisted"):
        source.persist(exported_as_of_date=export_date)


def test_rolling_source_does_not_retry_failed_preparation(tmp_path):
    calls = []

    class _HistoryService:
        def prepare(self, *_args, **_kwargs):
            calls.append("prepare")
            return StaticRRGHistoryPreparation(
                plan=_plan(tmp_path, "HK"),
                state=None,
                warnings=("history unavailable",),
            )

        def build(self, *_args, **_kwargs):
            raise AssertionError("failed preparation must not fall back to a DB rebuild")

    source = StaticGroupsRRGRollingHistoryExportSession(
        schema_version="static-site-v3",
        market="HK",
        directory=tmp_path,
        history_service=_HistoryService(),
    )

    with pytest.raises(StaticGroupsRRGUnavailableError, match="history unavailable"):
        source.build(
            db=object(),
            generated_at="2026-04-18T22:00:00Z",
            expected_as_of_date=date(2026, 4, 18),
            market="HK",
            formula_version=BALANCED_RS_FORMULA_VERSION,
        )

    assert calls == ["prepare"]
    assert source.warnings == ("history unavailable",)


def test_rolling_source_bootstraps_short_prepared_history(monkeypatch, tmp_path):
    export_date = date(2026, 4, 18)
    plan = _plan(tmp_path, "HK")
    preparations = [
        StaticRRGHistoryPreparation(
            plan=plan,
            state=_state(
                market="HK",
                source_date=export_date,
                weeks=MIN_TAIL_WEEKS - 1,
            ),
            warnings=(
                f"Rolling RRG history has only {MIN_TAIL_WEEKS - 1} weekly points.",
            ),
        ),
        StaticRRGHistoryPreparation(
            plan=plan,
            state=_state(market="HK", source_date=export_date),
            warnings=("Rolling RRG history was advanced after bootstrap.",),
        ),
    ]
    calls = []

    class _HistoryService:
        def prepare(
            self,
            db,
            *,
            market,
            through_date,
            directory,
            formula_version,
        ):
            calls.append(
                ("prepare", db, market, through_date, directory, formula_version)
            )
            return preparations.pop(0)

        def persist(self, *_args, **_kwargs):
            raise AssertionError("persist is not part of this test")

    class _BootstrapService:
        def backfill(self, db, *, market, through_date, formula_version):
            calls.append(("bootstrap", db, market, through_date, formula_version))
            return StaticRRGBootstrapBackfillResult(
                status=StaticRRGBootstrapBackfillStatus.COMPLETED,
                market=market,
                as_of_date=through_date,
                formula_version=formula_version,
                lookback_start_date=date(2026, 1, 1),
                target_dates=(through_date,),
                processed=1,
                total_dates=1,
            )

    monkeypatch.setattr(
        rrg_export,
        "_build_payload_from_state",
        lambda **kwargs: {"weeks": len(kwargs["state"].weeks)},
    )
    source = StaticGroupsRRGRollingHistoryExportSession(
        schema_version="static-site-v3",
        market="HK",
        directory=tmp_path,
        history_service=_HistoryService(),
        bootstrap_service=_BootstrapService(),
    )
    db = object()

    assert source.build(
        db=db,
        generated_at="2026-04-18T22:00:00Z",
        expected_as_of_date=export_date,
        market="HK",
        formula_version=BALANCED_RS_FORMULA_VERSION,
    ) == {"weeks": MIN_TAIL_WEEKS}
    assert [call[0] for call in calls] == ["prepare", "bootstrap", "prepare"]
    assert calls[1] == (
        "bootstrap",
        db,
        "HK",
        export_date,
        BALANCED_RS_FORMULA_VERSION,
    )
    assert source.bootstrap_result == StaticRRGBootstrapBackfillResult(
        status=StaticRRGBootstrapBackfillStatus.COMPLETED,
        market="HK",
        as_of_date=export_date,
        formula_version=BALANCED_RS_FORMULA_VERSION,
        lookback_start_date=date(2026, 1, 1),
        target_dates=(export_date,),
        processed=1,
        total_dates=1,
    )
    assert source.warnings == (
        f"Rolling RRG history has only {MIN_TAIL_WEEKS - 1} weekly points.",
        "Rolling RRG history was advanced after bootstrap.",
    )


def test_rolling_source_bootstraps_missing_current_snapshot(monkeypatch, tmp_path):
    export_date = date(2026, 4, 18)
    plan = _plan(tmp_path, "HK")
    preparations = [
        StaticRRGHistoryPreparation(
            plan=plan,
            state=None,
            warnings=("Rolling RRG history was not advanced: no current snapshot.",),
            unavailable_reason=StaticRRGHistoryUnavailableReason.CURRENT_SNAPSHOT_MISSING,
        ),
        StaticRRGHistoryPreparation(
            plan=plan,
            state=_state(market="HK", source_date=export_date),
        ),
    ]
    calls = []

    class _HistoryService:
        def prepare(self, db, **kwargs):
            calls.append(("prepare", db, kwargs))
            return preparations.pop(0)

        def persist(self, *_args, **_kwargs):
            raise AssertionError("persist is not part of this test")

    class _BootstrapService:
        def backfill(self, db, *, market, through_date, formula_version):
            calls.append(("bootstrap", db, market, through_date, formula_version))
            return StaticRRGBootstrapBackfillResult(
                status=StaticRRGBootstrapBackfillStatus.COMPLETED,
                market=market,
                as_of_date=through_date,
                formula_version=formula_version,
                lookback_start_date=date(2026, 1, 1),
            )

    monkeypatch.setattr(
        rrg_export,
        "_build_payload_from_state",
        lambda **_kwargs: {"available": True},
    )
    source = StaticGroupsRRGRollingHistoryExportSession(
        schema_version="static-site-v3",
        market="HK",
        directory=tmp_path,
        history_service=_HistoryService(),
        bootstrap_service=_BootstrapService(),
    )

    assert source.build(
        db=object(),
        generated_at="2026-04-18T22:00:00Z",
        expected_as_of_date=export_date,
        market="HK",
        formula_version=BALANCED_RS_FORMULA_VERSION,
    ) == {"available": True}
    assert [call[0] for call in calls] == ["prepare", "bootstrap", "prepare"]


def test_rolling_source_prefers_bootstrap_error_when_reprepare_still_missing(
    tmp_path,
):
    export_date = date(2026, 4, 18)
    plan = _plan(tmp_path, "HK")
    preparations = [
        StaticRRGHistoryPreparation(
            plan=plan,
            state=None,
            warnings=("Rolling RRG history was not advanced: no current snapshot.",),
            unavailable_reason=StaticRRGHistoryUnavailableReason.CURRENT_SNAPSHOT_MISSING,
        ),
        StaticRRGHistoryPreparation(
            plan=plan,
            state=None,
            warnings=("Rolling RRG history was not advanced: no current snapshot.",),
            unavailable_reason=StaticRRGHistoryUnavailableReason.CURRENT_SNAPSHOT_MISSING,
        ),
    ]

    class _HistoryService:
        def prepare(self, *_args, **_kwargs):
            return preparations.pop(0)

        def persist(self, *_args, **_kwargs):
            raise AssertionError("persist is not part of this test")

    class _BootstrapService:
        def backfill(self, _db, *, market, through_date, formula_version):
            return StaticRRGBootstrapBackfillResult(
                status=StaticRRGBootstrapBackfillStatus.ERRORED,
                market=market,
                as_of_date=through_date,
                formula_version=formula_version,
                lookback_start_date=date(2026, 1, 1),
                error="bootstrap price history unavailable",
            )

    source = StaticGroupsRRGRollingHistoryExportSession(
        schema_version="static-site-v3",
        market="HK",
        directory=tmp_path,
        history_service=_HistoryService(),
        bootstrap_service=_BootstrapService(),
    )

    with pytest.raises(StaticGroupsRRGUnavailableError) as exc_info:
        source.build(
            db=object(),
            generated_at="2026-04-18T22:00:00Z",
            expected_as_of_date=export_date,
            market="HK",
            formula_version=BALANCED_RS_FORMULA_VERSION,
        )

    assert exc_info.value.reason == (
        "Static RRG bootstrap did not complete: bootstrap price history unavailable"
    )


def test_rolling_source_contains_bootstrap_exception_in_optional_path(tmp_path):
    export_date = date(2026, 4, 18)
    calls = []

    class _HistoryService:
        def prepare(self, *_args, **_kwargs):
            calls.append("prepare")
            return StaticRRGHistoryPreparation(
                plan=_plan(tmp_path, "HK"),
                state=None,
                warnings=("Rolling RRG history was not advanced: no current snapshot.",),
                unavailable_reason=StaticRRGHistoryUnavailableReason.CURRENT_SNAPSHOT_MISSING,
            )

        def persist(self, *_args, **_kwargs):
            raise AssertionError("persist is not part of this test")

    class _BootstrapService:
        def backfill(self, *_args, **_kwargs):
            calls.append("bootstrap")
            raise RuntimeError("calendar provider unavailable")

    source = StaticGroupsRRGRollingHistoryExportSession(
        schema_version="static-site-v3",
        market="HK",
        directory=tmp_path,
        history_service=_HistoryService(),
        bootstrap_service=_BootstrapService(),
    )

    with pytest.raises(StaticGroupsRRGUnavailableError) as exc_info:
        source.build(
            db=object(),
            generated_at="2026-04-18T22:00:00Z",
            expected_as_of_date=export_date,
            market="HK",
            formula_version=BALANCED_RS_FORMULA_VERSION,
        )

    assert calls == ["prepare", "bootstrap"]
    assert exc_info.value.reason == (
        "Static RRG bootstrap did not complete: calendar provider unavailable"
    )
    assert source.bootstrap_result == StaticRRGBootstrapBackfillResult(
        status=StaticRRGBootstrapBackfillStatus.ERRORED,
        market="HK",
        as_of_date=export_date,
        formula_version=BALANCED_RS_FORMULA_VERSION,
        lookback_start_date=export_date,
        errors=1,
        reason="bootstrap_exception",
        error="calendar provider unavailable",
    )


def test_rolling_source_does_not_bootstrap_sufficient_history(monkeypatch, tmp_path):
    export_date = date(2026, 4, 18)
    preparation = StaticRRGHistoryPreparation(
        plan=_plan(tmp_path, "HK"),
        state=_state(market="HK", source_date=export_date),
    )

    class _HistoryService:
        def prepare(self, *_args, **_kwargs):
            return preparation

        def persist(self, *_args, **_kwargs):
            raise AssertionError("persist is not part of this test")

    class _BootstrapService:
        def backfill(self, *_args, **_kwargs):
            raise AssertionError("sufficient history must not bootstrap")

    monkeypatch.setattr(
        rrg_export,
        "_build_payload_from_state",
        lambda **_kwargs: {"available": True},
    )
    source = StaticGroupsRRGRollingHistoryExportSession(
        schema_version="static-site-v3",
        market="HK",
        directory=tmp_path,
        history_service=_HistoryService(),
        bootstrap_service=_BootstrapService(),
    )

    assert source.build(
        db=object(),
        generated_at="2026-04-18T22:00:00Z",
        expected_as_of_date=export_date,
        market="HK",
        formula_version=BALANCED_RS_FORMULA_VERSION,
    ) == {"available": True}
    assert source.bootstrap_result is None


def test_rolling_source_rejects_wrong_market_before_preparation(tmp_path):
    history_service = SimpleNamespace(
        prepare=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("wrong-market source must fail before preparation")
        )
    )
    source = StaticGroupsRRGRollingHistoryExportSession(
        schema_version="static-site-v3",
        market="HK",
        directory=tmp_path,
        history_service=history_service,
    )

    with pytest.raises(ValueError, match="cannot build US"):
        source.build(
            db=object(),
            generated_at="2026-04-18T22:00:00Z",
            expected_as_of_date=date(2026, 4, 18),
            market="US",
            formula_version=BALANCED_RS_FORMULA_VERSION,
        )


def test_rolling_export_session_rejects_persist_before_build(tmp_path):
    source = StaticGroupsRRGRollingHistoryExportSession(
        schema_version="static-site-v3",
        market="HK",
        directory=tmp_path,
        history_service=SimpleNamespace(),
    )

    with pytest.raises(RuntimeError, match="must be built"):
        source.persist(exported_as_of_date=date(2026, 4, 18))


def test_database_source_has_one_explicit_build_path(monkeypatch):
    export_date = date(2026, 4, 18)
    state = _state(market="HK", source_date=export_date)
    calls = []
    history_service = SimpleNamespace(
        build=lambda db, *, market, through_date, formula_version: (
            calls.append((db, market, through_date, formula_version)) or state
        )
    )
    monkeypatch.setattr(
        rrg_export,
        "_build_payload_from_state",
        lambda **_kwargs: {"available": True},
    )
    source = StaticGroupsRRGDatabasePayloadSource(
        schema_version="static-site-v3",
        history_service=history_service,
    )
    db = object()

    assert source.build(
        db=db,
        generated_at="2026-04-18T22:00:00Z",
        expected_as_of_date=export_date,
        market="HK",
        formula_version=BALANCED_RS_FORMULA_VERSION,
    ) == {"available": True}
    assert calls == [(db, "HK", export_date, BALANCED_RS_FORMULA_VERSION)]
