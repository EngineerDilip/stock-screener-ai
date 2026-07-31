"""Lifecycle for the one-time fresh-install balanced RS activation marker."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from sqlalchemy.orm import Session

from app.domain.relative_strength import BALANCED_RS_FORMULA_VERSION
from app.services.bootstrap_run_manifest import (
    BootstrapRunManifestRepository,
)


class ActiveFormulaReader(Protocol):
    def active_formula(self, db: Session, *, market: str) -> str: ...


class FreshBalancedRsBootstrapLifecycle:
    """Consume fresh-install identity once every enabled market is balanced."""

    def __init__(
        self,
        *,
        manifest_repository: BootstrapRunManifestRepository,
        formula_repository: ActiveFormulaReader,
    ) -> None:
        self.manifest_repository = manifest_repository
        self.formula_repository = formula_repository

    def consume_if_complete(self, db: Session) -> bool:
        manifest = self.manifest_repository.load(db)
        if manifest is None or not manifest.fresh_install:
            return False
        try:
            all_balanced = all(
                self.formula_repository.active_formula(db, market=market)
                == BALANCED_RS_FORMULA_VERSION
                for market in manifest.enabled_markets
            )
        except LookupError:
            return False
        if not all_balanced:
            return False
        self.manifest_repository.save(
            db,
            replace(manifest, fresh_install=False),
        )
        return True


__all__ = ["FreshBalancedRsBootstrapLifecycle"]
