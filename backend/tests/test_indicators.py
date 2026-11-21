"""指标计算与约束投影的纯函数测试。"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services.allocation_service import bounded_simplex_projection
from app.services.market_data import (
    annualized_vol,
    clamp,
    max_drawdown,
    moving_average,
    series_quality,
    staleness_factor,
    total_return,
)


def _series(values):
    return pd.Series(values, index=pd.date_range("2026-01-01", periods=len(values), freq="B"))


def test_clamp_bounds():
    assert clamp(-5) == 0
    assert clamp(120) == 100
    assert clamp(42.5) == 42.5


def test_total_return_and_ma():
    series = _series([100, 101, 102, 103, 104, 105])
    assert round(total_return(series, 5), 4) == 0.05
    assert moving_average(series, 3) == 104
    assert total_return(_series([100, 101]), 5) is None


def test_volatility_and_drawdown():
    flat = _series([100] * 30)
    assert annualized_vol(flat, 20) == 0
    drawdown_series = _series([100, 120, 90, 95, 99, 101, 98, 96, 97, 99, 100, 102])
    assert round(max_drawdown(drawdown_series), 4) == -0.25
    assert annualized_vol(_series([100] * 5), 20) is None


def test_series_quality_decays_with_staleness():
    recent = pd.Series([1.0] * 120, index=pd.date_range(end=pd.Timestamp.today(), periods=120, freq="B"))
    old = pd.Series([1.0] * 120, index=pd.date_range(end=pd.Timestamp.today() - pd.Timedelta(days=60), periods=120, freq="B"))
    assert series_quality(recent, 120) == 1.0
    assert series_quality(old, 120) < 0.5
    assert series_quality(pd.Series(dtype="float64")) == 0.0


def test_staleness_factor_annual_data():
    from datetime import date

    fresh = staleness_factor(date.today(), half_life_days=180)
    one_year = staleness_factor(date(date.today().year - 1, 12, 31), half_life_days=180)
    assert 0.9 < fresh <= 1.0
    assert one_year < 0.5


def test_projection_sums_to_one_and_respects_bounds():
    desired = np.array([0.5, 0.3, 0.2, 0.0])
    lower = np.array([0.05, 0.05, 0.0, 0.0])
    upper = np.array([0.4, 0.5, 0.3, 0.1])
    weights, residual = bounded_simplex_projection(desired, lower, upper, 1.0)
    assert weights.sum() == pytest.approx(1.0, abs=1e-9)
    assert residual < 1e-9
    assert np.all(weights >= lower - 1e-9)
    assert np.all(weights <= upper + 1e-9)


def test_projection_handles_infeasible_bounds():
    weights, residual = bounded_simplex_projection(
        np.array([0.5, 0.5]), np.array([0.8, 0.8]), np.array([1.0, 1.0]), 1.0
    )
    assert weights.sum() == pytest.approx(1.0, abs=1e-9)
    assert residual == 1.0


def test_projection_does_not_break_tight_upper():
    desired = np.array([0.9, 0.05, 0.05])
    lower = np.array([0.0, 0.0, 0.0])
    upper = np.array([0.4, 0.4, 0.4])
    weights, _ = bounded_simplex_projection(desired, lower, upper, 1.0)
    assert weights.sum() == pytest.approx(1.0, abs=1e-9)
    assert np.all(weights <= upper + 1e-12)
