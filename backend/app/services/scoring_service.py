"""资产评分引擎。

设计要点：
- 每类资产用不同的特征与权重（配置驱动），缺失特征不猜值，直接剔除权重并
  降低 data_quality / confidence。
- 评分只在同类资产内部做相对比较，不跨类别解释为预期收益。
- 纯规则计算，可测试；AI 不参与评分数字生成。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.jsonutil import clean_json
from app.core.timeutil import now_cn
from app.models.entities import AssetClass, AssetSignal, Event, EventImpact, Instrument, MarketObservation
from app.services import config_service
from app.services.market_data import (
    annualized_vol,
    clamp,
    latest_macro,
    load_close_series,
    max_drawdown,
    moving_average,
    series_quality,
    staleness_factor,
    total_return,
)

MACRO_MAP = {
    "equity_cn": ("WB_NY.GDP.MKTP.KD.ZG_CN", "gdp_up"),
    "fund": ("WB_NY.GDP.MKTP.KD.ZG_CN", "gdp_up"),
    "overseas": ("WB_NY.GDP.MKTP.KD.ZG_US", "gdp_up"),
    "bond": ("WB_FP.CPI.TOTL.ZG_CN", "inflation_down"),
    "reit": ("WB_FP.CPI.TOTL.ZG_CN", "inflation_down"),
    "commodity": ("WB_NY.GDP.MKTP.KD.ZG_CN", "gdp_up"),
    "gold": ("WB_FP.CPI.TOTL.ZG_CN", "inflation_up"),
    "cash": ("WB_FP.CPI.TOTL.ZG_CN", "inflation_down"),
    "wealth": ("WB_FP.CPI.TOTL.ZG_CN", "inflation_down"),
    "long_term": ("WB_FP.CPI.TOTL.ZG_CN", "inflation_down"),
}


def _macro_feature(db: Session, module: str) -> tuple[Optional[float], float, str, Optional[str]]:
    mapping = MACRO_MAP.get(module)
    if not mapping:
        return None, 0.0, "该类资产未配置宏观映射", None
    code, kind = mapping
    obs = latest_macro(db, code)
    if obs is None or obs.value is None:
        return None, 0.0, f"宏观指标 {code} 缺失", None
    stale = staleness_factor(obs.period_date)
    value = obs.value
    if kind == "gdp_up":
        raw = 50 + (value - 5.0) * 10
        text = f"{obs.name} {value:.2f}%（{obs.period} 年，来源 World Bank）"
    elif kind == "inflation_up":
        raw = 50 + (value - 2.0) * 8
        text = f"{obs.name} {value:.2f}%（{obs.period} 年，来源 World Bank）"
    else:
        raw = 50 - (value - 2.0) * 8
        text = f"{obs.name} {value:.2f}%（{obs.period} 年，来源 World Bank）"
    # 年度数据本身滞后，向中性收缩，避免把陈旧数据当强信号
    adjusted = 50 + (raw - 50) * stale
    return clamp(adjusted), stale, text, obs.fetched_at.isoformat(sep=" ", timespec="minutes") if obs.fetched_at else None


def _catalyst_feature(db: Session, instrument: Instrument, as_of: date) -> tuple[Optional[float], str]:
    since = now_cn() - timedelta(days=30)
    rows = db.execute(
        select(EventImpact, Event)
        .join(Event, Event.id == EventImpact.event_id)
        .where(
            EventImpact.instrument_symbol == instrument.symbol,
            Event.published_at >= since,
        )
    ).all()
    if not rows:
        return 50.0, "近 30 日无与该品种直接相关的事件"
    total = 0.0
    details = []
    for impact, event in rows:
        sign = {"bullish": 1, "bearish": -1}.get(impact.direction, 0)
        contribution = sign * (impact.magnitude or 0) * (impact.confidence or 0) * (event.importance or 0) / 100
        total += contribution
        details.append(f"{event.title[:24]}（{impact.direction}）")
    return clamp(50 + total * 45), "；".join(details[:3])


def _liquidity_feature(db: Session, instrument: Instrument, bars: pd.DataFrame, peers: dict[str, list[float]]) -> tuple[float, str]:
    tier_score = {"high": 72.0, "medium": 52.0, "low": 34.0, "very_low": 16.0}.get(instrument.liquidity_tier, 45.0)
    if bars is not None and not bars.empty and "amount" in bars:
        avg_amount = bars["amount"].tail(20).mean()
        if avg_amount and avg_amount > 0:
            values = sorted(v for v in peers.get(instrument.asset_class_code, []) if v and v > 0)
            if len(values) >= 3:
                rank = sum(1 for v in values if v <= avg_amount) / len(values)
                score = 20 + rank * 70
                return round(score, 1), f"20 日均成交额 {avg_amount/1e8:.2f} 亿元，同类分位 {rank*100:.0f}%"
            return round(tier_score, 1), f"流动性分层 {instrument.liquidity_tier}（同类样本不足）"
    return round(tier_score, 1), f"无成交额数据，按流动性分层 {instrument.liquidity_tier} 估计"


def _valuation_feature(db: Session, instrument: Instrument, module: str, series: pd.Series) -> tuple[Optional[float], str]:
    if module == "cash" and len(series) >= 21:
        ret20 = total_return(series, 20)
        if ret20 is not None:
            annual = (1 + ret20) ** (252 / 20) - 1
            return clamp(50 + (annual - 0.015) * 2000), f"近 20 日折算年化约 {annual*100:.2f}%"
    return None, "估值/收益率数据未接入（不猜测）"


def _action_for(score: Optional[float], confidence: float, data_quality: float) -> str:
    cfg = config_service.signal_rules().get("scoring", {})
    thresholds = cfg.get("action_thresholds", {})
    if score is None or data_quality < float(cfg.get("min_data_quality_for_action", 0.4)):
        return "暂不判断"
    if confidence < float(cfg.get("min_confidence_for_action", 0.35)):
        return "暂不判断"
    if score >= float(thresholds.get("strong_buy_research", 72)):
        return "考虑提高权重"
    if score >= float(thresholds.get("buy_research", 62)):
        return "积极研究"
    if score >= float(thresholds.get("hold", 45)):
        return "维持配置"
    if score >= float(thresholds.get("watch", 35)):
        return "观察等待"
    if score >= float(thresholds.get("reduce", 25)):
        return "考虑降低权重"
    return "降低风险"


def _bars_frame(db: Session, symbol: str, limit: int = 260) -> pd.DataFrame:
    rows = db.execute(
        select(
            MarketObservation.trade_date,
            MarketObservation.close,
            MarketObservation.amount,
        )
        .where(MarketObservation.symbol == symbol, MarketObservation.close.is_not(None))
        .order_by(MarketObservation.trade_date.asc())
    ).all()
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows, columns=["trade_date", "close", "amount"])
    frame = frame.drop_duplicates(subset=["trade_date"], keep="last").set_index("trade_date")
    return frame.tail(limit)


def _peer_amounts(db: Session) -> dict[str, list[float]]:
    since = date.today() - timedelta(days=40)
    rows = db.execute(
        select(Instrument.asset_class_code, MarketObservation.amount)
        .join(Instrument, Instrument.symbol == MarketObservation.symbol)
        .where(MarketObservation.trade_date >= since, MarketObservation.amount.is_not(None))
    ).all()
    grouped: dict[str, list[float]] = {}
    for class_code, amount in rows:
        grouped.setdefault(class_code, []).append(float(amount or 0))
    return grouped


def score_instrument(
    db: Session,
    instrument: Instrument,
    module: str,
    peer_amounts: dict[str, list[float]],
    regime: dict,
) -> dict:
    cfg = config_service.signal_rules().get("scoring", {})
    module_cfg = (cfg.get("modules", {}) or {}).get(module) or (cfg.get("modules", {}) or {}).get("generic", {})
    weights: dict[str, float] = module_cfg.get("weights", {"trend": 0.35, "macro": 0.25, "catalyst": 0.2, "liquidity": 0.2})
    params = module_cfg.get("params", {})

    frame = _bars_frame(db, instrument.symbol)
    series = frame["close"].astype(float) if not frame.empty else pd.Series(dtype="float64")
    quality = series_quality(series, expected_days=120)
    features: dict[str, dict] = {}
    missing: list[str] = []

    # 硬性门槛：没有足够价格序列时不得用流动性分层与“无事件=50”拼出一个评分
    if len(series) < 60:
        return {
            "total_score": None,
            "confidence": 0.0,
            "data_quality": round(quality, 3),
            "coverage": 0.0,
            "action": "暂不判断",
            "features": {},
            "missing_inputs": [f"价格序列仅 {len(series)} 条，少于 60 条最低要求"] + missing,
            "rationale": {
                "summary": f"{instrument.name} 缺少足够的历史价格，未生成评分，避免用不可靠数据给出建议。",
                "features": {},
                "weights": weights,
                "coverage": 0.0,
                "score_change": None,
                "regime": regime.get("state"),
                "regime_score": regime.get("score"),
                "disclaimer": "数据不足时不输出评分。",
            },
            "regime": regime.get("state", "数据不足"),
            "score_change": None,
        }

    # --- 趋势 ---
    if len(series) >= 60:
        ma20 = moving_average(series, 20)
        ma60 = moving_average(series, 60)
        dev20 = series.iloc[-1] / ma20 - 1 if ma20 else None
        dev60 = series.iloc[-1] / ma60 - 1 if ma60 else None
        trend_value = clamp(50 + (dev60 or 0) * 350 + (dev20 or 0) * 150)
        features["trend"] = {
            "value": round(trend_value, 1),
            "quality": quality,
            "text": f"现价相对 MA20 {(dev20 or 0)*100:+.1f}%、MA60 {(dev60 or 0)*100:+.1f}%",
            "as_of": series.index[-1].isoformat(),
        }
    else:
        missing.append("不足 60 个交易日的价格序列")

    # --- 波动与回撤（作为风险调整） ---
    vol20 = annualized_vol(series, 20) if len(series) >= 25 else None
    dd = max_drawdown(series.tail(120)) if len(series) >= 30 else None
    vol_score = clamp(100 - (vol20 or 0.25) * 300) if vol20 is not None else None
    if vol20 is not None:
        features["risk"] = {
            "value": round(clamp((vol_score or 50) + (dd or 0) * 100), 1),
            "quality": quality,
            "text": f"20 日年化波动 {vol20*100:.1f}%，近 120 日最大回撤 {(dd or 0)*100:.1f}%",
            "as_of": series.index[-1].isoformat(),
        }

    # --- 流动性 ---
    liq_value, liq_text = _liquidity_feature(db, instrument, frame, peer_amounts)
    features["liquidity"] = {"value": liq_value, "quality": 0.75, "text": liq_text, "as_of": None}

    # --- 宏观 ---
    macro_value, macro_stale, macro_text, macro_time = _macro_feature(db, module)
    if macro_value is not None:
        features["macro"] = {
            "value": round(macro_value, 1),
            "quality": round(0.45 + 0.55 * macro_stale, 2),
            "text": macro_text,
            "as_of": macro_time,
        }
    else:
        missing.append(macro_text)

    # --- 事件催化 ---
    cat_value, cat_text = _catalyst_feature(db, instrument, date.today())
    features["catalyst"] = {
        "value": round(cat_value, 1),
        "quality": 0.6,
        "text": cat_text,
        "as_of": None,
    }

    # --- 估值 / 收益率 ---
    val_value, val_text = _valuation_feature(db, instrument, module, series)
    if val_value is not None:
        features["valuation"] = {"value": round(val_value, 1), "quality": 0.8, "text": val_text, "as_of": None}
    else:
        missing.append(val_text)

    # --- 基本面（未接入财报数据，明确缺失） ---
    if module in ("equity_cn", "fund", "reit"):
        missing.append("盈利/财务基本面数据未接入")

    total_weight = sum(w for k, w in weights.items() if k in features)
    base = 50.0
    if total_weight <= 0:
        total_score = None
    else:
        base = sum(weights[k] * features[k]["value"] for k in weights if k in features) / total_weight
        # 风险调整：波动与回撤越大，扣分越多
        if "risk" in features:
            penalty = float(params.get("vol_penalty", 0.5)) * max(0.0, 55 - features["risk"]["value"]) * 0.35
            base -= penalty
        total_score = round(clamp(base), 1)

    coverage = round(sum(w for k, w in weights.items() if k in features), 3)
    quality_weighted = sum(weights[k] * features[k]["quality"] for k in weights if k in features)
    data_quality = round((quality_weighted / total_weight) * min(1.0, quality + 0.15), 3) if total_weight else 0.0

    catalyst_strength = abs((features.get("catalyst", {}).get("value") or 50) - 50) / 50
    regime_ok = 0.0 if regime.get("data_deficient") else 0.1
    confidence = round(min(0.85, 0.18 + 0.45 * data_quality + 0.14 * catalyst_strength + regime_ok), 3)

    prev = db.scalar(
        select(AssetSignal)
        .where(AssetSignal.instrument_id == instrument.id, AssetSignal.as_of < date.today())
        .order_by(AssetSignal.as_of.desc())
    )
    score_change = round(total_score - prev.total_score, 1) if (prev and prev.total_score is not None and total_score is not None) else None

    action = _action_for(total_score, confidence, data_quality)
    rationale = {
        "summary": _summary_text(instrument, total_score, action, data_quality),
        "features": features,
        "weights": weights,
        "coverage": coverage,
        "score_change": score_change,
        "regime": regime.get("state"),
        "regime_score": regime.get("score"),
        "disclaimer": "评分用于同类资产内的相对研究排序，不代表预期收益率，也不构成投资建议。",
    }
    return {
        "total_score": total_score,
        "confidence": confidence,
        "data_quality": data_quality,
        "coverage": coverage,
        "action": action,
        "features": features,
        "missing_inputs": missing,
        "rationale": rationale,
        "regime": regime.get("state", "数据不足"),
        "score_change": score_change,
    }


def _summary_text(instrument: Instrument, score: Optional[float], action: str, quality: float) -> str:
    if score is None:
        return f"{instrument.name} 可用数据不足，暂不输出评分，避免用不可靠数据加仓。"
    trend = "偏强" if score >= 60 else ("偏弱" if score <= 40 else "中性")
    return (
        f"{instrument.name} 综合相对吸引力 {score:.1f}（数据质量 {quality*100:.0f}%），"
        f"趋势与催化综合{trend}，建议动作：{action}。"
    )


def compute_all_signals(db: Session, as_of: Optional[date] = None) -> dict:
    from app.services.regime_service import compute_regime

    as_of = as_of or date.today()
    regime = compute_regime(db)
    module_map = {
        c.code: c.analysis_module for c in db.scalars(select(AssetClass)).all()
    }
    instruments = db.scalars(select(Instrument).where(Instrument.enabled.is_(True))).all()
    peers = _peer_amounts(db)

    computed = skipped = 0
    insufficient: list[str] = []
    for inst in instruments:
        if inst.data_status in ("manual", "restricted") or inst.data_source_code == "manual":
            skipped += 1
            continue
        module = inst.analysis_module or module_map.get(inst.asset_class_code, "generic")
        try:
            result = score_instrument(db, inst, module, peers, regime)
        except Exception as exc:  # noqa: BLE001
            insufficient.append(f"{inst.symbol}: 计算异常 {exc}"[:120])
            continue
        if result["total_score"] is None:
            skipped += 1
            insufficient.append(f"{inst.name}：{'；'.join(result['missing_inputs'][:2])}")
            continue
        existing = db.scalar(
            select(AssetSignal).where(
                AssetSignal.instrument_id == inst.id, AssetSignal.as_of == as_of
            )
        )
        if existing is None:
            existing = AssetSignal(instrument_id=inst.id, symbol=inst.symbol, as_of=as_of)
            db.add(existing)
            existing.version = 1
        existing.asset_class_code = inst.asset_class_code
        existing.fundamental = None
        existing.valuation = _feat(result, "valuation")
        existing.trend = _feat(result, "trend")
        existing.macro = _feat(result, "macro")
        existing.liquidity = _feat(result, "liquidity")
        existing.catalyst = _feat(result, "catalyst")
        existing.risk = _feat(result, "risk")
        existing.data_quality = result["data_quality"]
        existing.total_score = result["total_score"]
        existing.confidence = result["confidence"]
        existing.action = result["action"]
        existing.regime = result["regime"]
        existing.components = clean_json({k: {**v} for k, v in result["features"].items()})
        existing.rationale = clean_json(result["rationale"])
        existing.missing_inputs = result["missing_inputs"]
        computed += 1
    db.commit()
    return {
        "as_of": as_of.isoformat(),
        "computed": computed,
        "skipped": skipped,
        "insufficient": insufficient[:30],
        "regime": regime.get("state"),
        "regime_score": regime.get("score"),
    }


def _feat(result: dict, key: str) -> Optional[float]:
    feats = result["features"]
    return feats[key]["value"] if key in feats else None
