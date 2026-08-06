"""Universe-wide options scanner backing the Options Command Center page.

Every ranking here reads the LATEST persisted OptionsMetricsSnapshot row per
active US-market ticker (see app/models/options_metrics_snapshot.py) --
there is no live per-request yfinance fetch here, this is intentionally a
read of whatever has already been captured by the nightly batch or by users
viewing the single-symbol dashboard. Coverage grows organically over time
and can be sparse (e.g. during the yfinance open-interest data gaps
documented on OptionsMetricsSnapshot) -- every ranking list may legitimately
come back shorter than its nominal "top 10", or empty, when too few
symbols have the fields that ranking needs.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ...database import get_db
from ...models.options_metrics_snapshot import OptionsMetricsSnapshot
from ...models.stock_universe import StockUniverse
from ...services.options_market_signal import evaluate_snapshot_signal

router = APIRouter()

_TOP_N = 10
_FLIP_PROXIMITY_PCT = 1.5


def _latest_snapshots_for_active_universe(db: Session) -> List[OptionsMetricsSnapshot]:
    """One row per active US-market ticker: whichever persisted snapshot is
    most recent for that ticker, regardless of source (live_full vs.
    batch_abbreviated) -- callers filter further by whichever fields their
    specific ranking actually needs."""
    subq = (
        db.query(OptionsMetricsSnapshot)
        .join(StockUniverse, StockUniverse.symbol == OptionsMetricsSnapshot.ticker)
        .filter(StockUniverse.active_filter(), StockUniverse.market == "US")
        .distinct(OptionsMetricsSnapshot.ticker)
        .order_by(OptionsMetricsSnapshot.ticker, OptionsMetricsSnapshot.fetched_at.desc())
    )
    return subq.all()


def _symbol_row(symbol: str, **fields: Any) -> Dict[str, Any]:
    return {"symbol": symbol, **fields}


def _rank_volatility_acceleration(rows: List[OptionsMetricsSnapshot]) -> List[Dict[str, Any]]:
    eligible = [r for r in rows if r.total_gex is not None]
    eligible.sort(key=lambda r: r.total_gex)
    return [
        _symbol_row(
            r.ticker,
            price=r.underlying_price,
            totalGex=r.total_gex,
            distanceToFlipPct=_pct_distance(r.underlying_price, r.zero_gamma),
            regime="short_gamma" if r.total_gex < 0 else "long_gamma",
        )
        for r in eligible[:_TOP_N]
    ]


def _pct_distance(spot: Optional[float], level: Optional[float]) -> Optional[float]:
    if spot is None or not level:
        return None
    return round((spot - level) / level * 100.0, 2)


def _rank_gamma_flip_proximity(rows: List[OptionsMetricsSnapshot]) -> List[Dict[str, Any]]:
    candidates = []
    for r in rows:
        distance = _pct_distance(r.underlying_price, r.zero_gamma)
        if distance is None:
            continue
        if abs(distance) <= _FLIP_PROXIMITY_PCT:
            candidates.append((abs(distance), r, distance))
    candidates.sort(key=lambda t: t[0])
    return [
        _symbol_row(r.ticker, spot=r.underlying_price, flipLevel=r.zero_gamma, distancePct=distance)
        for _, r, distance in candidates[:_TOP_N]
    ]


def _rank_rich_vrp(rows: List[OptionsMetricsSnapshot]) -> List[Dict[str, Any]]:
    eligible = [r for r in rows if r.current_atm_iv is not None and r.historical_volatility is not None]
    eligible.sort(key=lambda r: (r.current_atm_iv - r.historical_volatility), reverse=True)
    return [
        _symbol_row(
            r.ticker,
            iv=r.current_atm_iv,
            hv=r.historical_volatility,
            vrpPct=round((r.current_atm_iv - r.historical_volatility) * 100.0, 1),
        )
        for r in eligible[:_TOP_N]
    ]


def _rank_extreme_skew(rows: List[OptionsMetricsSnapshot]) -> List[Dict[str, Any]]:
    # Most negative skew = strongest call skew, matching the frontend's
    # "call IV exceeds put IV" bullish convention (see options_market_signal).
    eligible = [r for r in rows if r.skew is not None]
    eligible.sort(key=lambda r: r.skew)
    return [_symbol_row(r.ticker, skew=r.skew) for r in eligible[:_TOP_N]]


def _rank_net_premium_inflows(rows: List[OptionsMetricsSnapshot]) -> List[Dict[str, Any]]:
    eligible = [r for r in rows if r.call_premium_notional is not None and r.put_premium_notional is not None]
    eligible.sort(key=lambda r: (r.call_premium_notional - r.put_premium_notional), reverse=True)
    return [
        _symbol_row(
            r.ticker,
            callPremium=r.call_premium_notional,
            putPremium=r.put_premium_notional,
            netPremium=round(r.call_premium_notional - r.put_premium_notional, 2),
        )
        for r in eligible[:_TOP_N]
    ]


def _rank_unusual_volume_oi(rows: List[OptionsMetricsSnapshot]) -> List[Dict[str, Any]]:
    # Only live_full rows populate unusual_volume_json -- flatten every
    # ticker's flagged contracts into one list and take the highest ratios
    # across the whole universe, not per-symbol.
    contracts: List[Dict[str, Any]] = []
    for r in rows:
        for contract in (r.unusual_volume_json or []):
            ratio = contract.get("ratio")
            if ratio is None:
                continue
            contracts.append({
                "symbol": r.ticker,
                "strike": contract.get("strike"),
                "type": contract.get("type"),
                "volume": contract.get("volume"),
                "openInterest": contract.get("open_interest"),
                "ratio": ratio,
            })
    contracts.sort(key=lambda c: c["ratio"], reverse=True)
    return contracts[:_TOP_N]


def _generate_alerts(rows: List[OptionsMetricsSnapshot]) -> List[Dict[str, Any]]:
    """One alert per ticker whose latest snapshot has a strong enough
    Executive Signal score, plus a dedicated wall-breach alert regardless of
    the aggregate score -- see mockData.js's documented convention this
    mirrors (>= 4 critical, >= 1.5 warning, structural breach always at
    least warning)."""
    alerts: List[Dict[str, Any]] = []
    next_id = 1

    for r in rows:
        signal = evaluate_snapshot_signal(r)
        breached_call = r.underlying_price is not None and r.call_wall is not None and r.underlying_price >= r.call_wall
        breached_put = r.underlying_price is not None and r.put_wall is not None and r.underlying_price <= r.put_wall

        if breached_call or breached_put:
            wall = r.call_wall if breached_call else r.put_wall
            direction = "Call Wall" if breached_call else "Put Wall"
            severity = "critical" if abs(signal.score) >= 4 else "warning"
            alerts.append({
                "id": next_id,
                "severity": severity,
                "text": f"Gamma Squeeze Alert: ${r.ticker} breached {direction} (${wall:.2f})",
            })
            next_id += 1
            continue

        if abs(signal.score) >= 4:
            alerts.append({
                "id": next_id,
                "severity": "critical",
                "text": f"${r.ticker} Executive Signal: {signal.label} (score {signal.score:+.1f})",
            })
            next_id += 1
        elif abs(signal.score) >= 1.5:
            alerts.append({
                "id": next_id,
                "severity": "warning",
                "text": f"${r.ticker} Executive Signal: {signal.label} (score {signal.score:+.1f})",
            })
            next_id += 1

    return alerts[:20]


def _macro_index(row: Optional[OptionsMetricsSnapshot], symbol: str) -> Dict[str, Any]:
    if row is None:
        return {"symbol": symbol, "spot": None, "flipLevel": None, "callWall": None, "putWall": None, "regime": None}
    return {
        "symbol": symbol,
        "spot": row.underlying_price,
        "flipLevel": row.zero_gamma,
        "callWall": row.call_wall,
        "putWall": row.put_wall,
        "regime": "long_gamma" if (row.total_gex or 0) >= 0 else "short_gamma",
    }


@router.get("/")
def get_command_center_snapshot(db: Session = Depends(get_db)) -> Dict[str, Any]:
    """Everything the Options Command Center page needs in one call: macro
    SPY/QQQ levels, the six ranking tables, and generated alerts. All
    derived from persisted OptionsMetricsSnapshot rows -- see module
    docstring for why this can legitimately return sparse/empty lists.

    Note: there is no genuine $SPX gamma-regime figure here -- SPX index
    options aren't tracked separately, so SPY's own regime is used as a
    proxy rather than fabricating an aggregate. VIX term structure is
    intentionally absent: this app has no VIX futures data source at all
    (see MarketExposure.vix, a single spot value only), so there is nothing
    real to report.
    """
    rows = _latest_snapshots_for_active_universe(db)
    by_ticker = {r.ticker: r for r in rows}

    spy_row = by_ticker.get("SPY")
    qqq_row = by_ticker.get("QQQ")

    return {
        "macro": {
            "spxProxy": {
                "label": "$SPX (SPY proxy)",
                "regime": _macro_index(spy_row, "SPY")["regime"],
                "flipLevel": spy_row.zero_gamma if spy_row else None,
                "spot": spy_row.underlying_price if spy_row else None,
            },
            "indices": [_macro_index(spy_row, "SPY"), _macro_index(qqq_row, "QQQ")],
        },
        "volatilityAcceleration": _rank_volatility_acceleration(rows),
        "gammaFlipProximity": _rank_gamma_flip_proximity(rows),
        "richVrp": _rank_rich_vrp(rows),
        "extremeSkew": _rank_extreme_skew(rows),
        "netPremiumInflows": _rank_net_premium_inflows(rows),
        "unusualVolumeOi": _rank_unusual_volume_oi(rows),
        "alerts": _generate_alerts(rows),
        "coverage": {
            "activeUniverseSymbolsWithData": len(rows),
        },
    }
