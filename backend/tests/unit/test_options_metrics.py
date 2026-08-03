import pytest

from app.services.options_metrics import (
    aggregate_by_strike,
    compute_key_gamma_levels,
    compute_ivr,
)


def test_compute_key_gamma_levels_uses_call_put_walls_and_zero_gamma_crossing():
    strike_agg = {
        40.0: {"call_gex": 100.0, "put_gex": -80.0, "total_gex": -80.0},
        45.0: {"call_gex": 50.0, "put_gex": -100.0, "total_gex": 100.0},
        50.0: {"call_gex": 10.0, "put_gex": -20.0, "total_gex": 20.0},
    }

    levels = compute_key_gamma_levels(strike_agg)

    assert levels["call_wall"] == 40.0
    assert levels["put_wall"] == 45.0
    assert levels["zero_gamma"] == pytest.approx(44.0)


def test_compute_ivr_returns_placeholder_when_historical_data_missing():
    assert compute_ivr(0.35, None, None) == pytest.approx(50.0)
    assert compute_ivr(0.35, 0.20, None) == pytest.approx(50.0)


def test_aggregate_by_strike_assigns_negative_put_gex():
    options_chain = [
        {"strike": 100.0, "type": "call", "gamma": 0.05, "open_interest": 10, "delta": 0.4, "iv": 0.3},
        {"strike": 100.0, "type": "put", "gamma": 0.05, "open_interest": 5, "delta": -0.4, "iv": 0.32},
    ]

    strike_agg = aggregate_by_strike(options_chain)
    assert strike_agg[100.0]["call_gex"] == pytest.approx(0.05 * 10 * 100)
    assert strike_agg[100.0]["put_gex"] == pytest.approx(-(0.05 * 5 * 100))
    assert strike_agg[100.0]["total_gex"] == pytest.approx((0.05 * 10 * 100) - (0.05 * 5 * 100))


def test_compute_key_gamma_levels_returns_none_zero_gamma_when_cumulative_starts_at_zero():
    strike_agg = {
        65.0: {"call_gex": 100.0, "put_gex": 0.0, "total_gex": 0.0},
        70.0: {"call_gex": 50.0, "put_gex": -10.0, "total_gex": 100.0},
    }

    levels = compute_key_gamma_levels(strike_agg)

    assert levels["call_wall"] == 65.0
    assert levels["put_wall"] is None
    assert levels["zero_gamma"] is None
