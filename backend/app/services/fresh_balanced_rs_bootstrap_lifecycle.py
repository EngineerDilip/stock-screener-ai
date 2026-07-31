"""Per-market lifecycle for fresh-install balanced RS activation."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from sqlalchemy.orm import Session

from app.domain.relative_strength import BALANCED_RS_FORMULA_VERSION
from app.services.bootstrap_run_manifest import (
    BootstrapRunManifestRepository,
    StaleBootstrapDispatch,
)


class ActiveFormulaReader(Protocol):
    def active_formula(self, db: Session, *, market: str) -> str: ...


class FreshBalancedRsBootstrapLifecycle:
    """Remove one market from the activation set after it becomes balanced."""

    def __init__(
        self,
        *,
        manifest_repository: BootstrapRunManifestRepository,
        formula_repository: ActiveFormulaReader,
    ) -> None:
        self.manifest_repository = manifest_repository
        self.formula_repository = formula_repository

    def complete_market(
        self,
        db: Session,
        *,
        market: str,
        dispatch_id: str,
    ) -> bool:
        normalized = str(market).upper()
        try:
            active_formula = self.formula_repository.active_formula(
                db,
                market=normalized,
            )
        except LookupError:
            return False
        if active_formula != BALANCED_RS_FORMULA_VERSION:
            return False

        def _complete(manifest):
            pending = tuple(
                pending_market
                for pending_market in manifest.pending_balanced_activation_markets
                if pending_market != normalized
            )
            return replace(
                manifest,
                pending_balanced_activation_markets=pending,
            )

        try:
            self.manifest_repository.update_dispatch(
                db,
                dispatch_id=dispatch_id,
                transform=_complete,
            )
        except StaleBootstrapDispatch:
            return False
        return True


__all__ = ["FreshBalancedRsBootstrapLifecycle"]
