from __future__ import annotations

from datetime import date

from app.services.group_history_readiness_service import GroupHistoryReadinessReport


def _report(
    *,
    ready: bool,
    missing=(),
    invalid=(),
    supported: bool = True,
) -> GroupHistoryReadinessReport:
    through = date(2026, 6, 30)
    valid = tuple(
        day
        for day in (date(2026, 1, 2), date(2026, 6, 30))
        if day not in set(missing) | set(invalid)
    )
    return GroupHistoryReadinessReport(
        market="US",
        through_date=through,
        formula_version="balanced-v1" if supported else None,
        supported=supported,
        desired_dates=tuple(sorted((*valid, *missing, *invalid))),
        valid_dates=valid,
        missing_dates=tuple(missing),
        invalid_dates=tuple(invalid),
        rank_change_ready={"1w": ready, "1m": ready, "3m": ready, "6m": ready},
        ready=ready,
        reason=None if supported else "group_rankings_not_supported",
    )


class _Readiness:
    def __init__(self, *reports):
        self.reports = list(reports)

    def evaluate(self, _db, *, market, through_date):
        assert market in {"US", "SG"}
        assert through_date == date(2026, 6, 30)
        return self.reports.pop(0)


class _Coordinator:
    def __init__(self, fail_date=None):
        self.ensure_calls = []
        self.repair_calls = []
        self.fail_date = fail_date

    def ensure_snapshot(self, _db, *, identity):
        self.ensure_calls.append(identity)
        if identity.as_of_date == self.fail_date:
            raise RuntimeError("price anchor unavailable")

    def repair_snapshot(self, _db, *, identity):
        self.repair_calls.append(identity)
        if identity.as_of_date == self.fail_date:
            raise RuntimeError("repair failed")


class _Policies:
    @staticmethod
    def policy_for(_market, day):
        return "point_in_time" if day.day == 1 else "current_active_fallback_v1"


class _DB:
    def __init__(self):
        self.rollbacks = 0

    def rollback(self):
        self.rollbacks += 1


def test_group_history_bootstrap_processes_only_missing_and_invalid_oldest_first():
    from app.services.group_history_bootstrap_service import (
        GroupHistoryBootstrapService,
        GroupHistoryBootstrapStatus,
    )

    missing = date(2026, 5, 2)
    invalid = date(2026, 4, 1)
    coordinator = _Coordinator()
    service = GroupHistoryBootstrapService(
        readiness_service=_Readiness(
            _report(ready=False, missing=(missing,), invalid=(invalid,)),
            _report(ready=True),
        ),
        snapshot_coordinator=coordinator,
        universe_resolver=_Policies(),
    )

    result = service.ensure(_DB(), market="us", through_date=date(2026, 6, 30))

    assert result.status is GroupHistoryBootstrapStatus.READY
    assert [item.as_of_date for item in coordinator.repair_calls] == [invalid]
    assert [item.as_of_date for item in coordinator.ensure_calls] == [missing]
    assert result.processed_dates == (invalid, missing)
    assert result.failed_dates == ()
    assert result.skipped_valid == 2
    assert result.policy_counts == {
        "point_in_time": 1,
        "current_active_fallback_v1": 1,
    }
    assert result.after.ready is True


def test_group_history_bootstrap_rolls_back_failed_date_and_trusts_after_report():
    from app.services.group_history_bootstrap_service import (
        GroupHistoryBootstrapService,
        GroupHistoryBootstrapStatus,
    )

    failed = date(2026, 5, 2)
    db = _DB()
    service = GroupHistoryBootstrapService(
        readiness_service=_Readiness(
            _report(ready=False, missing=(failed,)),
            _report(ready=False, missing=(failed,)),
        ),
        snapshot_coordinator=_Coordinator(fail_date=failed),
        universe_resolver=_Policies(),
    )

    result = service.ensure(db, market="US", through_date=date(2026, 6, 30))

    assert result.status is GroupHistoryBootstrapStatus.INCOMPLETE
    assert result.failed_dates == (failed,)
    assert result.errors == ((failed, "price anchor unavailable"),)
    assert db.rollbacks == 1
    assert result.after.ready is False


def test_group_history_bootstrap_skips_unsupported_market():
    from app.services.group_history_bootstrap_service import (
        GroupHistoryBootstrapService,
        GroupHistoryBootstrapStatus,
    )

    coordinator = _Coordinator()
    result = GroupHistoryBootstrapService(
        readiness_service=_Readiness(_report(ready=True, supported=False)),
        snapshot_coordinator=coordinator,
        universe_resolver=_Policies(),
    ).ensure(_DB(), market="SG", through_date=date(2026, 6, 30))

    assert result.status is GroupHistoryBootstrapStatus.SKIPPED
    assert coordinator.ensure_calls == []
    assert coordinator.repair_calls == []
