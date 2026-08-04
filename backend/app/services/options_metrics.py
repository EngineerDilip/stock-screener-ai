"""Options chain exposure and volatility metrics utilities.

Expected input: options_chain: list[dict] where each dict contains:
  - strike: float
  - type: 'call'|'put'
  - gamma: float (per-contract gamma)
  - delta: float (per-contract delta; calls positive, puts negative)
  - vanna: float
  - charm: float
  - open_interest: int
  - iv: float (implied volatility, e.g., 0.45)

These helpers compute per-strike aggregate exposures (DEX/VEX/CEX), key
gamma levels, IVR and skew.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple
import math

import pandas as pd


def _coerce_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _is_valid_iv(value: Any) -> bool:
    if value is None:
        return False
    try:
        iv = float(value)
    except (TypeError, ValueError):
        return False
    return iv > 0.0


def _interpolate_zero_crossing(prev_strike: float, prev_cum: float, cur_strike: float, cur_cum: float) -> float:

    if cur_cum == prev_cum:
        return cur_strike
    if prev_cum == 0:
        return prev_strike
    if cur_cum == 0:
        return cur_strike
    fraction = (0 - prev_cum) / (cur_cum - prev_cum)
    return prev_strike + fraction * (cur_strike - prev_strike)


def aggregate_by_strike(options_chain: List[Dict[str, Any]]) -> Dict[float, Dict[str, Any]]:
    """Aggregate exposures by strike.

    Returns a dict keyed by strike with aggregated fields:
      call_gex, put_gex, total_gex, dex, vex, cex, oi, iv_sample_count, iv_avg
    """
    strikes: Dict[float, Dict[str, Any]] = {}
    for opt in options_chain:
        strike = float(opt["strike"])
        typ = opt.get("type", "call").lower()
        oi = int(opt.get("open_interest") or 0)
        delta = _coerce_float(opt.get("delta"), 0.0)
        gamma = _coerce_float(opt.get("gamma"), 0.0)
        vanna = _coerce_float(opt.get("vanna"))
        charm = _coerce_float(opt.get("charm"))
        iv = _coerce_float(opt.get("iv"))

        entry = strikes.setdefault(strike, {
            "strike": strike,
            "call_gex": 0.0,
            "put_gex": 0.0,
            "total_gex": 0.0,
            "dex": 0.0,
            "vex": 0.0,
            "cex": 0.0,
            "oi": 0,
            "iv_sum": 0.0,
            "iv_count": 0,
            "has_vex": False,
            "has_cex": False,
        })

        # GEX per contract approximated as gamma * oi * 100, with put-side gamma
        # represented as negative exposure.
        raw_gex = (gamma or 0.0) * oi * 100
        gex = raw_gex if typ == "call" else -raw_gex
        if typ == "call":
            entry["call_gex"] += max(gex, 0.0)
        else:
            entry["put_gex"] += min(gex, 0.0)

        entry["total_gex"] += gex

        # Exposure greeks per strike
        if delta is not None:
            entry["dex"] += delta * oi * 100
        if vanna is not None:
            entry["vex"] += vanna * oi * 100
            entry["has_vex"] = True
        if charm is not None:
            entry["cex"] += charm * oi * 100
            entry["has_cex"] = True
        entry["oi"] += oi

        if iv is not None:
            try:
                entry["iv_sum"] += float(iv)
                entry["iv_count"] += 1
            except Exception:
                pass

    # finalize iv avg
    for s in strikes.values():
        s["iv_avg"] = (s["iv_sum"] / s["iv_count"]) if s["iv_count"] else None
        if not s.pop("has_vex", False):
            s["vex"] = None
        if not s.pop("has_cex", False):
            s["cex"] = None
        # remove internal sums
        s.pop("iv_sum", None)
        s.pop("iv_count", None)

    return dict(sorted(strikes.items()))


def compute_key_gamma_levels(strike_agg: Dict[float, Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """Compute Call Wall, Put Wall, and Zero Gamma (gamma flip) price levels.

    Call Wall is the strike with the largest positive call-side GEX. Put Wall is
    the strike with the most negative put-side GEX (largest absolute negative
    value). Zero Gamma is estimated by cumulative total_gex crossing from
    negative to positive with linear interpolation between surrounding strikes.
    """
    if not strike_agg:
        return {"call_wall": None, "put_wall": None, "zero_gamma": None}

    strikes = sorted(strike_agg.keys())
    call_wall = None
    max_call_gex = float('-inf')
    put_wall = None
    min_put_gex = float('inf')

    cum = 0.0
    cum_list: List[Tuple[float, float]] = []  # (strike, cumulative)
    for k in strikes:
        entry = strike_agg[k]
        call_gex = float(entry.get("call_gex", 0.0) or 0.0)
        put_gex = float(entry.get("put_gex", 0.0) or 0.0)
        total = float(entry.get("total_gex", 0.0) or 0.0)

        if call_gex > 0.0 and call_gex > max_call_gex:
            max_call_gex = call_gex
            call_wall = k

        if put_gex < 0.0 and put_gex < min_put_gex:
            min_put_gex = put_gex
            put_wall = k

        cum += total
        cum_list.append((k, cum))

    zero_gamma = None
    for i in range(1, len(cum_list)):
        prev_strike, prev_cum = cum_list[i - 1]
        cur_strike, cur_cum = cum_list[i]
        if prev_cum < 0 and cur_cum >= 0:
            zero_gamma = _interpolate_zero_crossing(prev_strike, prev_cum, cur_strike, cur_cum)
            break

    return {"call_wall": call_wall, "put_wall": put_wall, "zero_gamma": zero_gamma}


def compute_net_exposures(strike_agg: Dict[float, Dict[str, Any]]) -> Dict[str, Any]:
    """Compute total net DEX/VEX/CEX across all strikes."""
    net_dex = 0.0
    net_vex = 0.0
    net_cex = 0.0
    for s in strike_agg.values():
        net_dex += s.get("dex", 0.0) or 0.0
        net_vex += s.get("vex", 0.0) or 0.0
        net_cex += s.get("cex", 0.0) or 0.0

    return {"net_dex": net_dex, "net_vex": net_vex, "net_cex": net_cex}


def compute_ivr(current_iv: Optional[float], iv_52w_low: Optional[float], iv_52w_high: Optional[float]) -> Optional[float]:
    if current_iv is None:
        return None
    if iv_52w_low is None or iv_52w_high is None:
        return None

    try:
        iv_low = float(iv_52w_low)
        iv_high = float(iv_52w_high)
        denom = iv_high - iv_low
        if denom <= 0:
            return None
        return (float(current_iv) - iv_low) / denom * 100.0
    except Exception:
        return None


def find_nearest_iv_for_delta(options_chain: List[Dict[str, Any]], target_delta: float, typ: str) -> Optional[float]:

    """Find IV of option with delta closest to target_delta for given side ('call' or 'put').
    target_delta should be a positive number representing absolute delta (eg 0.25).
    For puts, delta values are typically negative; we compare absolute values.
    """
    best = None
    best_diff = None
    for opt in options_chain:
        if opt.get("type", "call").lower() != typ:
            continue
        if not _is_valid_iv(opt.get("iv")):
            continue
        d = float(opt.get("delta") or 0.0)
        absd = abs(d)
        diff = abs(absd - abs(target_delta))
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best = opt

    if best is None:
        return None
    return best.get("iv")


def compute_skew(options_chain: List[Dict[str, Any]], target_delta: float = 0.25) -> Optional[float]:

    """Skew = IV(25-delta put) - IV(25-delta call) for same expiration approximation."""
    iv_put = find_nearest_iv_for_delta(options_chain, target_delta, "put")
    iv_call = find_nearest_iv_for_delta(options_chain, target_delta, "call")
    if iv_put is None or iv_call is None:
        return None
    try:
        return float(iv_put) - float(iv_call)
    except Exception:
        return None


def _safe_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except (TypeError, ValueError):
        return default


def _nearest_option_row(df: pd.DataFrame, underlying_price: float) -> Optional[pd.Series]:
    if df is None or df.empty:
        return None
    if "strike" not in df.columns:
        return None
    df = df.copy()
    df = df[df["strike"].notna()]
    if df.empty:
        return None
    idx = (df["strike"].sub(underlying_price).abs()).idxmin()
    return df.loc[idx]


def _compute_historical_volatility(history: pd.DataFrame) -> Optional[float]:
    if history is None or history.empty or "Close" not in history.columns:
        return None

    closes = history["Close"].dropna()
    if closes.shape[0] < 2:
        return None

    closes = closes.tail(21)
    returns = closes.pct_change().dropna().apply(math.log1p)
    if returns.empty:
        return None

    return float(returns.std(ddof=0) * math.sqrt(252))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_gamma_delta(spot: float, strike: float, time_years: float, vol: float, option_type: str) -> Tuple[float, float]:
    """Black-Scholes gamma and delta.

    yfinance option chains do not reliably include gamma/delta columns, so
    walls/skew must be derived from strike + implied volatility instead of a
    (usually absent or stale) provider-supplied greek.
    """
    if spot <= 0 or strike <= 0 or time_years <= 0 or vol <= 0:
        return 0.0, 0.0
    denominator = vol * math.sqrt(time_years)
    if denominator <= 0:
        return 0.0, 0.0
    d1 = (math.log(spot / strike) + 0.5 * vol * vol * time_years) / denominator
    gamma = _norm_pdf(d1) / (spot * denominator)
    delta = _norm_cdf(d1) if option_type == "call" else _norm_cdf(d1) - 1.0
    return gamma, delta


def _time_to_expiry_years(expiration: str) -> float:
    try:
        expiry_dt = datetime.strptime(expiration, "%Y-%m-%d").date()
        days_to_expiry = max((expiry_dt - datetime.utcnow().date()).days, 1)
    except (TypeError, ValueError):
        days_to_expiry = 1
    return max(days_to_expiry / 365.0, 1.0 / 365.0)


def _find_valid_atm_option(df: pd.DataFrame, underlying_price: float, min_iv: float = 0.01) -> Optional[pd.Series]:
    """Find the strike closest to the money that has a usable (non-degenerate) IV.

    yfinance sometimes reports impliedVolatility as 0 (or near-zero) for a
    stale/illiquid contract even when its strike is otherwise ATM. Using that
    value directly collapses VRP to roughly `-historical_volatility`. Walk
    outward from the nearest strike until a contract with a real IV is found.
    """
    if df is None or df.empty or "strike" not in df.columns or "impliedVolatility" not in df.columns:
        return None
    ordered = df.assign(_dist=(df["strike"] - underlying_price).abs()).sort_values("_dist")
    for _, row in ordered.iterrows():
        iv = row.get("impliedVolatility")
        if iv is not None and iv >= min_iv:
            return row
    return None


def _update_iv_history_and_get_range(ticker: str, current_iv: Optional[float]) -> Tuple[Optional[float], Optional[float]]:
    """Persist today's ATM IV in a rolling ~252-session Redis history per ticker
    and return the observed (low, high) so IV Rank can be computed.

    No dedicated historical-IV table exists in this codebase, so the range is
    bootstrapped incrementally from live metric calculations rather than
    mixing in stock-price 52-week highs/lows (a different quantity).
    """
    if current_iv is None or current_iv <= 0:
        return None, None
    try:
        from .redis_pool import get_redis_client
        redis_client = get_redis_client()
        if redis_client is None:
            return None, None
        key = f"iv_history:{ticker.upper()}"
        today = datetime.utcnow().strftime("%Y-%m-%d")
        redis_client.hset(key, today, str(current_iv))
        redis_client.expire(key, 400 * 24 * 3600)
        raw = redis_client.hgetall(key)
    except Exception:
        return None, None

    if not raw:
        return None, None

    entries = sorted(raw.items())[-252:]
    values: List[float] = []
    for _, v in entries:
        try:
            values.append(float(v))
        except (TypeError, ValueError):
            continue

    if len(values) < 2:
        return None, None
    return min(values), max(values)


def calculate_options_metrics(ticker: str, expiration: str) -> Dict[str, Any]:
    """Fetch an options chain from yfinance and compute institutional options metrics.

    The returned payload includes GEX, walls, PCR, premium, HV, VRP, and expected move.
    """
    from .yfinance_service import YFinanceService
    import yfinance as yf

    svc = YFinanceService()
    svc._wait_for_yfinance_rate_limit()

    yf_ticker = yf.Ticker(ticker)
    try:
        option_chain = yf_ticker.option_chain(expiration)
    except Exception as exc:
        raise ValueError(f"Could not fetch option chain for {ticker} {expiration}: {exc}") from exc

    calls = option_chain.calls if hasattr(option_chain, "calls") else pd.DataFrame()
    puts = option_chain.puts if hasattr(option_chain, "puts") else pd.DataFrame()

    history = svc.get_historical_data(ticker, period="1mo", interval="1d", use_cache=False)
    underlying_price = None
    historical_volatility = None
    if history is not None and not history.empty and "Close" in history.columns:
        closes = history["Close"].dropna()
        if not closes.empty:
            underlying_price = _safe_float(closes.iloc[-1])
            historical_volatility = _compute_historical_volatility(history)

    if underlying_price is None or underlying_price == 0.0:
        info = getattr(yf_ticker, "info", {}) or {}
        underlying_price = _safe_float(
            info.get("currentPrice")
            or info.get("regularMarketPrice")
            or info.get("previousClose")
        )

    if underlying_price == 0.0:
        raise ValueError(f"Unable to determine underlying price for {ticker}")

    calls = calls.copy() if not calls.empty else pd.DataFrame(columns=["strike", "openInterest", "volume", "lastPrice", "impliedVolatility", "gamma"])
    puts = puts.copy() if not puts.empty else pd.DataFrame(columns=["strike", "openInterest", "volume", "lastPrice", "impliedVolatility", "gamma"])

    time_years = _time_to_expiry_years(expiration)

    for df, option_type in ((calls, "call"), (puts, "put")):
        if "volume" not in df.columns:
            df["volume"] = 0
        if "lastPrice" not in df.columns:
            df["lastPrice"] = 0.0
        if "impliedVolatility" not in df.columns:
            df["impliedVolatility"] = df.get("impliedVol", 0.0)
        if "strike" not in df.columns:
            df["strike"] = 0.0
        df["volume"] = df["volume"].fillna(0).apply(_safe_float)
        df["lastPrice"] = df["lastPrice"].fillna(0).apply(_safe_float)
        df["openInterest"] = df["openInterest"].fillna(0).apply(_safe_int)
        df["impliedVolatility"] = df["impliedVolatility"].fillna(0).apply(_safe_float)
        df["strike"] = df["strike"].fillna(0).apply(_safe_float)

        # yfinance option chains do not reliably supply gamma/delta, so derive
        # both via Black-Scholes from strike + implied volatility rather than
        # trusting a (usually absent) provider-supplied greek column.
        greeks = [
            _bs_gamma_delta(underlying_price, strike, time_years, iv, option_type)
            for strike, iv in zip(df["strike"], df["impliedVolatility"])
        ]
        df["gamma"] = [g for g, _ in greeks]
        df["delta"] = [d for _, d in greeks]

    calls["call_gex"] = calls["gamma"] * calls["openInterest"] * 100 * underlying_price * 0.01
    puts["put_gex"] = puts["gamma"] * puts["openInterest"] * 100 * underlying_price * 0.01 * -1

    total_call_gex = float(calls["call_gex"].sum())
    total_put_gex = float(puts["put_gex"].sum())
    total_gex = total_call_gex + total_put_gex

    call_wall = None
    if not calls.empty and calls["call_gex"].gt(0).any():
        call_wall = float(calls.loc[calls["call_gex"].idxmax(), "strike"])

    put_wall = None
    if not puts.empty and puts["put_gex"].lt(0).any():
        put_wall = float(puts.loc[puts["put_gex"].idxmin(), "strike"])

    total_call_volume = float(calls["volume"].sum())
    total_put_volume = float(puts["volume"].sum())
    total_call_oi = float(calls["openInterest"].sum())
    total_put_oi = float(puts["openInterest"].sum())

    volume_pcr = None
    if total_call_volume > 0:
        volume_pcr = total_put_volume / total_call_volume

    oi_pcr = None
    if total_call_oi > 0:
        oi_pcr = total_put_oi / total_call_oi

    call_premium_notional = float((calls["volume"] * calls["lastPrice"] * 100).sum())
    put_premium_notional = float((puts["volume"] * puts["lastPrice"] * 100).sum())

    options_chain = []
    for _, row in calls.iterrows():
        options_chain.append({
            "strike": float(row["strike"]),
            "type": "call",
            "delta": float(row.get("delta", 0.0) or 0.0),
            "gamma": float(row["gamma"]),
            "vanna": float(row.get("vanna", 0.0) or 0.0),
            "charm": float(row.get("charm", 0.0) or 0.0),
            "open_interest": int(row["openInterest"]),
            "iv": float(row["impliedVolatility"]) if row["impliedVolatility"] > 0 else None,
        })
    for _, row in puts.iterrows():
        options_chain.append({
            "strike": float(row["strike"]),
            "type": "put",
            "delta": float(row.get("delta", 0.0) or 0.0),
            "gamma": float(row["gamma"]),
            "vanna": float(row.get("vanna", 0.0) or 0.0),
            "charm": float(row.get("charm", 0.0) or 0.0),
            "open_interest": int(row["openInterest"]),
            "iv": float(row["impliedVolatility"]) if row["impliedVolatility"] > 0 else None,
        })

    atm_call = _nearest_option_row(calls, underlying_price)
    atm_put = _nearest_option_row(puts, underlying_price)

    atm_strike = None
    if atm_call is not None:
        atm_strike = float(atm_call["strike"])
    elif atm_put is not None:
        atm_strike = float(atm_put["strike"])

    atm_call_last_price = float(atm_call["lastPrice"]) if atm_call is not None else None
    atm_put_last_price = float(atm_put["lastPrice"]) if atm_put is not None else None

    # A strictly-nearest-strike contract can report a stale/near-zero IV for
    # illiquid names (e.g. ACN), which collapses VRP to ~ -historical_volatility.
    # Walk outward to the nearest strike with a usable IV instead.
    atm_call_iv_row = _find_valid_atm_option(calls, underlying_price)
    atm_put_iv_row = _find_valid_atm_option(puts, underlying_price)
    atm_call_iv = float(atm_call_iv_row["impliedVolatility"]) if atm_call_iv_row is not None else None
    atm_put_iv = float(atm_put_iv_row["impliedVolatility"]) if atm_put_iv_row is not None else None
    current_atm_iv = atm_call_iv if atm_call_iv is not None else atm_put_iv

    expected_move = None
    if atm_call_last_price is not None and atm_put_last_price is not None:
        expected_move = atm_call_last_price + atm_put_last_price

    volatility_risk_premium = None
    if current_atm_iv is not None and historical_volatility is not None:
        volatility_risk_premium = current_atm_iv - historical_volatility

    iv_52w_low, iv_52w_high = _update_iv_history_and_get_range(ticker, current_atm_iv)
    result = compute_options_metrics(options_chain, current_iv=current_atm_iv,
                                      iv_52w_low=iv_52w_low, iv_52w_high=iv_52w_high)
    result.update({
        "ticker": ticker,
        "expiration": expiration,
        "underlying_price": underlying_price,
        "historical_volatility": historical_volatility,
        "current_atm_iv": current_atm_iv,
        "volatility_risk_premium": volatility_risk_premium,
        "expected_move": expected_move,
        "atm_strike": atm_strike,
        "volume_put_call_ratio": volume_pcr,
        "open_interest_put_call_ratio": oi_pcr,
        "call_premium_notional": call_premium_notional,
        "put_premium_notional": put_premium_notional,
        "total_call_gex": total_call_gex,
        "total_put_gex": total_put_gex,
        "total_gex": total_gex,
        "call_wall": call_wall,
        "put_wall": put_wall,
    })
    return result


def compute_options_metrics(options_chain: List[Dict[str, Any]], current_iv: Optional[float] = None,
                            iv_52w_low: Optional[float] = None, iv_52w_high: Optional[float] = None) -> Dict[str, Any]:
    """High-level composer that returns aggregated metrics and per-strike exposures."""
    strike_agg = aggregate_by_strike(options_chain)
    key_levels = compute_key_gamma_levels(strike_agg)
    net = compute_net_exposures(strike_agg)
    ivr = None
    if current_iv is not None:
        ivr = compute_ivr(
            float(current_iv),
            float(iv_52w_low) if iv_52w_low is not None else None,
            float(iv_52w_high) if iv_52w_high is not None else None,
        )

    skew = compute_skew(options_chain, target_delta=0.25)

    return {
        "key_levels": key_levels,
        "net": net,
        "ivr": ivr,
        "skew": skew,
        "strikes": list(strike_agg.values()),
    }
