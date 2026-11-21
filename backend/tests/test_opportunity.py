"""机会雷达 v0：趋势特征、状态判定、状态转移合法性。"""
from __future__ import annotations

from app.services.opportunity_service import (
    ALLOWED_TRANSITIONS,
    apply_transition,
    decide_state,
    trend_features,
)


def _steady_up(n: int = 260, start: float = 100.0, step: float = 0.2) -> list[float]:
    return [start + i * step for i in range(n)]


def test_insufficient_bars_returns_none_not_default():
    assert trend_features([100.0] * 59) is None
    state, reasons = decide_state(None)
    assert state == "SCAN"
    assert "不足" in reasons[0]


def test_steady_uptrend_is_candidate():
    feats = trend_features(_steady_up())
    assert feats is not None
    assert feats["ma_order"] == "bull"
    state, reasons = decide_state(feats)
    assert state == "CANDIDATE", reasons


def test_chasing_is_downgraded_to_wait():
    # 前 240 天横盘，最后 20 天暴涨 30% → 趋势虽好但不追
    prices = [100.0] * 240 + [100.0 * (1.013 ** i) for i in range(1, 21)]
    feats = trend_features(prices)
    assert feats is not None and feats["ret20"] > 0.15
    state, reasons = decide_state(feats)
    assert state == "WAIT"
    assert "涨幅过大" in reasons[-1]


def test_break_below_ma60_exits_when_holding():
    prices = _steady_up(240) + [150.0 - i * 2 for i in range(1, 21)]
    feats = trend_features(prices)
    assert feats is not None
    assert feats["price"] < feats["ma60"]
    assert decide_state(feats, holding=True)[0] == "EXIT"
    assert decide_state(feats, holding=False)[0] == "SCAN"


def test_deep_drawdown_forces_exit():
    prices = [200.0] * 200 + [200.0 - i * 2 for i in range(1, 61)]
    feats = trend_features(prices)
    assert feats is not None and feats["drawdown_250"] <= -0.25
    assert decide_state(feats, holding=True)[0] == "EXIT"


def test_high_volatility_is_downgraded():
    # 交替 80/130，年化波动远高于 55%
    prices = [80.0, 130.0] * 130
    feats = trend_features(prices)
    assert feats is not None and feats["vol20"] is not None
    state, reasons = decide_state(feats)
    assert state in {"WATCH", "WAIT"}, reasons
    assert any("波动" in r or "涨幅" in r for r in reasons)


def test_holding_uptrend_is_hold():
    feats = trend_features(_steady_up())
    assert decide_state(feats, holding=True)[0] == "HOLD"


def test_transition_rejects_jump():
    # SCAN 不能直接跳 HOLD
    state, ok = apply_transition("SCAN", "HOLD")
    assert (state, ok) == ("SCAN", False)
    state, ok = apply_transition("CANDIDATE", "HOLD")
    assert (state, ok) == ("HOLD", True)
    # 首次出现直接接受
    assert apply_transition(None, "WATCH") == ("WATCH", True)


def test_all_states_have_transition_rules():
    states = {"SCAN", "WATCH", "WAIT", "CANDIDATE", "HOLD", "TRIM", "EXIT"}
    assert set(ALLOWED_TRANSITIONS) == states
    for state, targets in ALLOWED_TRANSITIONS.items():
        assert targets <= states, f"{state} 指向未知状态"
        assert state in targets, f"{state} 必须允许自循环"
