from __future__ import annotations

from datetime import date, timedelta

from app.domain.relative_strength import GroupSnapshotIdentity
from app.services.group_rank_snapshot_reader import GroupSnapshotIntegrityError


class _FormulaRepository:
    @staticmethod
    def active_formula(_db, *, market):
        assert market in {"US", "CA"}
        return "balanced-v1"


class _Calendar:
    def __init__(self, desired_dates):
        self.desired_dates = list(desired_dates)

    def trading_days(self, market, start, end):
        assert market in {"US", "CA"}
        assert start < end
        return self.desired_dates

    @staticmethod
    def last_completed_trading_day(_market):
        return date(2026, 6, 30)


class _Reader:
    def __init__(self, *, missing=(), invalid=()):
        self.missing = set(missing)
        self.invalid = set(invalid)

    def load_exact(self, _db, *, identity: GroupSnapshotIdentity, **_kwargs):
        if identity.as_of_date in self.invalid:
            raise GroupSnapshotIntegrityError("mixed run IDs")
        if identity.as_of_date in self.missing:
            return []
        return [
            {
                "industry_group": "Software",
                "rank": 1,
                "avg_rs_rating": 80.0,
                "num_stocks": 10,
            }
        ]


class _RRGProvider:
    def __init__(self, weekly_points=12):
        self.weekly_points = weekly_points
        self.calls = 0

    def get_all_groups_history(self, _db, *, market, days, as_of_date):
        self.calls += 1
        assert market == "US"
        points = [
            (as_of_date - timedelta(days=7 * offset), 50.0 + offset, 10)
            for offset in reversed(range(self.weekly_points))
        ]
        return as_of_date.isoformat(), {"Software": {"rank": 1}}, {
            "Software": points
        }


def _reference_days(current: date) -> tuple[date, ...]:
    return (
        current - timedelta(days=180),
        current - timedelta(days=90),
        current - timedelta(days=30),
        current - timedelta(days=7),
        current,
    )


def test_readiness_classifies_missing_invalid_rank_windows_and_rrg() -> None:
    from app.services.group_history_readiness_service import (
        GroupHistoryReadinessService,
    )

    current = date(2026, 6, 30)
    missing = current - timedelta(days=45)
    invalid = current - timedelta(days=60)
    desired = (*_reference_days(current), invalid, missing)
    provider = _RRGProvider(weekly_points=12)
    service = GroupHistoryReadinessService(
        calendar_service=_Calendar(desired),
        snapshot_reader=_Reader(missing=(missing,), invalid=(invalid,)),
        market_rs_repository=_FormulaRepository(),
        rrg_history_provider=provider,
    )

    report = service.evaluate(object(), market="US", through_date=current)

    assert report.formula_version == "balanced-v1"
    assert report.valid_dates == _reference_days(current)
    assert report.missing_dates == (missing,)
    assert report.invalid_dates == (invalid,)
    assert report.rank_change_ready == {
        "1w": True,
        "1m": True,
        "3m": True,
        "6m": True,
    }
    assert report.rrg_required is True
    assert report.rrg_usable_weeks == 12
    assert report.rrg_plottable_series == 1
    assert report.ready is False


def test_non_rrg_group_market_can_be_ready_without_rrg_provider_call() -> None:
    from app.services.group_history_readiness_service import (
        GroupHistoryReadinessService,
    )

    current = date(2026, 6, 30)
    provider = _RRGProvider(weekly_points=0)
    service = GroupHistoryReadinessService(
        calendar_service=_Calendar(_reference_days(current)),
        snapshot_reader=_Reader(),
        market_rs_repository=_FormulaRepository(),
        rrg_history_provider=provider,
    )

    report = service.evaluate(object(), market="CA", through_date=current)

    assert report.rrg_required is False
    assert report.rrg_usable_weeks == 0
    assert report.rrg_plottable_series == 0
    assert report.ready is True
    assert provider.calls == 0


def test_rrg_market_requires_twelve_provider_usable_weeks() -> None:
    from app.services.group_history_readiness_service import (
        GroupHistoryReadinessService,
    )

    current = date(2026, 6, 30)
    service = GroupHistoryReadinessService(
        calendar_service=_Calendar(_reference_days(current)),
        snapshot_reader=_Reader(),
        market_rs_repository=_FormulaRepository(),
        rrg_history_provider=_RRGProvider(weekly_points=11),
    )

    report = service.evaluate(object(), market="US", through_date=current)

    assert report.rrg_usable_weeks == 11
    assert report.rrg_plottable_series == 0
    assert report.ready is False


def test_readiness_uses_supplied_target_without_resolving_active_formula() -> None:
    from app.services.group_history_readiness_service import (
        GroupHistoryReadinessService,
    )
    from app.services.group_history_reconciliation import GroupHistoryTarget

    current = date(2026, 6, 30)

    class _UnexpectedFormulaLookup:
        @staticmethod
        def active_formula(*_args, **_kwargs):
            raise AssertionError("target formula must be authoritative")

    service = GroupHistoryReadinessService(
        calendar_service=_Calendar(_reference_days(current)),
        snapshot_reader=_Reader(),
        market_rs_repository=_UnexpectedFormulaLookup(),
        rrg_history_provider=_RRGProvider(weekly_points=12),
    )
    target = GroupHistoryTarget(
        market="US",
        formula_version="captured-formula-v1",
        through_date=current,
    )

    report = service.evaluate(object(), target=target)

    assert report.formula_version == "captured-formula-v1"
    assert report.market == "US"
    assert report.through_date == current
