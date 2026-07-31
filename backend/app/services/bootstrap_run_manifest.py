"""Persistence boundary for local runtime bootstrap task manifests."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any

from sqlalchemy.orm import Session

from ..models.app_settings import AppSetting

BOOTSTRAP_RUN_KEY = "runtime.activity.bootstrap_run"
RUNTIME_ACTIVITY_CATEGORY = "runtime_activity"
BOOTSTRAP_RUN_DESCRIPTION = "Latest local runtime bootstrap run task manifest."


class BootstrapQueueState(str, Enum):
    QUEUEING = "queueing"
    PARTIAL = "partial"
    QUEUED = "queued"
    DISPATCH_FAILED = "dispatch_failed"
    COMPLETED = "completed"
    FAILED = "failed"

    @classmethod
    def parse(cls, value: BootstrapQueueState | str) -> BootstrapQueueState:
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value))
        except ValueError as exc:
            raise ValueError(f"invalid bootstrap queue_state: {value}") from exc


class StaleBootstrapDispatch(RuntimeError):
    pass


class BootstrapAlreadyRunning(RuntimeError):
    pass


@dataclass(frozen=True)
class BootstrapRunManifest:
    primary_market: str
    enabled_markets: tuple[str, ...]
    dispatch_id: str | None = None
    ownership_version: int = 1
    fresh_install: bool = False
    pending_balanced_activation_markets: tuple[str, ...] = ()
    primary_task_id: str | None = None
    market_task_ids: Mapping[str, str | None] = field(default_factory=dict)
    completed_markets: tuple[str, ...] = ()
    failed_markets: tuple[str, ...] = ()
    queue_state: BootstrapQueueState | str = BootstrapQueueState.QUEUED
    queued_at: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "primary_market", str(self.primary_market).upper())
        object.__setattr__(
            self,
            "enabled_markets",
            tuple(str(market).upper() for market in self.enabled_markets),
        )
        pending = tuple(
            dict.fromkeys(
                str(market).upper()
                for market in self.pending_balanced_activation_markets
            )
        )
        unknown_pending = set(pending) - set(self.enabled_markets)
        if unknown_pending:
            raise ValueError(
                "pending balanced activation markets must be enabled: "
                + ", ".join(sorted(unknown_pending))
            )
        object.__setattr__(
            self,
            "pending_balanced_activation_markets",
            pending,
        )
        object.__setattr__(
            self,
            "market_task_ids",
            {
                str(market).upper(): str(task_id) if task_id is not None else None
                for market, task_id in self.market_task_ids.items()
            },
        )
        object.__setattr__(
            self,
            "completed_markets",
            tuple(
                dict.fromkeys(str(market).upper() for market in self.completed_markets)
            ),
        )
        object.__setattr__(
            self,
            "failed_markets",
            tuple(dict.fromkeys(str(market).upper() for market in self.failed_markets)),
        )
        object.__setattr__(
            self, "queue_state", BootstrapQueueState.parse(self.queue_state)
        )

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> BootstrapRunManifest:
        enabled_markets = tuple(payload.get("enabled_markets") or ())
        fresh_install = payload.get("fresh_install") is True
        pending_markets = (
            tuple(payload.get("pending_balanced_activation_markets") or ())
            if "pending_balanced_activation_markets" in payload
            else enabled_markets
            if fresh_install
            else ()
        )
        return cls(
            primary_market=str(payload["primary_market"]),
            enabled_markets=enabled_markets,
            dispatch_id=(
                str(payload["dispatch_id"])
                if payload.get("dispatch_id") is not None
                else None
            ),
            ownership_version=int(payload.get("ownership_version") or 0),
            fresh_install=fresh_install,
            pending_balanced_activation_markets=pending_markets,
            primary_task_id=(
                str(payload["primary_task_id"])
                if payload.get("primary_task_id") is not None
                else None
            ),
            market_task_ids=dict(payload.get("market_task_ids") or {}),
            completed_markets=tuple(payload.get("completed_markets") or ()),
            failed_markets=tuple(payload.get("failed_markets") or ()),
            queue_state=BootstrapQueueState.parse(
                payload.get("queue_state") or BootstrapQueueState.QUEUED
            ),
            queued_at=(
                str(payload["queued_at"])
                if payload.get("queued_at") is not None
                else None
            ),
        )

    @classmethod
    def create(
        cls,
        *,
        primary_market: str,
        enabled_markets: Iterable[str],
        dispatch_id: str | None = None,
        fresh_install: bool = False,
        pending_balanced_activation_markets: Iterable[str] | None = None,
        primary_task_id: str | None = None,
        market_task_ids: Mapping[str, str | None] | None = None,
        completed_markets: Iterable[str] = (),
        failed_markets: Iterable[str] = (),
        queue_state: BootstrapQueueState | str = BootstrapQueueState.QUEUED,
        queued_at: str | None = None,
    ) -> BootstrapRunManifest:
        normalized_enabled = tuple(enabled_markets)
        pending_markets = (
            tuple(pending_balanced_activation_markets)
            if pending_balanced_activation_markets is not None
            else normalized_enabled
            if fresh_install
            else ()
        )
        return cls(
            primary_market=primary_market,
            enabled_markets=normalized_enabled,
            dispatch_id=dispatch_id,
            fresh_install=fresh_install,
            pending_balanced_activation_markets=pending_markets,
            primary_task_id=primary_task_id,
            market_task_ids=dict(market_task_ids or {}),
            completed_markets=tuple(completed_markets),
            failed_markets=tuple(failed_markets),
            queue_state=queue_state,
            queued_at=queued_at,
        )

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "primary_market": self.primary_market,
            "enabled_markets": list(self.enabled_markets),
            "dispatch_id": self.dispatch_id,
            "ownership_version": self.ownership_version,
            "fresh_install": self.fresh_install,
            "pending_balanced_activation_markets": list(
                self.pending_balanced_activation_markets
            ),
            "primary_task_id": self.primary_task_id,
            "market_task_ids": dict(self.market_task_ids),
            "completed_markets": list(self.completed_markets),
            "failed_markets": list(self.failed_markets),
            "queue_state": self.queue_state.value,
        }
        if self.queued_at is not None:
            payload["queued_at"] = self.queued_at
        return payload

    def reconcile_terminal_state(self) -> BootstrapRunManifest:
        if self.queue_state == BootstrapQueueState.QUEUED:
            expected = set(self.enabled_markets)
        elif self.queue_state == BootstrapQueueState.DISPATCH_FAILED:
            expected = {
                market
                for market, task_id in self.market_task_ids.items()
                if task_id is not None
            }
        else:
            return self
        terminal = set(self.completed_markets) | set(self.failed_markets)
        if not expected or not expected <= terminal:
            return self
        terminal_state = (
            BootstrapQueueState.FAILED
            if self.failed_markets
            or self.queue_state == BootstrapQueueState.DISPATCH_FAILED
            else BootstrapQueueState.COMPLETED
        )
        return replace(self, queue_state=terminal_state)


class BootstrapRunManifestRepository:
    def load(self, db: Session) -> BootstrapRunManifest | None:
        setting = (
            db.query(AppSetting).filter(AppSetting.key == BOOTSTRAP_RUN_KEY).first()
        )
        if setting is None:
            return None
        try:
            payload = json.loads(setting.value)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(payload, dict):
            return None
        return BootstrapRunManifest.from_payload(payload)

    @staticmethod
    def _write_setting(
        setting: AppSetting | None,
        manifest: BootstrapRunManifest,
        db: Session,
    ) -> None:
        encoded = json.dumps(manifest.to_payload())
        if setting is None:
            db.add(
                AppSetting(
                    key=BOOTSTRAP_RUN_KEY,
                    value=encoded,
                    category=RUNTIME_ACTIVITY_CATEGORY,
                    description=BOOTSTRAP_RUN_DESCRIPTION,
                )
            )
            return
        setting.value = encoded
        setting.category = RUNTIME_ACTIVITY_CATEGORY
        setting.description = BOOTSTRAP_RUN_DESCRIPTION

    @staticmethod
    def _locked_setting(db: Session) -> AppSetting | None:
        return (
            db.query(AppSetting)
            .filter(AppSetting.key == BOOTSTRAP_RUN_KEY)
            .with_for_update()
            .first()
        )

    def begin_dispatch(
        self,
        db: Session,
        manifest: BootstrapRunManifest,
        *,
        commit: bool = True,
    ) -> dict[str, Any]:
        setting = self._locked_setting(db)
        if setting is not None:
            try:
                current = BootstrapRunManifest.from_payload(json.loads(setting.value))
            except (json.JSONDecodeError, TypeError, KeyError, ValueError):
                current = None
            if (
                current is not None
                and current.ownership_version >= 1
                and current.queue_state
                not in {
                    BootstrapQueueState.COMPLETED,
                    BootstrapQueueState.FAILED,
                }
            ):
                raise BootstrapAlreadyRunning(
                    f"Bootstrap dispatch {current.dispatch_id} is still active."
                )
        self._write_setting(setting, manifest, db)
        if commit:
            db.commit()
        return manifest.to_payload()

    def owns_dispatch(self, db: Session, *, dispatch_id: str) -> bool:
        current = self.load(db)
        return bool(
            current
            and current.dispatch_id == dispatch_id
            and current.queue_state
            not in {BootstrapQueueState.COMPLETED, BootstrapQueueState.FAILED}
        )

    def finish_market(
        self,
        db: Session,
        *,
        dispatch_id: str,
        market: str,
        succeeded: bool,
    ) -> BootstrapRunManifest:
        normalized = str(market).upper()

        def _finish(current: BootstrapRunManifest) -> BootstrapRunManifest:
            completed = current.completed_markets
            failed = current.failed_markets
            if succeeded:
                completed = tuple(dict.fromkeys((*completed, normalized)))
            else:
                failed = tuple(dict.fromkeys((*failed, normalized)))
            return replace(
                current,
                completed_markets=completed,
                failed_markets=failed,
            ).reconcile_terminal_state()

        return self.update_dispatch(db, dispatch_id=dispatch_id, transform=_finish)

    def update_dispatch(
        self,
        db: Session,
        *,
        dispatch_id: str,
        transform: Callable[[BootstrapRunManifest], BootstrapRunManifest],
    ) -> BootstrapRunManifest:
        setting = self._locked_setting(db)
        if setting is None:
            raise StaleBootstrapDispatch(
                f"Bootstrap dispatch {dispatch_id} has no persisted manifest."
            )
        try:
            current = BootstrapRunManifest.from_payload(json.loads(setting.value))
        except (json.JSONDecodeError, TypeError, KeyError, ValueError) as exc:
            raise StaleBootstrapDispatch(
                f"Bootstrap dispatch {dispatch_id} has an invalid manifest."
            ) from exc
        if current.dispatch_id != dispatch_id:
            raise StaleBootstrapDispatch(
                f"Bootstrap dispatch {dispatch_id} is stale; current dispatch is "
                f"{current.dispatch_id}."
            )
        updated = transform(current).reconcile_terminal_state()
        if updated.dispatch_id != dispatch_id:
            raise ValueError("Bootstrap dispatch updates cannot change dispatch_id.")
        self._write_setting(setting, updated, db)
        db.commit()
        return updated
