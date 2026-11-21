"""ETF 质量分 v0。

只回答「这个产品好不好用」：流动性、规模、折溢价是否稳定、历史是否够长。
**不回答「这个板块值不值得买」**——后者属于机会/策略层（1c），两者分数独立，
禁止合成一个「推荐分」，否则板块热度和产品缺陷会互相掩盖。

设计约束：
- 每个指标在横截面上做分位映射（0–100），不用绝对值，避免市场整体水位变化导致分数漂移。
- 缺失指标不填默认值：剔除该项后按剩余权重归一，同时降低置信度。
- 某指标覆盖率低于阈值时，整项权重置 0，并在 breakdown 里标注 disabled。
"""
from __future__ import annotations

import math
from statistics import median
from typing import Any, Iterable, Optional

DEFAULT_WEIGHTS: dict[str, float] = {
    "liquidity": 0.40,          # 成交额（越大越好）
    "size": 0.30,               # 规模（越大越好）
    "premium_stability": 0.20,  # 折溢价绝对值中位数（越小越好）
    "history_depth": 0.10,      # 日线根数（越大越好）
}

LABELS: dict[str, str] = {
    "liquidity": "流动性",
    "size": "规模",
    "premium_stability": "折溢价稳定性",
    "history_depth": "历史长度",
}

MIN_PREMIUM_SAMPLES = 20


def rank_scores(values: dict[str, float], higher_is_better: bool = True) -> dict[str, float]:
    """横截面分位映射到 0–100。单个样本直接给 100（无法比较，不伪造差异）。"""
    if not values:
        return {}
    ordered = sorted(values.items(), key=lambda kv: kv[1], reverse=higher_is_better)
    n = len(ordered)
    if n == 1:
        return {ordered[0][0]: 100.0}
    # 降序时 idx=0 是最优 → 得 100；升序（越小越好）时 idx=0 也是最优 → 同样得 100
    return {code: (n - 1 - idx) / (n - 1) * 100 for idx, (code, _) in enumerate(ordered)}


def premium_median_abs(
    bars: Iterable[tuple[Any, Optional[float]]],
    navs: dict[Any, float],
    window: int = 60,
) -> tuple[Optional[float], int]:
    """近 window 个交易日的 |价格/净值 − 1| 中位数。

    只用**同日**匹配：净值是当日盘后计算，与当日收盘价可比。跨日匹配会把
    价格波动误算成折溢价，宁可样本少也不放宽。
    """
    samples: list[float] = []
    ordered = [(d, c) for d, c in bars if c]
    for trade_date, close in ordered[-window:]:
        nav = navs.get(trade_date)
        if not nav or nav <= 0 or not close or close <= 0:
            continue
        samples.append(abs(close / nav - 1))
    if len(samples) < MIN_PREMIUM_SAMPLES:
        return None, len(samples)
    return median(samples), len(samples)


def compute_quality(
    items: list[dict],
    weights: Optional[dict[str, float]] = None,
    coverage_floor: float = 0.5,
) -> dict[str, dict]:
    """计算质量分。

    items 每项字段：code, category, amount_20d, amount_basis, mktcap,
    premium_median_abs, bar_count。
    """
    weights = dict(weights or DEFAULT_WEIGHTS)
    total = len(items)
    if total == 0:
        return {}

    raw: dict[str, dict[str, float]] = {k: {} for k in weights}
    for item in items:
        code = item["code"]
        amount = item.get("amount_20d")
        if amount and amount > 0:
            raw["liquidity"][code] = math.log10(amount)
        mktcap = item.get("mktcap")
        if mktcap and mktcap > 0:
            raw["size"][code] = math.log10(mktcap)
        # 货币 ETF 的净值是每万份收益口径，不参与折溢价
        if item.get("category") != "money":
            prem = item.get("premium_median_abs")
            if prem is not None:
                raw["premium_stability"][code] = prem
        depth = item.get("bar_count") or 0
        if depth >= 20:
            raw["history_depth"][code] = float(depth)

    # 覆盖率保护：覆盖率过低说明数据没准备好，不能靠少数样本给全池排序
    coverage = {k: (len(v) / total if total else 0.0) for k, v in raw.items()}
    disabled = {k for k, cov in coverage.items() if cov < coverage_floor and k != "liquidity"}
    for key in disabled:
        raw[key] = {}

    scored: dict[str, dict[str, float]] = {}
    scored["liquidity"] = rank_scores(raw["liquidity"], higher_is_better=True)
    scored["size"] = rank_scores(raw["size"], higher_is_better=True)
    scored["premium_stability"] = rank_scores(raw["premium_stability"], higher_is_better=False)
    scored["history_depth"] = rank_scores(raw["history_depth"], higher_is_better=True)

    results: dict[str, dict] = {}
    for item in items:
        code = item["code"]
        breakdown: dict[str, dict] = {}
        available_weight = 0.0
        weighted = 0.0
        for key, base_weight in weights.items():
            if key in disabled or base_weight <= 0:
                breakdown[key] = {
                    "label": LABELS.get(key, key),
                    "status": "disabled" if key in disabled else "unavailable",
                    "value": None,
                    "score": None,
                    "weight": 0.0,
                    "coverage": round(coverage.get(key, 0.0), 3),
                }
                continue
            score = scored[key].get(code)
            if score is None:
                breakdown[key] = {
                    "label": LABELS.get(key, key),
                    "status": "unavailable",
                    "value": None,
                    "score": None,
                    "weight": 0.0,
                    "coverage": round(coverage.get(key, 0.0), 3),
                }
                continue
            available_weight += base_weight
            weighted += base_weight * score
            breakdown[key] = {
                "label": LABELS.get(key, key),
                "status": "available",
                "value": _value_of(key, item),
                "score": round(score, 2),
                "weight": base_weight,
                "coverage": round(coverage.get(key, 0.0), 3),
            }

        if available_weight <= 0:
            results[code] = {
                "quality_score": None,
                "quality_confidence": 0.0,
                "quality_breakdown": breakdown,
            }
            continue

        # 置信度 = 可用权重占比（不是样本量），用于页面标注「分数基于几项指标」
        confidence = available_weight / sum(weights.values()) if sum(weights.values()) else 0.0
        results[code] = {
            "quality_score": round(weighted / available_weight, 2),
            "quality_confidence": round(confidence, 3),
            "quality_breakdown": breakdown,
        }
    return results


def _value_of(key: str, item: dict) -> Optional[float]:
    if key == "liquidity":
        return round(item.get("amount_20d") or 0, 2) or None
    if key == "size":
        return round(item.get("mktcap") or 0, 2) or None
    if key == "premium_stability":
        value = item.get("premium_median_abs")
        return round(value * 100, 3) if value is not None else None
    if key == "history_depth":
        return item.get("bar_count")
    return None
