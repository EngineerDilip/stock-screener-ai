"""Static-site RRG bootstrap universe policy."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.services.group_history_universe import CurrentActiveUniverse
from app.services.point_in_time_universe_service import PointInTimeUniverse


STATIC_RRG_BOOTSTRAP_UNIVERSE_POLICY = "current_weekly_reference_static_bootstrap"


class StaticRRGBootstrapUniverse(CurrentActiveUniverse):
    """Resolve historical bootstrap membership from the current active universe."""

    def resolve(
        self,
        db: Session,
        *,
        market: str,
        as_of_date: date,
    ) -> PointInTimeUniverse:
        return super().resolve(db, market=market, as_of_date=as_of_date)


__all__ = [
    "STATIC_RRG_BOOTSTRAP_UNIVERSE_POLICY",
    "StaticRRGBootstrapUniverse",
]
