from __future__ import annotations

from dataclasses import replace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models.app_settings  # noqa: F401
from app.database import Base


def test_bootstrap_run_manifest_repository_round_trips_market_task_ids():
    from app.services.bootstrap_run_manifest import (
        BootstrapRunManifest,
        BootstrapRunManifestRepository,
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        repository = BootstrapRunManifestRepository()
        manifest = BootstrapRunManifest(
            primary_market="US",
            enabled_markets=("US", "HK", "TW"),
            primary_task_id="primary-task-123",
            market_task_ids={
                "US": "primary-task-123",
                "HK": "background-task-2",
                "TW": "background-task-3",
            },
        )

        repository.begin_dispatch(db, manifest)
        loaded = repository.load(db)

        assert loaded == manifest
    finally:
        db.close()
        engine.dispose()


def test_bootstrap_run_manifest_repository_round_trips_queueing_manifest_without_task_ids():
    from app.services.bootstrap_run_manifest import (
        BootstrapRunManifest,
        BootstrapRunManifestRepository,
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        repository = BootstrapRunManifestRepository()
        manifest = BootstrapRunManifest(
            primary_market="us",
            enabled_markets=("us", "hk"),
            primary_task_id=None,
            market_task_ids={},
            queue_state="queueing",
        )

        repository.begin_dispatch(db, manifest)
        loaded = repository.load(db)

        assert loaded == BootstrapRunManifest(
            primary_market="US",
            enabled_markets=("US", "HK"),
            primary_task_id=None,
            market_task_ids={},
            queue_state="queueing",
        )
    finally:
        db.close()
        engine.dispose()


def test_bootstrap_run_manifest_rejects_unknown_queue_state():
    from app.services.bootstrap_run_manifest import BootstrapRunManifest

    with pytest.raises(ValueError, match="invalid bootstrap queue_state"):
        BootstrapRunManifest(
            primary_market="US",
            enabled_markets=("US",),
            queue_state="almost_queued",
        )


def test_bootstrap_manifest_round_trips_fresh_install() -> None:
    from app.services.bootstrap_run_manifest import BootstrapRunManifest

    manifest = BootstrapRunManifest.create(
        primary_market="US",
        enabled_markets=("US", "HK"),
        fresh_install=True,
    )

    assert manifest.to_payload()["fresh_install"] is True
    assert (
        BootstrapRunManifest.from_payload(manifest.to_payload()).fresh_install is True
    )


def test_bootstrap_manifest_treats_legacy_payload_as_non_fresh() -> None:
    from app.services.bootstrap_run_manifest import BootstrapRunManifest

    manifest = BootstrapRunManifest.from_payload(
        {"primary_market": "US", "enabled_markets": ["US"]}
    )

    assert manifest.fresh_install is False


def test_legacy_fresh_manifest_treats_all_enabled_markets_as_pending() -> None:
    from app.services.bootstrap_run_manifest import BootstrapRunManifest

    manifest = BootstrapRunManifest.from_payload(
        {
            "primary_market": "US",
            "enabled_markets": ["US", "HK"],
            "fresh_install": True,
        }
    )

    assert manifest.pending_balanced_activation_markets == ("US", "HK")


def test_manifest_round_trips_an_explicit_empty_pending_market_set() -> None:
    from app.services.bootstrap_run_manifest import BootstrapRunManifest

    manifest = BootstrapRunManifest.from_payload(
        {
            "primary_market": "US",
            "enabled_markets": ["US", "HK"],
            "fresh_install": True,
            "pending_balanced_activation_markets": [],
        }
    )

    assert manifest.pending_balanced_activation_markets == ()
    assert manifest.to_payload()["pending_balanced_activation_markets"] == []


def test_dispatch_update_preserves_consumed_pending_markets() -> None:
    from app.services.bootstrap_run_manifest import (
        BootstrapRunManifest,
        BootstrapRunManifestRepository,
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        repository = BootstrapRunManifestRepository()
        queueing = BootstrapRunManifest.create(
            primary_market="US",
            enabled_markets=("US", "HK"),
            fresh_install=True,
            dispatch_id="dispatch-a",
            queue_state="queueing",
        )
        repository.begin_dispatch(db, queueing)
        repository.update_dispatch(
            db,
            dispatch_id="dispatch-a",
            transform=lambda current: replace(
                current,
                pending_balanced_activation_markets=("HK",),
            ),
        )
        repository.update_dispatch(
            db,
            dispatch_id="dispatch-a",
            transform=lambda current: replace(current, queue_state="queued"),
        )

        loaded = repository.load(db)
        assert loaded.fresh_install is True
        assert loaded.pending_balanced_activation_markets == ("HK",)
    finally:
        db.close()
        engine.dispose()


def test_repository_rejects_a_new_dispatch_while_the_current_one_is_active() -> None:
    from app.services.bootstrap_run_manifest import (
        BootstrapAlreadyRunning,
        BootstrapRunManifest,
        BootstrapRunManifestRepository,
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        repository = BootstrapRunManifestRepository()
        repository.begin_dispatch(
            db,
            BootstrapRunManifest.create(
                primary_market="US",
                enabled_markets=("US",),
                fresh_install=False,
                dispatch_id="dispatch-a",
            ),
        )

        with pytest.raises(BootstrapAlreadyRunning, match="dispatch-a"):
            repository.begin_dispatch(
                db,
                BootstrapRunManifest.create(
                    primary_market="US",
                    enabled_markets=("US",),
                    fresh_install=True,
                    dispatch_id="dispatch-b",
                ),
            )

        assert repository.load(db).dispatch_id == "dispatch-a"
    finally:
        db.close()
        engine.dispose()


def test_repository_can_supersede_a_legacy_manifest_without_generation_ownership() -> (
    None
):
    from app.models.app_settings import AppSetting
    from app.services.bootstrap_run_manifest import (
        BOOTSTRAP_RUN_KEY,
        BootstrapRunManifest,
        BootstrapRunManifestRepository,
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        db.add(
            AppSetting(
                key=BOOTSTRAP_RUN_KEY,
                value=(
                    '{"primary_market":"US","enabled_markets":["US"],'
                    '"dispatch_id":"legacy-dispatch","queue_state":"queued"}'
                ),
            )
        )
        db.commit()

        BootstrapRunManifestRepository().begin_dispatch(
            db,
            BootstrapRunManifest.create(
                primary_market="HK",
                enabled_markets=("HK",),
                dispatch_id="dispatch-current",
                queue_state="queueing",
            ),
        )

        assert BootstrapRunManifestRepository().load(db).dispatch_id == (
            "dispatch-current"
        )
    finally:
        db.close()
        engine.dispose()


def test_repository_allows_a_new_dispatch_after_the_current_one_is_terminal() -> None:
    from app.services.bootstrap_run_manifest import (
        BootstrapRunManifest,
        BootstrapRunManifestRepository,
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        repository = BootstrapRunManifestRepository()
        first = BootstrapRunManifest.create(
            primary_market="US",
            enabled_markets=("US",),
            dispatch_id="dispatch-a",
            queue_state="queueing",
        )
        completed = replace(first, queue_state="completed")
        second = replace(first, dispatch_id="dispatch-b")
        repository.begin_dispatch(db, first)
        repository.update_dispatch(
            db,
            dispatch_id="dispatch-a",
            transform=lambda _manifest: completed,
        )
        repository.begin_dispatch(db, second)

        assert repository.load(db).dispatch_id == "dispatch-b"
    finally:
        db.close()
        engine.dispose()


def test_queued_update_reconciles_a_market_that_completed_before_task_id_recording() -> (
    None
):
    from app.services.bootstrap_run_manifest import (
        BootstrapQueueState,
        BootstrapRunManifest,
        BootstrapRunManifestRepository,
    )

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    try:
        repository = BootstrapRunManifestRepository()
        repository.begin_dispatch(
            db,
            BootstrapRunManifest.create(
                primary_market="US",
                enabled_markets=("US",),
                dispatch_id="dispatch-a",
                queue_state="queueing",
            ),
        )
        repository.finish_market(
            db,
            dispatch_id="dispatch-a",
            market="US",
            succeeded=True,
        )
        repository.update_dispatch(
            db,
            dispatch_id="dispatch-a",
            transform=lambda current: replace(
                current,
                primary_task_id="task-us",
                market_task_ids={"US": "task-us"},
                queue_state="queued",
            ),
        )

        assert repository.load(db).queue_state == BootstrapQueueState.COMPLETED
    finally:
        db.close()
        engine.dispose()
