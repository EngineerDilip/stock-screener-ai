from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.domain.relative_strength import (
    BALANCED_RS_FORMULA_VERSION,
    LEGACY_RS_FORMULA_VERSION,
)
from app.services.bootstrap_publication_date import (
    resolve_bootstrap_publication_date,
)


def test_bootstrap_publication_date_uses_recent_active_balanced_run() -> None:
    repository = SimpleNamespace(
        active_formula=lambda _db, *, market: BALANCED_RS_FORMULA_VERSION,
        get_latest_completed=lambda _db, **_kwargs: SimpleNamespace(
            id=42,
            as_of_date=date(2026, 4, 9),
        ),
    )

    resolution = resolve_bootstrap_publication_date(
        object(),
        market="hk",
        requested_date=date(2026, 4, 10),
        repository=repository,
        max_lag_days=3,
    )

    assert resolution.market == "HK"
    assert resolution.selected_date == date(2026, 4, 9)
    assert resolution.market_rs_run_id == 42
    assert resolution.lag_days == 1
    assert resolution.reason_code == "balanced_run_selected"


def test_bootstrap_publication_date_keeps_legacy_formula_on_requested_date() -> None:
    calls = []
    repository = SimpleNamespace(
        active_formula=lambda _db, *, market: LEGACY_RS_FORMULA_VERSION,
        get_latest_completed=lambda *_args, **_kwargs: calls.append("unexpected"),
    )

    resolution = resolve_bootstrap_publication_date(
        object(),
        market="US",
        requested_date=date(2026, 4, 10),
        repository=repository,
        max_lag_days=3,
    )

    assert resolution.selected_date == date(2026, 4, 10)
    assert resolution.reason_code == "active_formula_not_balanced"
    assert calls == []


def test_bootstrap_publication_date_rejects_stale_balanced_run() -> None:
    repository = SimpleNamespace(
        active_formula=lambda _db, *, market: BALANCED_RS_FORMULA_VERSION,
        get_latest_completed=lambda _db, **_kwargs: SimpleNamespace(
            id=42,
            as_of_date=date(2026, 4, 1),
        ),
    )

    resolution = resolve_bootstrap_publication_date(
        object(),
        market="HK",
        requested_date=date(2026, 4, 10),
        repository=repository,
        max_lag_days=3,
    )

    assert resolution.selected_date == date(2026, 4, 10)
    assert resolution.market_rs_run_id == 42
    assert resolution.lag_days == 9
    assert resolution.reason_code == "balanced_run_lag_exceeds_policy"
