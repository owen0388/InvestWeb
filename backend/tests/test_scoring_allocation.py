"""评分与配置引擎测试（使用初始化后的真实数据库，无行情时应拒绝输出评分）。"""
from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import select

from app.models.entities import Instrument, Portfolio, RiskConstraint
from app.services.allocation_service import compute_allocation
from app.services.regime_service import compute_regime
from app.services.scoring_service import compute_all_signals, score_instrument


def test_regime_reports_data_deficiency(db):
    regime = compute_regime(db)
    assert regime["state"] in ("数据不足", "中性或混合")
    if regime["state"] == "数据不足":
        assert regime["score"] is None
        assert "不输出风险偏好判断" in regime["explanation"]
        assert regime["coverage"] < regime["min_coverage"]


def test_signals_are_not_fabricated_without_prices(db):
    result = compute_all_signals(db)
    assert result["computed"] == 0
    assert result["skipped"] > 0
    assert result["insufficient"]
    inst = db.scalar(select(Instrument).where(Instrument.symbol == "sh000300"))
    scored = score_instrument(db, inst, "equity_cn", {}, {"state": "数据不足", "data_deficient": True})
    assert scored["total_score"] is None
    assert scored["action"] == "暂不判断"
    assert scored["data_quality"] == 0


def test_allocation_sums_to_one_and_respects_limits(db):
    portfolio = db.scalar(select(Portfolio).order_by(Portfolio.id))
    result = compute_allocation(db, portfolio, persist=False)
    targets = result["targets"]
    total = sum(t["target"] for t in targets)
    assert total == pytest.approx(1.0, abs=1e-6)
    for target in targets:
        assert target["target"] >= target["min"] - 1e-9
        assert target["target"] <= target["max"] + 1e-9
        assert target["target"] >= -1e-12
    derivative = next((t for t in targets if t["asset_class_code"] == "DERIVATIVE"), None)
    if derivative:
        assert derivative["target"] == 0
    assert result["portfolio"]["is_personalized"] is False
    assert "示例" in result["disclaimer"] or "不构成个性化建议" in result["disclaimer"]


def test_allocation_respects_class_constraints_from_db(db):
    portfolio = db.scalar(select(Portfolio).order_by(Portfolio.id))
    constraints = {
        c.asset_class_code: c
        for c in db.scalars(select(RiskConstraint).where(RiskConstraint.portfolio_id == portfolio.id)).all()
    }
    result = compute_allocation(db, portfolio, persist=False)
    for target in result["targets"]:
        constraint = constraints.get(target["asset_class_code"])
        if not constraint:
            continue
        assert target["target"] >= constraint.min_weight - 1e-9
        assert target["target"] <= constraint.max_weight + 1e-9


def test_turnover_is_reported_and_within_limit_when_feasible(db):
    portfolio = db.scalar(select(Portfolio).order_by(Portfolio.id))
    result = compute_allocation(db, portfolio, persist=False)
    assert 0 <= result["turnover"] <= 1
    assert result["max_turnover"] > 0
    if not result["turnover_relaxed"]:
        assert result["turnover"] <= result["max_turnover"] + 1e-6
