"""Versioned persistence for background Group history reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum
import json
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.app_settings import AppSetting


GROUP_HISTORY_RECONCILIATION_SCHEMA_VERSION = 1
GROUP_HISTORY_RECONCILIATION_CATEGORY = "group_history_reconciliation"
GROUP_HISTORY_RECONCILIATION_KEY_PREFIX = (
    f"runtime.group_history.v{GROUP_HISTORY_RECONCILIATION_SCHEMA_VERSION}."
)
GROUP_HISTORY_RECONCILIATION_ACTIVE_TIMEOUT = timedelta(hours=6)


class GroupHistoryReconciliationStatus(StrEnum):
    QUEUED = "queued"
    REPAIRING = "repairing"
    READY = "ready"
    INCOMPLETE = "incomplete"
    FAILED = "failed"


@dataclass(frozen=True)
class GroupHistoryReconciliationMarker:
    market: str
    formula_version: str
    through_date: date
    status: GroupHistoryReconciliationStatus
    updated_at: str
    counts: dict[str, Any] | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": GROUP_HISTORY_RECONCILIATION_SCHEMA_VERSION,
            "market": self.market,
            "formula_version": self.formula_version,
            "through_date": self.through_date.isoformat(),
            "status": self.status.value,
            "updated_at": self.updated_at,
            "counts": dict(self.counts or {}),
            "error": self.error,
        }


class GroupHistoryReconciliationRepository:
    @staticmethod
    def key(market: str) -> str:
        return f"{GROUP_HISTORY_RECONCILIATION_KEY_PREFIX}{str(market).upper()}"

    def load(
        self,
        db: Session,
        *,
        market: str,
    ) -> GroupHistoryReconciliationMarker | None:
        setting = (
            db.query(AppSetting)
            .filter(AppSetting.key == self.key(market))
            .one_or_none()
        )
        if setting is None:
            return None
        try:
            payload = json.loads(setting.value)
            if (
                int(payload.get("schema_version"))
                != GROUP_HISTORY_RECONCILIATION_SCHEMA_VERSION
            ):
                return None
            return GroupHistoryReconciliationMarker(
                market=str(payload["market"]).upper(),
                formula_version=str(payload["formula_version"]),
                through_date=date.fromisoformat(str(payload["through_date"])),
                status=GroupHistoryReconciliationStatus(str(payload["status"])),
                updated_at=str(payload["updated_at"]),
                counts=(
                    dict(payload["counts"])
                    if isinstance(payload.get("counts"), dict)
                    else None
                ),
                error=(str(payload["error"]) if payload.get("error") else None),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def reserve(
        self,
        db: Session,
        *,
        market: str,
        formula_version: str,
        through_date: date,
    ) -> bool:
        normalized_market = str(market).upper()
        existing = self.load(db, market=normalized_market)
        if (
            existing is not None
            and existing.formula_version == formula_version
            and existing.through_date == through_date
            and existing.status
            in {
                GroupHistoryReconciliationStatus.QUEUED,
                GroupHistoryReconciliationStatus.REPAIRING,
            }
            and not self._is_stale(existing)
        ):
            return False
        try:
            self.mark(
                db,
                market=normalized_market,
                formula_version=formula_version,
                through_date=through_date,
                status=GroupHistoryReconciliationStatus.QUEUED,
            )
        except IntegrityError:
            db.rollback()
            return False
        return True

    @staticmethod
    def _is_stale(marker: GroupHistoryReconciliationMarker) -> bool:
        try:
            updated_at = datetime.fromisoformat(marker.updated_at)
        except ValueError:
            return True
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        return (
            datetime.now(timezone.utc) - updated_at
            > GROUP_HISTORY_RECONCILIATION_ACTIVE_TIMEOUT
        )

    def mark(
        self,
        db: Session,
        *,
        market: str,
        formula_version: str,
        through_date: date,
        status: GroupHistoryReconciliationStatus,
        counts: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> GroupHistoryReconciliationMarker:
        normalized_market = str(market).upper()
        marker = GroupHistoryReconciliationMarker(
            market=normalized_market,
            formula_version=formula_version,
            through_date=through_date,
            status=status,
            updated_at=datetime.now(timezone.utc).isoformat(),
            counts=counts,
            error=error,
        )
        key = self.key(normalized_market)
        setting = db.query(AppSetting).filter(AppSetting.key == key).one_or_none()
        encoded = json.dumps(marker.as_dict(), sort_keys=True)
        if setting is None:
            db.add(
                AppSetting(
                    key=key,
                    value=encoded,
                    category=GROUP_HISTORY_RECONCILIATION_CATEGORY,
                    description=(
                        f"Group history repair state for {normalized_market}"
                    ),
                )
            )
        else:
            setting.value = encoded
            setting.category = GROUP_HISTORY_RECONCILIATION_CATEGORY
        db.commit()
        return marker


__all__ = [
    "GROUP_HISTORY_RECONCILIATION_SCHEMA_VERSION",
    "GROUP_HISTORY_RECONCILIATION_ACTIVE_TIMEOUT",
    "GroupHistoryReconciliationMarker",
    "GroupHistoryReconciliationRepository",
    "GroupHistoryReconciliationStatus",
]
