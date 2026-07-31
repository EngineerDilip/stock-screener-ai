"""Application use-case factories composed from process runtime services."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.use_cases.feature_store.build_daily_snapshot import (
        BuildDailyFeatureSnapshotUseCase,
    )
    from app.use_cases.feature_store.compare_runs import CompareFeatureRunsUseCase
    from app.use_cases.feature_store.list_runs import ListFeatureRunsUseCase
    from app.use_cases.scanning.create_scan import CreateScanUseCase
    from app.use_cases.scanning.explain_stock import ExplainStockUseCase
    from app.use_cases.scanning.export_scan_results import ExportScanResultsUseCase
    from app.use_cases.scanning.get_filter_options import GetFilterOptionsUseCase
    from app.use_cases.scanning.get_peers import GetPeersUseCase
    from app.use_cases.scanning.get_scan_results import GetScanResultsUseCase
    from app.use_cases.scanning.get_scan_symbols import GetScanSymbolsUseCase
    from app.use_cases.scanning.get_setup_details import GetSetupDetailsUseCase
    from app.use_cases.scanning.get_single_result import GetSingleResultUseCase
    from app.use_cases.scanning.run_bulk_scan import RunBulkScanUseCase


def get_create_scan_use_case() -> CreateScanUseCase:
    """Build the HTTP scan use case with its mandatory freshness gate."""
    from app.services.market_data_freshness import evaluate_symbol_freshness
    from app.use_cases.scanning.create_scan import CreateScanUseCase
    from app.wiring.bootstrap import get_task_dispatcher

    return CreateScanUseCase(
        dispatcher=get_task_dispatcher(),
        freshness_evaluator=evaluate_symbol_freshness,
    )


def get_create_scan_use_case_without_freshness_gate() -> CreateScanUseCase:
    """Build the internal bootstrap scan use case without a freshness gate."""
    from app.use_cases.scanning.create_scan import CreateScanUseCase
    from app.wiring.bootstrap import get_task_dispatcher

    return CreateScanUseCase(
        dispatcher=get_task_dispatcher(),
        freshness_evaluator=None,
    )


def get_get_scan_results_use_case() -> GetScanResultsUseCase:
    from app.use_cases.scanning.get_scan_results import GetScanResultsUseCase

    return GetScanResultsUseCase()


def get_get_scan_symbols_use_case() -> GetScanSymbolsUseCase:
    from app.use_cases.scanning.get_scan_symbols import GetScanSymbolsUseCase

    return GetScanSymbolsUseCase()


def get_get_filter_options_use_case() -> GetFilterOptionsUseCase:
    from app.use_cases.scanning.get_filter_options import GetFilterOptionsUseCase

    return GetFilterOptionsUseCase()


def get_get_single_result_use_case() -> GetSingleResultUseCase:
    from app.use_cases.scanning.get_single_result import GetSingleResultUseCase

    return GetSingleResultUseCase()


def get_get_setup_details_use_case() -> GetSetupDetailsUseCase:
    from app.use_cases.scanning.get_setup_details import GetSetupDetailsUseCase

    return GetSetupDetailsUseCase()


def get_get_peers_use_case() -> GetPeersUseCase:
    from app.use_cases.scanning.get_peers import GetPeersUseCase

    return GetPeersUseCase()


def get_export_scan_results_use_case() -> ExportScanResultsUseCase:
    from app.use_cases.scanning.export_scan_results import ExportScanResultsUseCase

    return ExportScanResultsUseCase()


def get_run_bulk_scan_use_case() -> RunBulkScanUseCase:
    from app.use_cases.scanning.run_bulk_scan import RunBulkScanUseCase
    from app.wiring.bootstrap import (
        get_market_rs_reader,
        get_scan_orchestrator,
        get_stock_data_provider,
    )

    return RunBulkScanUseCase(
        scanner=get_scan_orchestrator(),
        data_provider=get_stock_data_provider(),
        market_rs_reader=get_market_rs_reader(),
    )


def get_explain_stock_use_case() -> ExplainStockUseCase:
    from app.use_cases.scanning.explain_stock import ExplainStockUseCase

    return ExplainStockUseCase()


def get_list_feature_runs_use_case() -> ListFeatureRunsUseCase:
    from app.use_cases.feature_store.list_runs import ListFeatureRunsUseCase

    return ListFeatureRunsUseCase()


def get_compare_feature_runs_use_case() -> CompareFeatureRunsUseCase:
    from app.use_cases.feature_store.compare_runs import CompareFeatureRunsUseCase

    return CompareFeatureRunsUseCase()


def get_build_daily_snapshot_use_case() -> BuildDailyFeatureSnapshotUseCase:
    from app.services.bootstrap_cache_coverage import (
        evaluate_bootstrap_cache_coverage,
    )
    from app.use_cases.feature_store.build_daily_snapshot import (
        BuildDailyFeatureSnapshotUseCase,
    )
    from app.wiring.bootstrap import (
        get_market_calendar_service,
        get_market_rs_reader,
        get_scan_orchestrator,
        get_stock_data_provider,
    )

    return BuildDailyFeatureSnapshotUseCase(
        scanner=get_scan_orchestrator(),
        data_provider=get_stock_data_provider(),
        market_calendar=get_market_calendar_service(),
        market_rs_reader=get_market_rs_reader(),
        bootstrap_coverage_evaluator=evaluate_bootstrap_cache_coverage,
    )


__all__ = [
    "get_build_daily_snapshot_use_case",
    "get_compare_feature_runs_use_case",
    "get_create_scan_use_case",
    "get_create_scan_use_case_without_freshness_gate",
    "get_explain_stock_use_case",
    "get_export_scan_results_use_case",
    "get_get_filter_options_use_case",
    "get_get_peers_use_case",
    "get_get_scan_results_use_case",
    "get_get_scan_symbols_use_case",
    "get_get_setup_details_use_case",
    "get_get_single_result_use_case",
    "get_list_feature_runs_use_case",
    "get_run_bulk_scan_use_case",
]
