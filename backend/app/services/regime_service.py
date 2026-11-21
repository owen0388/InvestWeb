"""市场风险状态（风险偏好）判定。

输出必须可追溯到具体指标、数据时间与事件；数据覆盖不足时明确返回
“数据不足”，不允许用缺失数据推断风险偏好。
"""
from __future__ import annotations

from datetime import timedelta

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import now_cn
from app.models.entities import AssetSignal, Event, Instrument, MarketObservation, SystemSetting
from app.services import config_service
from app.services.market_data import (
    annualized_vol,
    clamp,
    load_close_series,
    moving_average,
    total_return,
)

CN_EQUITY_INDICES = ["sh000300", "sh000001", "sh000905", "sh000852", "sz399006", "sh000688"]
HAVEN_SYMBOLS = ["sh518880", "sh511010"]


def _bar_date(series: pd.Series):
    return series.index[-1].isoformat() if len(series) else None


def compute_regime(db: Session) -> dict:
    cfg = config_service.signal_rules().get("regime", {})
    comp_cfg = cfg.get("components", {})
    thresholds = cfg.get("thresholds", {})
    min_coverage = float(cfg.get("min_coverage", 0.45))

    components: dict[str, dict] = {}

    hs300 = load_close_series(db, "sh000300", 300)
    # 1) 权益趋势
    if len(hs300) >= 60:
        ma60 = moving_average(hs300, 60)
        dev = hs300.iloc[-1] / ma60 - 1 if ma60 else None
        components["equity_trend"] = {
            "value": clamp(50 + dev * 400) if dev is not None else None,
            "raw": round(dev * 100, 2) if dev is not None else None,
            "text": f"沪深300 相对 60 日均线 {dev*100:+.1f}%" if dev is not None else "无数据",
            "as_of": _bar_date(hs300),
        }
    # 2) 权益动量
    ret20 = total_return(hs300, 20)
    components["equity_momentum"] = {
        "value": clamp(50 + ret20 * 320) if ret20 is not None else None,
        "raw": round(ret20 * 100, 2) if ret20 is not None else None,
        "text": f"沪深300 近 20 日 {ret20*100:+.1f}%" if ret20 is not None else "无数据",
        "as_of": _bar_date(hs300),
    }
    # 3) 波动率（越低越有利于风险偏好）
    vol = annualized_vol(hs300, 20)
    components["volatility"] = {
        "value": clamp(100 - max(0.0, (vol - 0.08)) * 250) if vol is not None else None,
        "raw": round(vol * 100, 2) if vol is not None else None,
        "text": f"沪深300 20 日年化波动 {vol*100:.1f}%" if vol is not None else "无数据",
        "as_of": _bar_date(hs300),
    }
    # 4) 市场广度
    above = total = 0
    breadth_dates = []
    for symbol in CN_EQUITY_INDICES:
        series = load_close_series(db, symbol, 120)
        if len(series) < 60:
            continue
        ma = moving_average(series, 60)
        if ma is None:
            continue
        total += 1
        if series.iloc[-1] > ma:
            above += 1
        breadth_dates.append(_bar_date(series))
    components["breadth"] = {
        "value": round(above / total * 100, 1) if total >= 3 else None,
        "raw": f"{above}/{total}" if total else None,
        "text": f"{above}/{total} 个主要指数位于 60 日均线上方" if total else "无足够指数数据",
        "as_of": max([d for d in breadth_dates if d], default=None),
    }
    # 5) 避险资产（黄金/国债走强 → 风险偏好下降）
    haven_rets = []
    for symbol in HAVEN_SYMBOLS:
        series = load_close_series(db, symbol, 120)
        ret = total_return(series, 20)
        if ret is not None:
            haven_rets.append(ret)
    components["haven"] = {
        "value": clamp(50 - (sum(haven_rets) / len(haven_rets)) * 300) if haven_rets else None,
        "raw": round(sum(haven_rets) / len(haven_rets) * 100, 2) if haven_rets else None,
        "text": f"黄金/国债近 20 日平均 {sum(haven_rets)/len(haven_rets)*100:+.1f}%" if haven_rets else "无数据",
        "as_of": _bar_date(load_close_series(db, HAVEN_SYMBOLS[0], 30)),
    }
    # 6) 事件风险（近 72 小时高重要性事件）
    since = now_cn() - timedelta(hours=72)
    events = db.scalars(select(Event).where(Event.published_at >= since)).all()
    risk_load = 0.0
    max_importance = 0.0
    for event in events:
        weight = 1.0 if event.direction in ("bearish", "mixed") else 0.45
        risk_load += (event.importance / 100.0) * (event.confidence or 0.3) * weight * 12
        max_importance = max(max_importance, event.importance or 0)
    components["event_risk"] = {
        "value": clamp(100 - risk_load) if events else 60.0,
        "raw": round(risk_load, 2),
        "text": f"近 72 小时 {len(events)} 条事件，最高重要性 {max_importance:.0f}" if events else "近期无重大事件",
        "as_of": now_cn().isoformat(sep=" ", timespec="minutes"),
    }

    total_weight = 0.0
    weighted = 0.0
    drivers = []
    for key, meta in components.items():
        weight = float(comp_cfg.get(key, {}).get("weight", 0.0))
        value = meta.get("value")
        if value is None or weight <= 0:
            continue
        total_weight += weight
        weighted += weight * value
        drivers.append(
            {
                "key": key,
                "label": comp_cfg.get(key, {}).get("label", key),
                "value": round(value, 1),
                "weight": weight,
                "text": meta.get("text"),
                "as_of": meta.get("as_of"),
            }
        )
    coverage = round(total_weight, 3)
    if coverage < min_coverage or total_weight == 0:
        score = None
        state = "数据不足"
        explanation = (
            f"可用指标权重覆盖 {coverage:.0%}，低于阈值 {min_coverage:.0%}，"
            "不输出风险偏好判断。请先完成行情与宏观数据采集。"
        )
    else:
        score = round(weighted / total_weight, 1)
        vol_value = components.get("volatility", {}).get("value")
        extreme = (
            max_importance >= float(thresholds.get("extreme_event_importance", 88))
            and (vol_value is not None and vol_value < 35)
        )
        if extreme:
            state = "极端事件风险"
            explanation = "出现极高重要性事件且波动率显著抬升，优先控制风险敞口，不将数据波动解读为买入机会。"
        elif score >= float(thresholds.get("risk_on", 62)):
            state = "风险偏好上升"
            explanation = "权益趋势与动量偏强、波动率可控，风险偏好处于上行区间。"
        elif score <= float(thresholds.get("risk_off", 38)):
            state = "风险偏好下降"
            explanation = "权益趋势偏弱或避险资产走强，风险偏好处于下行区间。"
        else:
            state = "中性或混合"
            explanation = "各维度信号不一致，暂不形成明确的风险偏好方向。"

    result = {
        "state": state,
        "score": score,
        "coverage": coverage,
        "min_coverage": min_coverage,
        "explanation": explanation,
        "drivers": drivers,
        "components": components,
        "data_deficient": score is None,
        "as_of": now_cn().isoformat(sep=" ", timespec="seconds"),
    }
    _persist_regime(db, result)
    return result


def _persist_regime(db: Session, result: dict) -> None:
    key = "last_regime"
    row = db.get(SystemSetting, key)
    if row is None:
        row = SystemSetting(key=key, description="最近一次市场风险状态计算结果")
        db.add(row)
    row.value = result

    hist_key = "regime_history"
    hist = db.get(SystemSetting, hist_key)
    if hist is None:
        hist = SystemSetting(key=hist_key, description="风险状态历史（最多 120 条）", value={"items": []})
        db.add(hist)
    items = (hist.value or {}).get("items", [])
    today = now_cn().date().isoformat()
    items = [i for i in items if i.get("date") != today]
    items.append({"date": today, "score": result["score"], "state": result["state"]})
    hist.value = {"items": items[-120:]}
    db.commit()


def latest_signals(db: Session) -> list[dict]:
    rows = db.execute(
        select(AssetSignal).order_by(AssetSignal.as_of.desc(), AssetSignal.total_score.desc()).limit(200)
    ).scalars().all()
    return [
        {
            "symbol": r.symbol,
            "total_score": r.total_score,
            "confidence": r.confidence,
            "action": r.action,
            "as_of": r.as_of,
        }
        for r in rows
    ]
