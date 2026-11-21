"""机会雷达 v0：趋势特征 + 7 状态机。

只回答「现在该不该关注」，不回答「买多少」（后者属于配置层）。
三条硬约束：

1. **数据不足不出信号**：序列 <60 根直接返回 SCAN，不用 50 分默认值凑数。
2. **不追高**：短期涨幅超过阈值降级为 WAIT（等回调），而不是给高分。
3. **状态不跳跃**：转移表显式定义，测试断言非法转移会被拒绝。

状态含义：
    SCAN       刚进入候选池，历史不足或数据异常
    WATCH      趋势刚转正，尚未确认
    WAIT       趋势正但已追高，等回调
    CANDIDATE  趋势确认 + 未追高 + 波动可接受，可进入研究
    HOLD       已持有且趋势未坏
    TRIM       已持有但趋势转弱
    EXIT       趋势破坏或深度回撤
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

import pandas as pd

from app.services.market_data import moving_average

# 允许的状态转移。不在表里的转移一律视为非法，防止"从 SCAN 直接跳到 HOLD"。
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "SCAN": {"SCAN", "WATCH", "WAIT"},
    "WATCH": {"SCAN", "WATCH", "WAIT", "CANDIDATE"},
    "WAIT": {"SCAN", "WAIT", "WATCH", "CANDIDATE"},
    "CANDIDATE": {"SCAN", "WAIT", "CANDIDATE", "HOLD", "TRIM"},
    "HOLD": {"HOLD", "TRIM", "EXIT"},
    "TRIM": {"HOLD", "TRIM", "EXIT", "CANDIDATE"},
    "EXIT": {"EXIT", "SCAN", "WATCH"},
}

DEFAULTS = {
    "min_bars": 60,
    "chase_ret20": 0.15,     # 20 日涨幅超过 15% 视为追高
    "chase_ret60": 0.35,     # 60 日涨幅超过 35% 视为追高
    "max_vol": 0.55,         # 年化波动超过 55% 降级
    "exit_drawdown": -0.25,  # 距 250 日高点回撤超过 25% 退出
}


def trend_features(closes: Iterable[float], params: Optional[dict] = None) -> Optional[dict]:
    """计算趋势特征。序列不足返回 None——不猜、不补、不用默认值。"""
    cfg = {**DEFAULTS, **(params or {})}
    series = pd.Series(list(closes), dtype="float64").dropna()
    if len(series) < cfg["min_bars"]:
        return None

    price = float(series.iloc[-1])
    ma20 = moving_average(series, 20)
    ma60 = moving_average(series, 60)
    if not ma20 or not ma60 or price <= 0:
        return None

    ret20 = price / float(series.iloc[-21]) - 1 if len(series) >= 21 else None
    ret60 = price / float(series.iloc[-61]) - 1
    daily = series.pct_change().dropna().tail(20)
    vol20 = float(daily.std() * math.sqrt(252)) if len(daily) >= 10 else None
    high250 = float(series.tail(250).max())
    drawdown = price / high250 - 1 if high250 > 0 else None

    return {
        "price": round(price, 4),
        "ma20": round(ma20, 4),
        "ma60": round(ma60, 4),
        "dev20": round(price / ma20 - 1, 4),
        "dev60": round(price / ma60 - 1, 4),
        "ma_order": "bull" if ma20 > ma60 else "bear",
        "ret20": round(ret20, 4) if ret20 is not None else None,
        "ret60": round(ret60, 4),
        "vol20": round(vol20, 4) if vol20 is not None else None,
        "drawdown_250": round(drawdown, 4) if drawdown is not None else None,
        "bars": int(len(series)),
    }


def decide_state(
    features: Optional[dict],
    holding: bool = False,
    params: Optional[dict] = None,
) -> tuple[str, list[str]]:
    """根据特征给出状态与理由。理由必须可读，便于回查。"""
    cfg = {**DEFAULTS, **(params or {})}
    if features is None:
        return "SCAN", ["价格序列不足，暂不判断（数据问题，非趋势判断）"]

    reasons: list[str] = []
    price, ma20, ma60 = features["price"], features["ma20"], features["ma60"]
    ret20, ret60 = features["ret20"], features["ret60"]
    vol, drawdown = features["vol20"], features["drawdown_250"]

    above20 = price > ma20
    above60 = price > ma60
    bull = features["ma_order"] == "bull"

    # 退出优先：深度回撤或跌破长期均线，先保护本金
    if drawdown is not None and drawdown <= cfg["exit_drawdown"]:
        reasons.append(f"距 250 日高点回撤 {drawdown:.1%}，超过 {cfg['exit_drawdown']:.0%}")
        return ("EXIT" if holding else "SCAN"), reasons
    if not above60:
        # 注意：SCAN 在这里表示「趋势不合格、不进入观察」，与「数据不足」共用状态名。
        # 两者必须靠理由区分，否则统计上会把"没数据"和"趋势差"混成一类。
        reasons.append(
            f"价格低于 MA60（偏离 {features['dev60']:.1%}），趋势未修复，不纳入观察"
        )
        return ("EXIT" if holding else "SCAN"), reasons

    # 不追高：涨幅过大只标记等待，不给候选
    chasing = (ret20 is not None and ret20 > cfg["chase_ret20"]) or (
        ret60 is not None and ret60 > cfg["chase_ret60"]
    )
    if chasing:
        detail = []
        if ret20 is not None and ret20 > cfg["chase_ret20"]:
            detail.append(f"20日 +{ret20:.1%}")
        if ret60 > cfg["chase_ret60"]:
            detail.append(f"60日 +{ret60:.1%}")
        reasons.append("短期涨幅过大（" + "、".join(detail) + "），等回调")
        return "WAIT", reasons

    if vol is not None and vol > cfg["max_vol"]:
        reasons.append(f"年化波动 {vol:.0%} 超过 {cfg['max_vol']:.0%}，降级观察")
        return "TRIM" if holding else "WATCH", reasons

    if above20 and bull:
        reasons.append("价格在 MA20 上方，且 MA20 > MA60")
        if holding:
            return "HOLD", reasons
        return "CANDIDATE", reasons

    if above60 and not above20:
        reasons.append("价格跌破 MA20 但仍在 MA60 上方")
        return "TRIM" if holding else "WATCH", reasons

    reasons.append("趋势方向不明确")
    return "WATCH", reasons


def apply_transition(current: Optional[str], target: str) -> tuple[str, bool]:
    """校验状态转移。非法转移返回 (current, False)，不强行跳转。"""
    if current is None:
        return target, True
    if current == target:
        return current, True
    allowed = ALLOWED_TRANSITIONS.get(current, set())
    if target in allowed:
        return target, True
    return current, False


def state_bucket(state: str, features: Optional[dict]) -> str:
    """把「数据问题」与「趋势不合格」分开计数。

    两者都会落在 SCAN，但含义完全不同：前者是缺数据（要修采集），
    后者是趋势没修复（正常现象）。混在一起会得到"165 只待扫描"这种误导性数字。
    """
    if state != "SCAN":
        return state
    return "NO_DATA" if features is None else "TREND_BAD"


def relative_strength_scores(items: list[dict], min_group: int = 5) -> dict[str, float]:
    """同类别的相对强弱分位（0–100）。

    items 每项：code, category, ret20, ret60。
    同类样本 <min_group 时不排名——三只 ETF 的"第一名"没有信息量。
    """
    groups: dict[str, list[dict]] = {}
    for item in items:
        if item.get("ret20") is None or item.get("ret60") is None:
            continue
        groups.setdefault(item["category"], []).append(item)

    out: dict[str, float] = {}
    for group in groups.values():
        if len(group) < min_group:
            continue
        # 20 日与 60 日动量各占一半，避免只看短期反弹
        ranked = sorted(group, key=lambda x: x["ret20"] * 0.5 + x["ret60"] * 0.5)
        n = len(ranked)
        for idx, item in enumerate(ranked):
            out[item["code"]] = (idx / (n - 1)) * 100
    return out


def trend_strength(features: dict) -> float:
    """趋势强度 0–100：均线多头排列 + 偏离 + 中期动量，各占一部分。"""
    score = 0.0
    if features["ma_order"] == "bull":
        score += 35
    # 站上 MA20 的幅度，0~20% 线性映射到 0~35 分
    dev20 = features["dev20"]
    score += max(0.0, min(dev20 / 0.20, 1.0)) * 35
    # 60 日动量，0~30% 映射到 0~30 分
    ret60 = features["ret60"] or 0.0
    score += max(0.0, min(ret60 / 0.30, 1.0)) * 30
    return round(min(score, 100.0), 2)


def opportunity_score(features: dict, rs_score: Optional[float]) -> float:
    """机会分 0–100，只看「标的吸引力」。

    **不含质量分**：产品好不好是门槛（quality < 40 直接不进列表），
    不是加分项——否则"烂产品但板块热"会被分数掩盖。
    相对强弱缺失时按趋势分归一，不补默认值。
    """
    trend = trend_strength(features)
    if rs_score is None:
        return round(trend, 2)
    return round(trend * 0.6 + rs_score * 0.4, 2)
