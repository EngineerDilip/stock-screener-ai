"""Queue-time recording for one runtime bootstrap dispatch generation."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from uuid import uuid4

from app.services.bootstrap_run_manifest import BootstrapQueueState
from app.tasks.market_queues import normalize_market

logger = logging.getLogger(__name__)
RecordBootstrapRun = Callable[..., dict]


class BootstrapDispatchError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        primary_market: str,
        enabled_markets: Iterable[str],
        primary_task_id: str | None = None,
        market_task_ids: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.primary_market = primary_market
        self.enabled_markets = list(enabled_markets)
        self.primary_task_id = primary_task_id
        self.market_task_ids = dict(market_task_ids or {})

    @property
    def dispatched_any(self) -> bool:
        return self.primary_task_id is not None or bool(self.market_task_ids)


@dataclass
class BootstrapQueueManifestRecorder:
    primary_market: str
    enabled_markets: list[str]
    dispatch_id: str
    record_run: RecordBootstrapRun = field(repr=False)
    mark_bootstrap_failed: Callable[[], None] = field(repr=False)
    market_task_ids: dict[str, str] = field(default_factory=dict)
    fresh_install: bool = False
    pending_balanced_activation_markets: tuple[str, ...] = ()
    primary_task_id: str | None = None

    @classmethod
    def create(
        cls,
        *,
        primary_market: str,
        enabled_markets: Iterable[str],
        record_run: RecordBootstrapRun,
        mark_bootstrap_failed: Callable[[], None],
        fresh_install: bool = False,
        pending_balanced_activation_markets: Iterable[str] = (),
    ) -> BootstrapQueueManifestRecorder:
        return cls(
            primary_market=primary_market,
            enabled_markets=list(enabled_markets),
            dispatch_id=uuid4().hex,
            record_run=record_run,
            mark_bootstrap_failed=mark_bootstrap_failed,
            fresh_install=fresh_install,
            pending_balanced_activation_markets=tuple(
                pending_balanced_activation_markets
            ),
        )

    def record_queueing(self) -> None:
        self._record(BootstrapQueueState.QUEUEING)

    def record_dispatched_market(self, *, market: str, task_id: str) -> None:
        market_code = normalize_market(market)
        if market_code == self.primary_market:
            self.primary_task_id = task_id
        self.market_task_ids[market_code] = task_id
        self._record_safely(
            BootstrapQueueState.PARTIAL,
            warning="Failed to record partial bootstrap task manifest",
        )

    def record_queued(self) -> None:
        self._record(BootstrapQueueState.QUEUED)

    def record_dispatch_failed_safely(self) -> None:
        try:
            if self.primary_task_id is None and not self.market_task_ids:
                self.mark_bootstrap_failed()
                self._record(BootstrapQueueState.FAILED)
            else:
                self._record(BootstrapQueueState.DISPATCH_FAILED)
        except Exception:
            logger.warning(
                "Failed to record bootstrap dispatch failure",
                extra=self.log_extra(),
                exc_info=True,
            )

    def _record_safely(
        self,
        queue_state: BootstrapQueueState,
        *,
        warning: str,
    ) -> None:
        try:
            self._record(queue_state)
        except Exception:
            logger.warning(warning, extra=self.log_extra(), exc_info=True)

    def _record(self, queue_state: BootstrapQueueState) -> None:
        kwargs = {
            "primary_market": self.primary_market,
            "enabled_markets": self.enabled_markets,
            "dispatch_id": self.dispatch_id,
            "fresh_install": self.fresh_install,
            "primary_task_id": self.primary_task_id,
            "market_task_ids": self.market_task_ids,
            "queue_state": queue_state.value,
        }
        if self.pending_balanced_activation_markets:
            kwargs["pending_balanced_activation_markets"] = (
                self.pending_balanced_activation_markets
            )
        self.record_run(**kwargs)

    def log_extra(self) -> dict:
        return {
            "primary_market": self.primary_market,
            "enabled_markets": self.enabled_markets,
            "market_task_ids": self.market_task_ids,
        }

    def dispatch_error(self, exc: Exception) -> BootstrapDispatchError:
        return BootstrapDispatchError(
            str(exc),
            primary_market=self.primary_market,
            enabled_markets=self.enabled_markets,
            primary_task_id=self.primary_task_id,
            market_task_ids=self.market_task_ids,
        )


__all__ = ["BootstrapDispatchError", "BootstrapQueueManifestRecorder"]
