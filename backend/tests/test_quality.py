"""ETF 质量分：分位映射、缺失归权、覆盖率保护。"""
from __future__ import annotations

from datetime import date, timedelta

from app.services.quality_service import compute_quality, premium_median_abs, rank_scores


def _item(code: str, amount: float | None, mktcap: float | None, prem: float | None, bars: int = 250, category: str = "broad") -> dict:
    return {
        "code": code,
        "category": category,
        "amount_20d": amount,
        "amount_basis": "20d_est" if amount else "1d",
        "mktcap": mktcap,
        "premium_median_abs": prem,
        "bar_count": bars,
    }


def test_rank_scores_direction():
    hi = rank_scores({"a": 100, "b": 10}, higher_is_better=True)
    assert hi["a"] == 100.0 and hi["b"] == 0.0
    lo = rank_scores({"a": 0.001, "b": 0.05}, higher_is_better=False)
    assert lo["a"] == 100.0 and lo["b"] == 0.0


def test_single_sample_gets_full_score_without_fake_spread():
    assert rank_scores({"only": 42.0}) == {"only": 100.0}


def test_missing_premium_renormalizes_weights():
    items = [
        _item("A", 1e9, 5e10, None),
        _item("B", 1e8, 5e9, 0.002),
        _item("C", 1e7, 5e8, 0.001),
    ]
    res = compute_quality(items)
    a = res["A"]
    # A 缺折溢价，其余三项仍应给出分数，且置信度 < 1
    assert a["quality_score"] is not None
    assert a["quality_breakdown"]["premium_stability"]["status"] == "unavailable"
    assert a["quality_confidence"] < 1.0
    assert len([v for v in a["quality_breakdown"].values() if v["status"] == "available"]) == 3


def test_higher_liquidity_and_size_rank_higher():
    items = [
        _item("BIG", 5e9, 1e11, 0.001),
        _item("MID", 5e8, 1e10, 0.001),
        _item("SML", 5e7, 1e9, 0.001),
    ]
    res = compute_quality(items)
    assert res["BIG"]["quality_score"] > res["MID"]["quality_score"] > res["SML"]["quality_score"]


def test_unstable_premium_is_penalized():
    items = [
        _item("STABLE", 1e9, 5e10, 0.001),
        _item("WOBBLY", 1e9, 5e10, 0.045),
    ]
    res = compute_quality(items)
    assert res["STABLE"]["quality_score"] > res["WOBBLY"]["quality_score"]


def test_money_etf_excluded_from_premium_metric():
    items = [
        _item("MONEY", 5e9, 1e11, None, category="money"),
        _item("EQ", 5e9, 1e11, 0.001),
    ]
    res = compute_quality(items)
    assert res["MONEY"]["quality_breakdown"]["premium_stability"]["status"] == "unavailable"
    # 覆盖率 1/2 = 0.5 未低于阈值，指标本身仍启用
    assert res["MONEY"]["quality_breakdown"]["premium_stability"]["coverage"] == 0.5


def test_low_coverage_metric_is_disabled():
    items = [_item(f"C{i}", 1e9, 5e10, None) for i in range(10)]
    items[0]["premium_median_abs"] = 0.001  # 覆盖率 10% < 50%
    res = compute_quality(items)
    status = res["C0"]["quality_breakdown"]["premium_stability"]["status"]
    assert status == "disabled"


def test_all_missing_returns_none_not_zero():
    items = [_item("X", None, None, None, bars=0)]
    res = compute_quality(items)
    assert res["X"]["quality_score"] is None
    assert res["X"]["quality_confidence"] == 0.0


def test_empty_input():
    assert compute_quality([]) == {}


def test_premium_median_requires_same_day_match():
    base = date(2026, 9, 1)
    bars = [(base + timedelta(days=i), 1.0) for i in range(30)]
    # 净值日期整体错开一年 → 同日匹配为 0，样本不足必须返回 None，而不是用 0 填充
    navs = {(base + timedelta(days=365 + i)): 1.0 for i in range(30)}
    value, samples = premium_median_abs(bars, navs)
    assert value is None and samples == 0

    navs_ok = {(base + timedelta(days=i)): 1.0 for i in range(30)}
    value_ok, samples_ok = premium_median_abs(bars, navs_ok)
    assert samples_ok == 30
    assert value_ok == 0.0
