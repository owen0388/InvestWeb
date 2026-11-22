"""统一机会分出口（P0 过渡期）。

## 为什么需要

系统里原本有两套机会评分，互不相干：

| 页面 | 来源 | 口径 | 覆盖 |
| --- | --- | --- | --- |
| `/radar` | 实时计算 | 趋势强度 60% + 同类相对强弱 40% | ETF 候选池 |
| `/opportunities` | `asset_signals` 表 | 6 特征加权 − 风险惩罚 | 61 个精管标的 |

同一只 ETF 在两处结论可能完全相反。实测 sh515790（光伏ETF）：
`asset_signals` 给 20.1 分「降低风险」，而雷达因趋势达标给 60+ 分「积极研究」。

## 过渡期的取舍

**核心机会分采用雷达口径**，理由：
1. 雷达覆盖 ETF 候选池，是机会发现的主战场
2. 公式简单可解释（趋势 + RS），且已与质量分正确分离
3. `asset_signals` 的 valuation(15%) + fundamental(10%) 权重是空转的
   （代码里只有 cash 模块实现了估值，fundamental 根本没有实现），
   再加上世界银行年度宏观数据严重滞后，不适合做"当期机会"判断

**不做的事**：不强行把两套公式合成一个（任务明确允许此选择）。
`asset_signals` 的分保留，但标记 `legacy_v1`，仅供非 ETF 标的使用，
且不再以「机会分」的名义对外。

## 动作词

统一为**研究优先级**，不是交易指令：
高优先级研究 / 重点关注 / 观察 / 暂不关注 / 数据不足
"""
from __future__ import annotations

import time
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import now_cn
from app.models.entities import AssetSignal

# 过渡期核心机会分版本号。P1 上线新模型时另起版本，不与本版混用。
SCORE_VERSION = "radar_trend_v1"
LEGACY_VERSION = "legacy_v1"

# 研究优先级（不是买入/卖出指令）
LEVEL_HIGH = "高优先级研究"
LEVEL_FOCUS = "重点关注"
LEVEL_WATCH = "观察"
LEVEL_SKIP = "暂不关注"
LEVEL_NA = "数据不足"

# 阈值：沿用雷达既有的 65 / 50 / 35，不新造参数
LEVEL_THRESHOLDS = ((65, LEVEL_HIGH), (50, LEVEL_FOCUS), (35, LEVEL_WATCH))

# 全池评分缓存：算一次 249 只约 0.6 秒，不缓存会让 /opportunities 每次请求都重算
_CACHE: dict = {"date": None, "ts": 0.0, "items": {}}
CACHE_TTL = 300.0


def level_for(score: Optional[float], state: Optional[str] = None) -> str:
    """分数 → 研究优先级。缺失数据不冒充「暂不关注」，而是明确「数据不足」。"""
    if score is None:
        if state in ("TREND_BAD", "SCAN", "WAIT", "TRIM", "EXIT"):
            return LEVEL_SKIP
        return LEVEL_NA
    for threshold, label in LEVEL_THRESHOLDS:
        if score >= threshold:
            return label
    return LEVEL_SKIP


def _try_radar(db: Session) -> dict[str, dict]:
    """取候选池全池评分（带进程内缓存）。失败时返回空 dict，不抛异常。"""
    today = now_cn().date()
    now = time.time()
    if _CACHE["date"] == today and (now - _CACHE["ts"]) < CACHE_TTL:
        return _CACHE["items"]

    try:
        from app.services.opportunity_radar_service import _evaluate_universe

        result = _evaluate_universe(db)
        items = {it["symbol"]: it for it in result["evaluated"]}
        _CACHE.update({"date": today, "ts": now, "items": items})
        return items
    except Exception:  # noqa: BLE001
        return {}


def invalidate_cache() -> None:
    """数据刷新后主动失效（ETf 质量分重算、行情同步等场景调用）。"""
    _CACHE.update({"date": None, "ts": 0.0, "items": {}})


def get_unified_score(db: Session, symbol: str) -> dict:
    """任一标的的统一机会分。

    优先级：
    1. 在 ETF 候选池中 → 雷达口径（核心机会分）
    2. 不在候选池 → 回退 asset_signals，标记 legacy_v1，等级仍按分数换算
    3. 都没有 → score=None，level=数据不足
    """
    radar_items = _try_radar(db)
    item = radar_items.get(symbol)
    if item is not None:
        score = item.get("score")
        state = item.get("state")
        return {
            "symbol": symbol,
            "score": score,  # 不做二次舍入，避免与雷达侧精度不一致（曾差 0.03）
            "score_version": SCORE_VERSION,
            "level": level_for(score, state),
            "state": state,
            "rs_score": item.get("rs_score"),
            "quality_score": item.get("quality_score"),
            "confidence": item.get("quality_confidence"),
            "source": "radar",
            "as_of": now_cn().date().isoformat(),
            "note": (
                "趋势与相对强弱合成；非 CANDIDATE 状态不给分（不假装有机会）。"
                if score is None
                else "趋势强度 60% + 同类相对强弱 40%"
            ),
        }

    # 回退：非 ETF 标的（股票/指数/期货等）走旧的 asset_signals
    latest = db.scalar(select(AssetSignal.as_of).order_by(AssetSignal.as_of.desc()))
    row = None
    if latest:
        row = db.scalar(
            select(AssetSignal).where(
                AssetSignal.symbol == symbol, AssetSignal.as_of == latest
            )
        )
    if row is None:
        return {
            "symbol": symbol,
            "score": None,
            "score_version": SCORE_VERSION,
            "level": LEVEL_NA,
            "state": None,
            "rs_score": None,
            "quality_score": None,
            "confidence": None,
            "source": "none",
            "as_of": now_cn().date().isoformat(),
            "note": "无可用评分数据",
        }

    return {
        "symbol": symbol,
        "score": row.total_score,
        "score_version": LEGACY_VERSION,
        "level": level_for(row.total_score),
        "state": None,
        "rs_score": None,
        "quality_score": None,
        "confidence": row.confidence,
        "source": "asset_signals",
        "as_of": row.as_of.isoformat() if row.as_of else None,
        "note": "非 ETF 标的沿用旧口径（legacy_v1），不作为正式机会分",
    }
