"""Static-site RRG bootstrap universe policy."""

from __future__ import annotations

import hashlib
from datetime import date

from sqlalchemy.orm import Session

from app.models.stock_universe import StockUniverse
from app.services.point_in_time_universe_service import PointInTimeUniverse


STATIC_RRG_BOOTSTRAP_UNIVERSE_POLICY = "current_weekly_reference_static_bootstrap"


class StaticRRGBootstrapUniverse:
    """Resolve historical bootstrap membership from the current active universe."""

    @staticmethod
    def _universe_hash(symbols: tuple[str, ...]) -> str:
        payload = "".join(f"{symbol}\n" for symbol in symbols).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def resolve(
        self,
        db: Session,
        *,
        market: str,
        as_of_date: date,
    ) -> PointInTimeUniverse:
        normalized_market = str(market or "").strip().upper()
        symbols = tuple(
            symbol
            for symbol, in db.query(StockUniverse.symbol)
            .filter(
                StockUniverse.market == normalized_market,
                StockUniverse.active_filter(),
            )
            .order_by(StockUniverse.symbol.asc())
            .all()
        )
        return PointInTimeUniverse(
            market=normalized_market,
            as_of_date=as_of_date,
            symbols=symbols,
            universe_hash=self._universe_hash(symbols),
        )


__all__ = [
    "STATIC_RRG_BOOTSTRAP_UNIVERSE_POLICY",
    "StaticRRGBootstrapUniverse",
]
