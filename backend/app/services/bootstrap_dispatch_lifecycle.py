"""Atomic ownership boundary for local runtime bootstrap dispatches."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.services.bootstrap_run_manifest import (
    BootstrapAlreadyRunning,
    BootstrapRunManifest,
    BootstrapRunManifestRepository,
)
from app.services.runtime_preferences_service import _stage_runtime_preferences


def claim_runtime_bootstrap_dispatch(
    db: Session,
    *,
    manifest: BootstrapRunManifest,
) -> dict:
    """Persist preferences and generation ownership in one transaction."""
    try:
        _stage_runtime_preferences(
            db,
            primary_market=manifest.primary_market,
            enabled_markets=list(manifest.enabled_markets),
            bootstrap_state="running",
        )
        BootstrapRunManifestRepository().begin_dispatch(
            db,
            manifest,
            commit=False,
        )
        db.commit()
    except BootstrapAlreadyRunning:
        db.rollback()
        raise
    except IntegrityError as exc:
        db.rollback()
        raise BootstrapAlreadyRunning(
            "Another bootstrap dispatch claimed ownership concurrently."
        ) from exc
    except Exception:
        db.rollback()
        raise
    return manifest.to_payload()


__all__ = ["claim_runtime_bootstrap_dispatch"]
