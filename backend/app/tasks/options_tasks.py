from __future__ import annotations

from __future__ import annotations

from typing import List
import json
import logging

from celery import shared_task

from app.wiring.bootstrap import get_redis_client, initialize_process_runtime_services
from app.services.yfinance_service import YFinanceService
from app.services.options_metrics import compute_options_metrics

logger = logging.getLogger(__name__)


@shared_task(name='app.tasks.options_tasks.schedule_daily_update')
def schedule_daily_update(limit: int = 2000):
    """Fetch options for active US symbols (NYSE/NASDAQ) and cache metrics in Redis.

    Stores JSON at key `options_metrics:{symbol}` with a 7-day TTL.
    Default `limit` is higher to cover broader US listings.
    """
    try:
        # ensure runtime services initialized in worker process
        runtime = initialize_process_runtime_services()
        redis_client = get_redis_client()
        sus = runtime.stock_universe_service()
        session = runtime.session_factory()()

        # fetch active US symbols (includes NYSE/NASDAQ)
        symbols = sus.get_active_symbols(db=session, market='US', limit=limit)
        if not symbols:
            logger.info("options_tasks: no symbols found for prefetching")
            return {'fetched': 0}
        from app.services.rate_budget_policy import get_rate_budget_policy

        policy = get_rate_budget_policy()
        batch_size = policy.get_batch_size("yfinance", "US") or 150

        ysvc = YFinanceService()
        fetched = 0

        # process symbols in batches to avoid hitting provider limits
        for i in range(0, len(symbols), batch_size):
            batch = symbols[i:i + batch_size]
            for sym in batch:
                try:
                    # ensure we respect per-market limiter per symbol if needed
                    try:
                        ysvc._wait_for_yfinance_rate_limit()
                    except Exception:
                        pass
                    import yfinance as yf
                    t = yf.Ticker(sym)
                    exps = getattr(t, 'options', []) or []
                    if not exps:
                        continue
                    oc = t.option_chain(exps[0])
                    options_chain = []
                    for _, r in oc.calls.iterrows():
                        options_chain.append({
                            'strike': float(r.get('strike')),
                            'type': 'call',
                            'delta': float(r.get('delta') or 0.0) if 'delta' in r.index else 0.0,
                            'gamma': float(r.get('gamma') or 0.0) if 'gamma' in r.index else 0.0,
                            'vanna': 0.0,
                            'charm': 0.0,
                            'open_interest': int(r.get('openInterest') or 0),
                            'iv': float(r.get('impliedVolatility') or r.get('impliedVol') or 0.0),
                        })
                    for _, r in oc.puts.iterrows():
                        options_chain.append({
                            'strike': float(r.get('strike')),
                            'type': 'put',
                            'delta': float(r.get('delta') or 0.0) if 'delta' in r.index else 0.0,
                            'gamma': float(r.get('gamma') or 0.0) if 'gamma' in r.index else 0.0,
                            'vanna': 0.0,
                            'charm': 0.0,
                            'open_interest': int(r.get('openInterest') or 0),
                            'iv': float(r.get('impliedVolatility') or r.get('impliedVol') or 0.0),
                        })

                    if not options_chain:
                        continue

                    metrics = compute_options_metrics(options_chain)
                    key = f"options_metrics:{sym.upper()}"
                    try:
                        redis_client.set(key, json.dumps(metrics), ex=7 * 24 * 3600)
                    except Exception:
                        logger.debug("Failed to write options metrics to Redis for %s", sym)
                    fetched += 1
                except Exception as e:
                    logger.debug(f"Failed to fetch options for {sym}: {e}")
                    continue

            # wait between batches using the distributed rate limiter
            try:
                rl = runtime.rate_limiter()
                rl.wait_for_market('yfinance', 'US')
            except Exception:
                # best-effort: continue without blocking if rate limiter error
                pass

        logger.info("options_tasks: cached options metrics for %d symbols", fetched)
        return {'fetched': fetched}

    except Exception as e:
        logger.exception("options_tasks: unexpected error: %s", e)
        return {'error': str(e)}
