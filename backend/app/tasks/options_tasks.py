from __future__ import annotations

import logging

from celery import shared_task

from .options_analysis_tasks import batch_analyze_options_exposure

logger = logging.getLogger(__name__)


@shared_task(name='app.tasks.options_tasks.schedule_daily_update')
def schedule_daily_update(limit: int = 2000):
    """Trigger the consolidated options analysis batch for active US symbols.

    This used to run its own separate yfinance sweep over the whole US
    universe just to populate the `options_metrics:{symbol}` Redis cache
    (used by the Options Analytics Dashboard SummaryCards). That duplicated
    the fetch already done by options_analysis_tasks.analyze_options_exposure
    (Daily Batch Options Analysis), doubling external API calls and general-
    queue worker time. analyze_options_exposure now derives and caches
    options_metrics:{symbol} itself from the same fetched chain, so this task
    just delegates to the shared batch instead of fetching again.
    """
    logger.info("Triggering consolidated options analysis batch (options metrics is now derived from it)")
    return batch_analyze_options_exposure.delay(market='US', limit=limit)

