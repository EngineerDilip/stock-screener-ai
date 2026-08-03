"""
Celery tasks for options dealer exposure analysis.
Analyzes Gamma, Vanna, and Charm exposures for any stock ticker.
Includes batch job to precompute analysis for all active stocks.
"""

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd
import yfinance as yf
from celery import shared_task

from ..services.yfinance_service import YFinanceService
from ..services.rate_budget_policy import RateBudgetPolicy
from ..services.options_metrics import compute_key_gamma_levels
from ..wiring.bootstrap import get_redis_client
from ..models import StockUniverse
from ..database import SessionLocal

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2)
def batch_analyze_options_exposure(self, market: str = "US", limit: int = 2000) -> Dict[str, Any]:
    """
    Batch job to analyze options exposure for all active stocks in a market.
    
    Runs nightly to precompute structural levels (Call Wall, Put Wall, Flip Level)
    for all active tickers and cache results for 24+ hours.
    
    Args:
        market: Market code (default "US")
        limit: Maximum number of stocks to analyze (default 2000)
    
    Returns:
        Job summary: {analyzed: count, cached: count, errors: count, market: market}
    """
    try:
        session = SessionLocal()
        analyzed = 0
        errors = 0
        
        try:
            # Get all active symbols for the market
            active_symbols = (
                session.query(StockUniverse.symbol)
                .filter(StockUniverse.is_active == True, StockUniverse.market == market)
                .limit(limit)
                .all()
            )
            
            symbols = [s[0] for s in active_symbols]
            logger.info(f"Starting batch analysis for {len(symbols)} {market} stocks")
            
            # Get batch size from rate budget policy
            batch_size = RateBudgetPolicy.get_batch_size("yfinance", market)
            logger.info(f"Using batch size: {batch_size}")
            
            # Process in batches with rate limiting between batches
            for i in range(0, len(symbols), batch_size):
                batch = symbols[i : i + batch_size]
                
                for symbol in batch:
                    try:
                        # Run analysis for this symbol
                        analyze_options_exposure.apply_async(args=[symbol], queue='data_fetch')
                        analyzed += 1
                    except Exception as e:
                        logger.error(f"Error queuing analysis for {symbol}: {e}")
                        errors += 1
                
                # Wait between batches to respect rate limits
                try:
                    svc = YFinanceService()
                    svc.wait_for_market(market)
                    logger.info(f"Batch {i // batch_size + 1} complete, waiting before next batch...")
                except Exception:
                    pass
        
        finally:
            session.close()
        
        result = {
            "analyzed": analyzed,
            "errors": errors,
            "market": market,
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        logger.info(f"Batch analysis complete: {result}")
        return result
    
    except Exception as exc:
        logger.error(f"Batch analysis failed: {exc}")
        raise self.retry(exc=exc, countdown=60)


@shared_task(bind=True, max_retries=2)
def analyze_options_exposure(self, symbol: str) -> Dict[str, Any]:
    """
    Comprehensive options exposure analysis including structural levels.
    
    Returns Call Wall, Put Wall, Flip Level, spot price, and strike-by-strike GEX/VEX/CEX.
    Results are cached in Redis for 24 hours.
    
    Args:
        symbol: Stock ticker symbol (e.g., "AAPL")
    
    Returns:
        Analysis results dict with structural levels and strike data
    """
    try:
        symbol = symbol.upper()
        
        # Check cache first
        try:
            redis_client = get_redis_client()
            cache_key = f"options_analysis:{symbol}"
            cached = redis_client.get(cache_key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass  # Cache miss or error, proceed with analysis
        
        # Fetch options for next 3 expirations
        svc = YFinanceService()
        try:
            svc._wait_for_yfinance_rate_limit()
        except Exception:
            pass
        
        ticker = yf.Ticker(symbol)
        expirations = getattr(ticker, 'options', []) or []
        if not expirations:
            raise ValueError(f"No options data available for {symbol}")
        
        # Get spot price
        hist = ticker.history(period="1d")
        if hist.empty:
            raise ValueError(f"No market data found for {symbol}")
        spot_price = float(hist["Close"].iloc[-1])
        
        # Fetch next 3 expirations
        all_options = []
        for exp_date in expirations[:3]:
            try:
                oc = ticker.option_chain(exp_date)
                for df_source, option_type in ((oc.calls, 'call'), (oc.puts, 'put')):
                    if df_source is None or df_source.empty:
                        continue

                    df_source = df_source.copy()
                    df_source['openInterest'] = pd.to_numeric(df_source.get('openInterest', 0), errors='coerce').fillna(0).astype(int)
                    df_source = df_source[df_source['openInterest'] > 0]
                    if df_source.empty:
                        continue

                    df_source['strike'] = pd.to_numeric(df_source.get('strike', 0.0), errors='coerce').fillna(0.0)
                    df_source['gamma'] = pd.to_numeric(df_source.get('gamma', 0.0), errors='coerce').fillna(0.0)
                    df_source['vanna'] = pd.to_numeric(df_source.get('vanna', 0.0), errors='coerce').fillna(0.0)
                    df_source['charm'] = pd.to_numeric(df_source.get('charm', 0.0), errors='coerce').fillna(0.0)
                    df_source['type'] = option_type

                    all_options.append(df_source[['strike', 'type', 'gamma', 'vanna', 'charm', 'openInterest']])
            except Exception:
                continue

        if not all_options:
            raise ValueError(f"No options with OI found for {symbol}")

        df = pd.concat(all_options, ignore_index=True)

        S = spot_price
        df['sign'] = 1
        df.loc[df['type'] == 'put', 'sign'] = -1
        df['GEX'] = -df['sign'] * df['gamma'] * df['openInterest'] * 100 * S
        df['VEX'] = -df['sign'] * df['vanna'] * df['openInterest'] * 100 * S
        df['CEX'] = -df['sign'] * df['charm'] * df['openInterest'] * 100 * S
        df['Total_GEX'] = df['GEX']

        exp_df = df[['strike', 'type', 'GEX', 'VEX', 'CEX', 'Total_GEX']].copy()
        
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
        
        result = {
            "symbol": symbol,
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
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        # Cache results for 24 hours
        try:
            redis_client = get_redis_client()
            cache_key = f"options_analysis:{symbol}"
            redis_client.setex(cache_key, 86400, json.dumps(result))
        except Exception:
            pass  # Cache error, but result is still returned
        
        return result
    
    except Exception as exc:
        # Retry up to max_retries times
        raise self.retry(exc=exc, countdown=30)
