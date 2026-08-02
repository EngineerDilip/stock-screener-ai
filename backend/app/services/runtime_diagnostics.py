"""Runtime diagnostics for long-running worker stages."""

from __future__ import annotations

import gc
import logging
import resource
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _max_rss_mb() -> float:
    raw_rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return round(raw_rss / (1024 * 1024), 2)
    return round(raw_rss / 1024, 2)


@contextmanager
def log_runtime_stage(logger, name: str, **extra) -> Iterator[None]:
    started = time.perf_counter()
    logger.info(
        "Runtime stage started: %s",
        name,
        extra={"runtime_stage": name, **extra},
    )
    try:
        yield
    except BaseException as exc:
        elapsed = round(time.perf_counter() - started, 3)
        logger.info(
            "Runtime stage failed: %s",
            name,
            extra={
                "runtime_stage": name,
                "elapsed_seconds": elapsed,
                "max_rss_mb": _max_rss_mb(),
                "exception_type": type(exc).__name__,
                **extra,
            },
            exc_info=True,
        )
        raise
    else:
        elapsed = round(time.perf_counter() - started, 3)
        logger.info(
            "Runtime stage finished: %s",
            name,
            extra={
                "runtime_stage": name,
                "elapsed_seconds": elapsed,
                "max_rss_mb": _max_rss_mb(),
                **extra,
            },
        )


def release_session_memory(
    db: Session,
    *,
    stage: str,
    collect: bool = True,
    end_transaction: bool = False,
) -> None:
    """Detach ORM state between heavy pipeline stages."""
    if end_transaction:
        try:
            _rollback_clean_transaction(db, stage=stage)
        except Exception:
            logger.debug(
                "Session transaction cleanup failed after %s",
                stage,
                exc_info=True,
            )
            if collect:
                gc.collect()
            raise
    try:
        db.expire_all()
        db.expunge_all()
    except Exception:
        logger.debug("Session cleanup failed after %s", stage, exc_info=True)
    if collect:
        gc.collect()


def _rollback_clean_transaction(db: Session, *, stage: str) -> None:
    in_transaction = getattr(db, "in_transaction", None)
    if not callable(in_transaction) or in_transaction() is not True:
        return
    if any(bool(getattr(db, attr, ())) for attr in ("new", "dirty", "deleted")):
        raise RuntimeError(
            "Cannot release session memory after "
            f"{stage}: transaction has pending ORM changes."
        )
    db.rollback()
