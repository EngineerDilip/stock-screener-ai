from __future__ import annotations

from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any, Optional

from ...services.options_metrics import calculate_options_metrics, compute_options_metrics, compute_key_gamma_levels
from ...schemas.options_metrics import OptionsMetricsResponse

from ...services.yfinance_service import YFinanceService
import yfinance as yf
from ...wiring.bootstrap import get_redis_client
import json

router = APIRouter()


@router.post("/metrics", response_model=OptionsMetricsResponse)
async def post_options_metrics(payload: Dict[str, Any]):
  """Compute options exposure metrics.

  Payload may either include `options_chain` (list) or a `symbol` to fetch
  options from yfinance (nearest expiry). Optional `current_iv`,
  `iv_52w_low`, `iv_52w_high` may be provided; when missing and `symbol`
  is present, the endpoint will attempt to infer them from yfinance.
  """
  try:
    options_chain: Optional[List[Dict[str, Any]]] = payload.get("options_chain")
    symbol: Optional[str] = payload.get("symbol")

    if not options_chain and not symbol:
      raise HTTPException(status_code=422, detail="Missing options_chain or symbol in payload")

    # If symbol provided and options_chain absent, try cache then compute live.
    # (Previously this also called a now-removed _options_from_yfinance()
    # pre-fetch here that read a `gamma`/`delta` column yfinance doesn't
    # actually provide -- it always came back zeroed -- and whose result was
    # then thrown away anyway once `expiration` resolved below and
    # calculate_options_metrics() took over. That was two extra live yfinance
    # round-trips wasted on every cache miss for no effect.)
    if not options_chain and symbol:
      try:
        redis_client = get_redis_client()
        key = f"options_metrics:{symbol.upper()}"
        cached = redis_client.get(key)
        if cached:
          # cached value is precomputed metrics JSON; return directly
          return json.loads(cached)
      except Exception:
        # cache miss or error — fall back to live fetch
        pass

    expiration = payload.get("expiration")
    current_iv = payload.get("current_iv")
    iv_52w_low = payload.get("iv_52w_low")
    iv_52w_high = payload.get("iv_52w_high")

    if symbol and not options_chain and not expiration:
      try:
        ticker = yf.Ticker(symbol)
        expirations = getattr(ticker, 'options', []) or []
        if expirations:
          expiration = expirations[0]
      except Exception:
        expiration = None
      if not expiration:
        raise HTTPException(status_code=404, detail=f"No options data found for symbol {symbol}")

    if symbol and not options_chain and expiration:
      result = calculate_options_metrics(symbol, expiration)
      return result

    # If we have a symbol and any IV pieces are missing, try to fetch price range
    if symbol and (current_iv is None or iv_52w_low is None or iv_52w_high is None):
      try:
        svc = YFinanceService()
        pr = svc.get_price_range(symbol)
        if pr:
          if current_iv is None:
            # current_iv from impliedVol of near-the-money option is preferable,
            # but fall back to simple heuristic using last close volatility proxy
            current_iv = payload.get('current_iv') or None
          if iv_52w_low is None and pr.get('low_52w') is not None:
            iv_52w_low = pr.get('low_52w')
          if iv_52w_high is None and pr.get('high_52w') is not None:
            iv_52w_high = pr.get('high_52w')
      except Exception:
        pass

    result = compute_options_metrics(options_chain, current_iv=current_iv,
                     iv_52w_low=iv_52w_low, iv_52w_high=iv_52w_high)
    # pydantic model will validate/serialize
    return result
  except HTTPException:
    raise
  except Exception as exc:
    raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/analysis")
async def post_options_analysis(payload: Dict[str, Any]):
  """Comprehensive options exposure analysis including structural levels.

  Returns Call Wall, Put Wall, Flip Level, spot price, and strike-by-strike GEX/VEX/CEX.

  Not called by the frontend (it uses GET /analysis/{symbol}, backed by
  analyze_options_exposure in options_analysis_tasks.py, which derives gamma
  via Black-Scholes). This POST variant instead reads `gamma`/`vanna`/`charm`
  straight off the raw yfinance option chain rows, columns yfinance doesn't
  actually populate -- they come back 0.0 here. Left unfixed since nothing
  currently exercises this path; if you wire something to it, route gamma
  through the same _bs_gamma_delta helper the GET path uses instead of
  trusting these columns.
  """
  import pandas as pd
  from datetime import datetime
  
  try:
    symbol: Optional[str] = payload.get("symbol")
    if not symbol:
      raise HTTPException(status_code=422, detail="Missing symbol in payload")
    
    # Fetch options for next 3 expirations
    svc = YFinanceService()
    try:
      svc._wait_for_yfinance_rate_limit()
    except Exception:
      pass
    
    ticker = yf.Ticker(symbol)
    expirations = getattr(ticker, 'options', []) or []
    if not expirations:
      raise HTTPException(status_code=404, detail=f"No options data found for {symbol}")
    
    # Get spot price
    hist = ticker.history(period="1d")
    if hist.empty:
      raise HTTPException(status_code=404, detail=f"No market data found for {symbol}")
    spot_price = float(hist["Close"].iloc[-1])
    
    # Fetch next 3 expirations
    all_options = []
    for exp_date in expirations[:3]:
      try:
        oc = ticker.option_chain(exp_date)
        
        # Process calls
        for _, r in oc.calls.iterrows():
          try:
            oi = int(r.get('openInterest', 0) or 0)
            if oi == 0:
              continue
            
            all_options.append({
              "strike": float(r.get('strike', 0.0)),
              "type": "call",
              "gamma": float(r.get('gamma', 0.0) or 0.0),
              "vanna": float(r.get('vanna', 0.0) or 0.0),
              "charm": float(r.get('charm', 0.0) or 0.0),
              "openInterest": oi,
            })
          except (ValueError, TypeError):
            continue
        
        # Process puts
        for _, r in oc.puts.iterrows():
          try:
            oi = int(r.get('openInterest', 0) or 0)
            if oi == 0:
              continue
            
            all_options.append({
              "strike": float(r.get('strike', 0.0)),
              "type": "put",
              "gamma": float(r.get('gamma', 0.0) or 0.0),
              "vanna": float(r.get('vanna', 0.0) or 0.0),
              "charm": float(r.get('charm', 0.0) or 0.0),
              "openInterest": oi,
            })
          except (ValueError, TypeError):
            continue
      except Exception:
        continue
    
    if not all_options:
      raise HTTPException(status_code=404, detail=f"No options with OI found for {symbol}")
    
    # Convert to DataFrame
    df = pd.DataFrame(all_options)
    
    # Calculate dealer exposures (Short Calls, Long Puts)
    S = spot_price
    exposures = []
    
    for _, row in df.iterrows():
      option_type = row["type"]
      gamma = row["gamma"]
      vanna = row["vanna"]
      charm = row["charm"]
      oi = row["openInterest"]
      strike = row["strike"]
      
      # Market maker assumption: short calls, long puts
      if option_type == "call":
        sign = 1
      else:
        sign = -1
      
      call_gex = -sign * gamma * oi * 100 * S
      vex = -sign * vanna * oi * 100 * S if pd.notna(row.get("vanna")) else None
      cex = -sign * charm * oi * 100 * S if pd.notna(row.get("charm")) else None
      total_gex = call_gex
      
      exposures.append({
        "strike": strike,
        "type": option_type,
        "GEX": call_gex,
        "VEX": vex,
        "CEX": cex,
        "Total_GEX": total_gex,
      })
    
    exp_df = pd.DataFrame(exposures)
    
    # Aggregate by strike
    agg_df = exp_df.groupby("strike").agg({
      "GEX": "sum",
      "Total_GEX": "sum",
      "VEX": "sum",
      "CEX": "sum",
    }).reset_index()
    
    agg_df.columns = ["strike", "Call_GEX", "Total_GEX", "Total_VEX", "Total_CEX"]
    agg_df = agg_df.sort_values("strike").reset_index(drop=True)
    agg_df["Put_GEX"] = agg_df["Total_GEX"] - agg_df["Call_GEX"]

    strike_agg = {
      float(row["strike"]): {
        "call_gex": float(row["Call_GEX"]),
        "put_gex": float(row["Put_GEX"]),
        "total_gex": float(row["Total_GEX"]),
      }
      for _, row in agg_df.iterrows()
    }
    key_levels = compute_key_gamma_levels(strike_agg)
    call_wall = float(key_levels["call_wall"])
    call_wall_gex = float(strike_agg[call_wall]["call_gex"])
    put_wall = float(key_levels["put_wall"])
    put_wall_gex = float(strike_agg[put_wall]["put_gex"])

    flip_level = None
    flip_cumgex = None
    if key_levels["zero_gamma"] is not None:
      flip_level = float(key_levels["zero_gamma"])
      cum_total = agg_df["Total_GEX"].cumsum()
      flip_idx = agg_df.index[agg_df["strike"] == flip_level]
      if len(flip_idx) > 0:
        flip_cumgex = float(cum_total.iloc[flip_idx[0]])
    
    # Build strike-by-strike data for chart
    strikes_data = []
    for _, row in agg_df.iterrows():
      strikes_data.append({
        "strike": float(row["strike"]),
        "Total_GEX": float(row["Total_GEX"]),
        "Total_VEX": float(row["Total_VEX"]),
        "Total_CEX": float(row["Total_CEX"]),
      })
    
    return {
      "symbol": symbol.upper(),
      "spot_price": spot_price,
      "call_wall": {
        "strike": call_wall,
        "gex": call_wall_gex,
      },
      "put_wall": {
        "strike": put_wall,
        "gex": put_wall_gex,
      },
      "flip_level": {
        "strike": flip_level,
        "cumulative_gex": flip_cumgex,
      } if flip_level else None,
      "strikes": strikes_data,
    }
  
  except HTTPException:
    raise
  except Exception as exc:
    raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/analysis/{symbol}")
async def get_options_analysis(symbol: str):
  """Retrieve pre-computed options exposure analysis for a ticker.
  
  This endpoint returns cached results from the nightly batch job.
  If no cached results exist, analysis is computed on-demand (slower, 5-10s).
  """
  try:
    symbol = symbol.upper()
    
    # Try cache first
    try:
      redis_client = get_redis_client()
      cache_key = f"options_analysis:{symbol}"
      cached = redis_client.get(cache_key)
      if cached:
        return json.loads(cached)
    except Exception:
      pass  # Cache miss or error
    
    # If no cache, trigger on-demand analysis via Celery.
    # Must route to a queue an actual worker consumes -- the bare 'data_fetch'
    # name has no subscriber (see start_celery.sh / docker-compose.yml, both
    # only listen on the per-market data_fetch_<market> queues), so every
    # cache miss here previously dispatched into a void and reliably burned
    # the full 30s timeout below before returning a 504. This is the same
    # class of bug batch_analyze_options_exposure was already fixed for
    # (see options_analysis_tasks.py) but this on-demand path was missed.
    from ...tasks.market_queues import data_fetch_queue_for_market
    from ...tasks.options_analysis_tasks import analyze_options_exposure
    task_result = analyze_options_exposure.apply_async(
        args=[symbol], queue=data_fetch_queue_for_market("US")
    )
    
    # Wait up to 30 seconds for result
    try:
      analysis_result = task_result.get(timeout=30)
      return analysis_result
    except Exception as e:
      raise HTTPException(status_code=504, detail=f"Analysis timed out: {str(e)}")
  
  except HTTPException:
    raise
  except Exception as exc:
    raise HTTPException(status_code=500, detail=str(exc)) from exc
