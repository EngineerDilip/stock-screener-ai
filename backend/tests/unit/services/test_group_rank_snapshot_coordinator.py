from datetime import date
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from app.domain.relative_strength import (
    BALANCED_RS_FORMULA_VERSION,
    LEGACY_RS_FORMULA_VERSION,
    GroupSnapshotIdentity,
)
from app.services.group_rank_snapshot_coordinator import (
    GroupRankSnapshotCoordinator,
    GroupSnapshotStatus,
)


AS_OF = date(2026, 4, 10)


def _coordinator(reader, stock, canonical, legacy):
    return GroupRankSnapshotCoordinator(
        reader=reader,
        market_rs_snapshot_service=stock,
        canonical_group_service=canonical,
        legacy_group_service=legacy,
    )


def test_balanced_snapshot_never_calls_legacy(db_session):
    reader = Mock()
    reader.load_exact.side_effect = [[], [{"market_rs_run_id": 44}]]
    stock = Mock()
    stock.calculate.return_value.id = 44
    canonical = Mock()
    legacy = Mock()
    identity = GroupSnapshotIdentity("US", AS_OF, BALANCED_RS_FORMULA_VERSION)

    result = _coordinator(reader, stock, canonical, legacy).ensure_snapshot(
        db_session, identity=identity
    )

    assert result.status is GroupSnapshotStatus.PROCESSED
    stock.calculate.assert_called_once_with(
        db_session,
        market="US",
        as_of_date=AS_OF,
        formula_version=BALANCED_RS_FORMULA_VERSION,
    )
    canonical.calculate_and_store.assert_called_once()
    legacy.calculate_group_rankings.assert_not_called()


def test_legacy_snapshot_never_calls_canonical_stock_or_group(db_session):
    reader = Mock()
    reader.load_exact.side_effect = [[], [{"market_rs_run_id": None}]]
    stock = Mock()
    canonical = Mock()
    legacy = Mock()
    identity = GroupSnapshotIdentity("US", AS_OF, LEGACY_RS_FORMULA_VERSION)

    _coordinator(reader, stock, canonical, legacy).ensure_snapshot(
        db_session, identity=identity
    )

    legacy.calculate_group_rankings.assert_called_once_with(
        db_session,
        AS_OF,
        market="US",
        formula_version=LEGACY_RS_FORMULA_VERSION,
    )
    stock.calculate.assert_not_called()
    canonical.calculate_and_store.assert_not_called()


def test_legacy_snapshot_passes_historical_universe_to_calculation(db_session):
    reader = Mock()
    reader.load_exact.side_effect = [[], [{"market_rs_run_id": None}]]
    legacy = Mock()
    identity = GroupSnapshotIdentity("US", AS_OF, LEGACY_RS_FORMULA_VERSION)

    _coordinator(reader, Mock(), Mock(), legacy).ensure_snapshot(
        db_session,
        identity=identity,
        universe_symbols=("OLD", "HIST"),
    )

    legacy.calculate_group_rankings.assert_called_once_with(
        db_session,
        AS_OF,
        market="US",
        formula_version=LEGACY_RS_FORMULA_VERSION,
        universe_symbols=("OLD", "HIST"),
    )


def test_backfill_rolls_back_failed_date_before_processing_next(db_session):
    coordinator = _coordinator(Mock(), Mock(), Mock(), Mock())
    first = GroupSnapshotIdentity(
        "US", date(2026, 4, 9), BALANCED_RS_FORMULA_VERSION
    )
    second = GroupSnapshotIdentity("US", AS_OF, BALANCED_RS_FORMULA_VERSION)
    coordinator.ensure_snapshot = Mock(
        side_effect=[
            RuntimeError("database aborted"),
            Mock(
                status=GroupSnapshotStatus.PROCESSED,
                row_count=3,
                market_rs_run_id=8,
            ),
        ]
    )
    db_session.rollback = Mock(wraps=db_session.rollback)

    report = coordinator.backfill(
        db_session,
        identities=(first, second),
        continue_on_error=True,
    )

    assert db_session.rollback.call_count == 1
    assert [item.status for item in report.results] == [
        GroupSnapshotStatus.ERRORED,
        GroupSnapshotStatus.PROCESSED,
    ]
    assert coordinator.ensure_snapshot.call_args_list == [
        call(db_session, identity=first),
        call(db_session, identity=second),
    ]


def test_repair_snapshot_rebuilds_exact_balanced_identity(db_session):
    identity = GroupSnapshotIdentity("US", AS_OF, BALANCED_RS_FORMULA_VERSION)
    reader = Mock()
    reader.load_exact.return_value = [
        {"market_rs_run_id": 42, "industry_group": "Software"}
    ]
    stock = Mock()
    stock.calculate.return_value.id = 42
    canonical = Mock()

    result = _coordinator(reader, stock, canonical, Mock()).repair_snapshot(
        db_session,
        identity=identity,
    )

    assert result.status is GroupSnapshotStatus.PROCESSED
    stock.calculate.assert_called_once_with(
        db_session,
        market="US",
        as_of_date=AS_OF,
        formula_version=BALANCED_RS_FORMULA_VERSION,
        rebuild_incompatible=True,
    )
    canonical.calculate_and_store.assert_called_once_with(
        db_session,
        market="US",
        as_of_date=AS_OF,
        formula_version=BALANCED_RS_FORMULA_VERSION,
    )


def test_repair_snapshot_uses_real_legacy_calculation_path(db_session):
    identity = GroupSnapshotIdentity("US", AS_OF, LEGACY_RS_FORMULA_VERSION)
    reader = Mock()
    reader.load_exact.return_value = [
        {"market_rs_run_id": None, "industry_group": "Software"}
    ]
    legacy = Mock()
    legacy.ranking_repository.delete_range.return_value = 1

    result = _coordinator(reader, Mock(), Mock(), legacy).repair_snapshot(
        db_session,
        identity=identity,
    )

    assert result.status is GroupSnapshotStatus.PROCESSED
    legacy.ranking_repository.delete_range.assert_called_once_with(
        db_session,
        start_date=AS_OF,
        end_date=AS_OF,
        market="US",
        formula_version=LEGACY_RS_FORMULA_VERSION,
    )
    legacy.calculate_group_rankings.assert_called_once_with(
        db_session,
        AS_OF,
        market="US",
        formula_version=LEGACY_RS_FORMULA_VERSION,
    )


def test_repair_snapshot_rejects_empty_legacy_replacement(db_session):
    identity = GroupSnapshotIdentity("US", AS_OF, LEGACY_RS_FORMULA_VERSION)
    reader = Mock()
    legacy = Mock()
    legacy.calculate_group_rankings.return_value = SimpleNamespace(rankings=())

    with pytest.raises(RuntimeError, match="produced no rankings"):
        _coordinator(reader, Mock(), Mock(), legacy).repair_snapshot(
            db_session,
            identity=identity,
        )

    legacy.ranking_repository.delete_range.assert_called_once()
    reader.load_exact.assert_not_called()


def test_repair_snapshot_rejects_empty_balanced_replacement(db_session):
    identity = GroupSnapshotIdentity("US", AS_OF, BALANCED_RS_FORMULA_VERSION)
    reader = Mock()
    stock = Mock()
    stock.calculate.return_value.id = 42
    canonical = Mock()
    canonical.calculate_and_store.return_value = []

    with pytest.raises(RuntimeError, match="produced no rankings"):
        _coordinator(reader, stock, canonical, Mock()).repair_snapshot(
            db_session,
            identity=identity,
        )

    reader.load_exact.assert_not_called()
