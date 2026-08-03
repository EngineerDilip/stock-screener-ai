"""Batch GEX computation for a ticker universe.

Reads a JSON config with ticker entries and writes per-ticker gamma exposure
snapshots to a JSON output file for downstream DB persistence.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime


class PermanentFetchError(Exception):
    """Raised when data for a ticker is structurally unavailable."""


def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        cfg = json.load(handle)

    tickers = []
    for entry in cfg.get("tickers", []):
        if isinstance(entry, str):
            tickers.append({"symbol": entry, "yahoo_ticker": entry, "company_name": None})
        else:
            symbol = entry.get("symbol")
            if not symbol:
                continue
            tickers.append(
                {
                    "symbol": symbol,
                    "yahoo_ticker": entry.get("yahoo_ticker", symbol),
                    "company_name": entry.get("company_name"),
                }
            )

    cfg["tickers"] = tickers
    cfg.setdefault("strike_range_pct", 20.0)
    cfg.setdefault("max_strikes", None)
    return cfg


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _bs_gamma(spot: float, strike: float, time_years: float, vol: float) -> float:
    if spot <= 0 or strike <= 0 or time_years <= 0 or vol <= 0:
        return 0.0
    numerator = math.log(spot / strike) + 0.5 * vol * vol * time_years
    denominator = vol * math.sqrt(time_years)
    if denominator <= 0:
        return 0.0
    d1 = numerator / denominator
    return _norm_pdf(d1) / (spot * vol * math.sqrt(time_years))


def _infer_flip_level(gex_by_strike: list[tuple[float, float]]) -> float | None:
    if not gex_by_strike:
        return None

    cumulative = 0.0
    previous = None
    for strike, gex in sorted(gex_by_strike, key=lambda item: item[0]):
        cumulative += gex
        if previous is not None and previous != 0 and ((previous < 0 <= cumulative) or (previous > 0 >= cumulative)):
            return strike
        previous = cumulative

    nearest = min(gex_by_strike, key=lambda item: abs(item[1]))
    return nearest[0]


def fetch_one(symbol_cfg: dict, strike_range_pct: float, max_strikes: int | None) -> dict:
    import yfinance as yf

    symbol = symbol_cfg["symbol"]
    yahoo_ticker = symbol_cfg.get("yahoo_ticker") or symbol

    ticker = yf.Ticker(yahoo_ticker)
    expirations = ticker.options
    if not expirations:
        raise PermanentFetchError("no options chain available")

    expiration = expirations[0]
    chain = ticker.option_chain(expiration)

    try:
        spot = float(ticker.fast_info["last_price"])
    except Exception:
        hist = ticker.history(period="1d")
        if hist.empty:
            raise PermanentFetchError("unable to resolve spot price")
        spot = float(hist["Close"].iloc[-1])

    calls = chain.calls
    puts = chain.puts
    if calls is None or puts is None or calls.empty or puts.empty:
        raise PermanentFetchError("empty options chain")

    if "impliedVolatility" not in calls.columns or "impliedVolatility" not in puts.columns:
        raise PermanentFetchError("implied volatility not available in options chain")

    expiry_dt = datetime.strptime(expiration, "%Y-%m-%d")
    days_to_expiry = max((expiry_dt.date() - datetime.utcnow().date()).days, 1)
    time_years = max(days_to_expiry / 365.0, 1.0 / 365.0)

    lo = spot * (1 - strike_range_pct / 100.0)
    hi = spot * (1 + strike_range_pct / 100.0)

    strike_pool = sorted(set(calls["strike"].tolist()) | set(puts["strike"].tolist()))
    strikes = [value for value in strike_pool if lo <= value <= hi]
    if max_strikes is not None and max_strikes > 0 and len(strikes) > max_strikes:
        atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
        half = max_strikes // 2
        start = max(0, atm_idx - half)
        end = min(len(strikes), start + max_strikes)
        start = max(0, end - max_strikes)
        strikes = strikes[start:end]

    call_rows = calls[calls["strike"].isin(strikes)]
    put_rows = puts[puts["strike"].isin(strikes)]

    call_oi_total = 0
    put_oi_total = 0
    call_gex_total = 0.0
    put_gex_total = 0.0
    by_strike = {}

    for _, row in call_rows.iterrows():
        strike = float(row["strike"])
        oi = int(row.get("openInterest", 0) or 0)
        iv = float(row.get("impliedVolatility", 0) or 0)
        if oi <= 0 or iv <= 0:
            continue
        gamma = _bs_gamma(spot, strike, time_years, iv)
        gex = gamma * oi * 100.0 * spot * spot * 0.01
        call_oi_total += oi
        call_gex_total += gex
        by_strike[strike] = by_strike.get(strike, 0.0) + gex

    for _, row in put_rows.iterrows():
        strike = float(row["strike"])
        oi = int(row.get("openInterest", 0) or 0)
        iv = float(row.get("impliedVolatility", 0) or 0)
        if oi <= 0 or iv <= 0:
            continue
        gamma = _bs_gamma(spot, strike, time_years, iv)
        gex = -gamma * oi * 100.0 * spot * spot * 0.01
        put_oi_total += oi
        put_gex_total += gex
        by_strike[strike] = by_strike.get(strike, 0.0) + gex

    total_gex = call_gex_total + put_gex_total
    flip_level = _infer_flip_level(list(by_strike.items()))
    distance_pct = None
    if flip_level not in (None, 0):
        distance_pct = ((spot - flip_level) / flip_level) * 100.0

    company_name = symbol_cfg.get("company_name")
    if not company_name:
        try:
            company_name = ticker.info.get("shortName") or symbol
        except Exception:
            company_name = symbol

    return {
        "symbol": symbol,
        "company_name": company_name,
        "status": "OK",
        "expiration": expiration,
        "spot_price": spot,
        "call_oi": call_oi_total,
        "put_oi": put_oi_total,
        "call_gex": call_gex_total,
        "put_gex": put_gex_total,
        "total_gex": total_gex,
        "flip_level": flip_level,
        "distance_to_flip_pct": distance_pct,
        "fetched_at": datetime.utcnow().isoformat(),
    }


def fetch_with_retry(
    symbol_cfg: dict,
    strike_range_pct: float,
    max_strikes: int | None,
    max_retry_minutes: float,
    retry_interval_sec: float,
) -> dict:
    symbol = symbol_cfg["symbol"]
    deadline = time.monotonic() + (max_retry_minutes * 60.0)
    attempt = 0
    last_error = "unknown"

    while True:
        attempt += 1
        try:
            result = fetch_one(symbol_cfg, strike_range_pct, max_strikes)
            print(f"[{symbol}] OK on attempt {attempt}", flush=True)
            return result
        except PermanentFetchError as exc:
            print(f"[{symbol}] permanent failure: {exc}", flush=True)
            return {
                "symbol": symbol,
                "company_name": symbol_cfg.get("company_name"),
                "status": "FAILED",
                "error": str(exc),
                "fetched_at": datetime.utcnow().isoformat(),
            }
        except Exception as exc:
            last_error = str(exc)

        if time.monotonic() >= deadline:
            print(f"[{symbol}] FAILED after {attempt} attempts: {last_error}", flush=True)
            return {
                "symbol": symbol,
                "company_name": symbol_cfg.get("company_name"),
                "status": "FAILED",
                "error": last_error,
                "fetched_at": datetime.utcnow().isoformat(),
            }

        time.sleep(retry_interval_sec)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compute full-universe GEX snapshots")
    parser.add_argument("config", help="Path to input JSON config")
    parser.add_argument("--output", default="gex_output.json", help="Path to output JSON")
    parser.add_argument("--max-retry-minutes", type=float, default=0.75)
    parser.add_argument("--retry-interval-sec", type=float, default=3.0)
    args = parser.parse_args()

    cfg = load_config(args.config)
    strike_range_pct = float(cfg.get("strike_range_pct", 20.0))
    raw_max_strikes = cfg.get("max_strikes")
    max_strikes = int(raw_max_strikes) if isinstance(raw_max_strikes, int) and raw_max_strikes > 0 else None

    rows = []
    for ticker_cfg in cfg.get("tickers", []):
        rows.append(
            fetch_with_retry(
                ticker_cfg,
                strike_range_pct=strike_range_pct,
                max_strikes=max_strikes,
                max_retry_minutes=args.max_retry_minutes,
                retry_interval_sec=args.retry_interval_sec,
            )
        )

    payload = {
        "generated_at": datetime.utcnow().isoformat(),
        "strike_range_pct": strike_range_pct,
        "max_strikes": max_strikes,
        "rows": rows,
    }

    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)

    ok = len([row for row in rows if row.get("status") == "OK"])
    failed = len(rows) - ok
    print(f"Done. {ok} OK, {failed} failed, total {len(rows)}", flush=True)


if __name__ == "__main__":
    main()
