"""I/O boundary that builds price-refresh planner inputs."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from .price_history_coverage import PriceHistoryCoverage, classify_price_history
from .price_refresh_planning import (
    GitHubSeedOutcome,
    LIVE_TOP_UP_MODES,
    PriceRefreshMode,
    PriceRefreshPlan,
    PriceRefreshPlanningInput,
    plan_price_refresh_from_input,
)
from .runtime_diagnostics import log_runtime_stage


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceRefreshUniverse:
    symbols: tuple[str, ...]
    symbol_markets: dict[str, str]


def _normalize_symbols(symbols: Sequence[str]) -> tuple[str, ...]:
    return tuple(str(symbol).upper() for symbol in symbols)


def _key_market_refresh_symbols(
    market: str | None,
    normalize_market: Callable[[str], str],
) -> dict[str, str]:
    """Provider-supported key-market data symbols keyed to their market.

    Daily Snapshot cards (e.g. BTC-USD, ^VIX, DX-Y.NYB) are not part of the
    stock universe, so without this the server deployment never fetches them
    — the static-site pipeline fetches them explicitly and stays correct.
    """
    from ..domain.markets.key_markets import (
        KEY_MARKET_INSTRUMENTS_BY_MARKET,
        key_market_instruments,
    )
    from ..domain.providers.price_symbol_support import split_supported_price_symbols

    if market is not None:
        instruments = key_market_instruments(market)
    else:
        instruments = tuple(
            instrument
            for group in KEY_MARKET_INSTRUMENTS_BY_MARKET.values()
            for instrument in group
        )
    by_symbol = {
        instrument.data_symbol.upper(): normalize_market(instrument.market)
        for instrument in instruments
    }
    supported, _skipped = split_supported_price_symbols(list(by_symbol))
    return {symbol: by_symbol[symbol] for symbol in supported}


def load_active_price_refresh_universe(
    db: Session,
    *,
    market: str | None,
    effective_market: str,
    normalize_market: Callable[[str], str],
) -> PriceRefreshUniverse:
    from ..models.stock_universe import StockUniverse

    query = db.query(StockUniverse.symbol, StockUniverse.market).filter(
        StockUniverse.is_active == True
    )
    if market is not None:
        query = query.filter(StockUniverse.market == normalize_market(market))
    query = query.order_by(StockUniverse.market_cap.desc().nullslast())
    universe_rows = query.all()
    all_symbols = tuple(row.symbol for row in universe_rows)
    symbol_markets = {
        str(row.symbol).upper(): normalize_market(
            getattr(row, "market", None) or effective_market
        )
        for row in universe_rows
    }
    return PriceRefreshUniverse(symbols=all_symbols, symbol_markets=symbol_markets)


def extend_universe_with_key_market_symbols(
    universe: PriceRefreshUniverse,
    market: str | None,
    normalize_market: Callable[[str], str],
) -> PriceRefreshUniverse:
    """Append Daily Snapshot key-market symbols to a refresh universe.

    Composed into refresh planning only — readiness gating loads the plain
    universe and must not count instruments outside it.
    """
    key_market_symbols = _key_market_refresh_symbols(market, normalize_market)
    extra_symbols = tuple(
        symbol for symbol in key_market_symbols if symbol not in universe.symbol_markets
    )
    if not extra_symbols:
        return universe
    return PriceRefreshUniverse(
        symbols=universe.symbols + extra_symbols,
        symbol_markets={
            **universe.symbol_markets,
            **{symbol: key_market_symbols[symbol] for symbol in extra_symbols},
        },
    )


def build_price_refresh_planning_input(
    db: Session,
    *,
    mode: PriceRefreshMode | str,
    market: str | None,
    effective_market: str,
    normalize_market: Callable[[str], str],
    market_calendar_service,
    sync_github_seed: Callable[..., Mapping[str, Any]],
    recently_refreshed_filter: Callable[[Sequence[str]], Sequence[str]] | None = None,
    ensure_group_history: bool = False,
    group_history_price_coverage_service=None,
) -> PriceRefreshPlanningInput:
    parsed_mode = PriceRefreshMode.parse(mode)
    with log_runtime_stage(
        logger,
        "price_refresh.load_universe",
        market=effective_market,
        mode=parsed_mode.value,
    ):
        active_universe = load_active_price_refresh_universe(
            db,
            market=market,
            effective_market=effective_market,
            normalize_market=normalize_market,
        )
        universe = extend_universe_with_key_market_symbols(
            active_universe,
            market,
            normalize_market,
        )
    all_symbols = _normalize_symbols(universe.symbols)
    group_history_symbols = _normalize_symbols(active_universe.symbols)
    github_seed = None
    if parsed_mode in LIVE_TOP_UP_MODES and all_symbols and market is not None:
        with log_runtime_stage(
            logger,
            "price_refresh.sync_github_seed",
            market=effective_market,
            mode=parsed_mode.value,
            symbol_count=len(all_symbols),
        ):
            github_seed = GitHubSeedOutcome.from_mapping(
                sync_github_seed(db, market=effective_market, allow_stale=True)
            )

    target_as_of = None
    coverage = None
    if parsed_mode in LIVE_TOP_UP_MODES and all_symbols:
        target_as_of = market_calendar_service.last_completed_trading_day(effective_market)
        with log_runtime_stage(
            logger,
            "price_refresh.classify_coverage",
            market=effective_market,
            mode=parsed_mode.value,
            symbol_count=len(all_symbols),
            target_as_of=target_as_of.isoformat() if target_as_of else None,
        ):
            coverage = classify_price_history(
                db,
                symbols=all_symbols,
                as_of_date=target_as_of,
            )
            if ensure_group_history:
                if group_history_price_coverage_service is None:
                    from .group_history_price_coverage import (
                        GroupHistoryPriceCoverageService,
                    )

                    group_history_price_coverage_service = (
                        GroupHistoryPriceCoverageService(
                            calendar_service=market_calendar_service
                        )
                    )
                history_coverage = group_history_price_coverage_service.classify(
                    db,
                    market=effective_market,
                    through_date=target_as_of,
                    symbols=group_history_symbols,
                )
                incomplete = set(history_coverage.incomplete_symbols)
                coverage = PriceHistoryCoverage(
                    fresh=tuple(
                        symbol for symbol in coverage.fresh if symbol not in incomplete
                    ),
                    stale=tuple(
                        symbol for symbol in coverage.stale if symbol not in incomplete
                    ),
                    no_history=tuple(
                        dict.fromkeys((*coverage.no_history, *history_coverage.incomplete_symbols))
                    ),
                )

    auto_refresh_symbols = None
    if parsed_mode is PriceRefreshMode.AUTO and recently_refreshed_filter is not None:
        auto_refresh_symbols = _normalize_symbols(recently_refreshed_filter(all_symbols))

    return PriceRefreshPlanningInput(
        all_symbols=all_symbols,
        mode=parsed_mode,
        effective_market=effective_market,
        symbol_markets=universe.symbol_markets,
        github_seed=github_seed,
        coverage=coverage,
        target_as_of=target_as_of,
        auto_refresh_symbols=auto_refresh_symbols,
    )


def build_market_price_refresh_plan(
    db: Session,
    **kwargs,
) -> PriceRefreshPlan:
    return plan_price_refresh_from_input(
        build_price_refresh_planning_input(db, **kwargs)
    )
