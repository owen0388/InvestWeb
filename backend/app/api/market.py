"""市场总览、风险状态、行情与宏观接口。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.timeutil import now_cn
from app.models.entities import (
    AssetClass,
    AssetSignal,
    DataSource,
    Event,
    Instrument,
    MacroObservation,
    MarketObservation,
    SystemSetting,
)
from app.services.collector_service import source_health_snapshot
from app.services.market_data import quote_with_change, staleness_factor

router = APIRouter(prefix="/api/market", tags=["market"])

HEADLINE = [
    "sh000001", "sh000300", "sz399006", "sh000688",
    "sh518880", "hf_CL", "sh511010", "hkHSI", "gb_$ixx",
]
HEADLINE_SAFE = ["sh000001", "sh000300", "sz399006", "sh000688", "sh518880", "hf_CL", "sh511010", "hkHSI", "gb_$dji"]


def _latest_quotes(db: Session, symbols: list[str]) -> list[dict]:
    out = []
    for symbol in symbols:
        inst = db.scalar(select(Instrument).where(Instrument.symbol == symbol))
        obs, change = quote_with_change(db, symbol)
        if not inst:
            continue
        out.append(
            {
                "symbol": symbol,
                "name": inst.name,
                "asset_class_code": inst.asset_class_code,
                "value": obs.close if obs else None,
                "change_pct": change,
                "trade_date": obs.trade_date.isoformat() if obs else None,
                "observed_at": obs.observed_at.isoformat(sep=" ", timespec="seconds") if obs else None,
                "data_type": obs.data_type if obs else "缺失",
                "data_status": inst.data_status,
                "source": obs.source_code if obs else inst.data_source_code,
            }
        )
    return out


def _regime(db: Session) -> dict:
    row = db.get(SystemSetting, "last_regime")
    if row and isinstance(row.value, dict) and row.value.get("state"):
        return row.value
    from app.services.regime_service import compute_regime

    return compute_regime(db)


@router.get("/overview")
def market_overview(db: Session = Depends(get_db)):
    regime = _regime(db)
    headline = _latest_quotes(db, HEADLINE_SAFE)

    instruments = db.scalars(select(Instrument)).all()
    total = len(instruments)
    automatic = [i for i in instruments if i.data_status not in ("manual", "restricted")]
    fresh = [i for i in automatic if i.last_data_at and (now_cn() - i.last_data_at).total_seconds() < 86400 * 3]
    manual = [i for i in instruments if i.data_status == "manual"]
    restricted = [i for i in instruments if i.data_status == "restricted"]
    missing = [i for i in automatic if i.last_data_at is None]

    classes = db.scalars(select(AssetClass).where(AssetClass.level == 1).order_by(AssetClass.sort_order)).all()
    class_snapshot = []
    for cls in classes:
        members = [i for i in instruments if i.asset_class_code.startswith(cls.code)]
        healthy = [i for i in members if i.data_status in ("normal", "delayed")]
        latest = max((i.last_data_at for i in members if i.last_data_at), default=None)
        class_snapshot.append(
            {
                "code": cls.code,
                "name": cls.name,
                "instrument_count": len(members),
                "with_data": len(healthy),
                "manual": len([i for i in members if i.data_status == "manual"]),
                "last_data_at": latest.isoformat(sep=" ", timespec="minutes") if latest else None,
                "liquidity_tier": cls.liquidity_tier,
            }
        )

    since = now_cn() - timedelta(days=3)
    events = db.scalars(
        select(Event).where(Event.published_at >= since).order_by(Event.importance.desc()).limit(5)
    ).all()
    as_of = db.scalar(select(AssetSignal.as_of).order_by(AssetSignal.as_of.desc()))
    signals = db.scalars(select(AssetSignal).where(AssetSignal.as_of == as_of)).all() if as_of else []
    actionable = [s for s in signals if s.action in ("考虑提高权重", "积极研究", "考虑降低权重", "降低风险")]

    sources = source_health_snapshot(db)
    healthy_sources = len([s for s in sources if s["status"] == "healthy"])
    macro_cards = []
    for code, label in [
        ("WB_NY.GDP.MKTP.KD.ZG_CN", "中国 GDP 增速"),
        ("WB_FP.CPI.TOTL.ZG_CN", "中国 CPI"),
        ("WB_NY.GDP.MKTP.KD.ZG_US", "美国 GDP 增速"),
        ("WB_GC.DOD.TOTL.GD.ZS_CN", "中国政府债务/GDP"),
    ]:
        obs = db.scalar(
            select(MacroObservation)
            .where(MacroObservation.indicator_code == code, MacroObservation.value.is_not(None))
            .order_by(MacroObservation.period.desc())
        )
        if obs:
            macro_cards.append(
                {
                    "code": code,
                    "name": label,
                    "value": obs.value,
                    "unit": obs.unit or "%",
                    "period": obs.period,
                    "source": obs.source_code,
                    "fetched_at": obs.fetched_at.isoformat(sep=" ", timespec="minutes"),
                    "staleness": round(staleness_factor(obs.period_date), 2),
                    "is_stale": staleness_factor(obs.period_date) < 0.5,
                }
            )

    return {
        "as_of": now_cn().isoformat(sep=" ", timespec="seconds"),
        "regime": regime,
        "headline": headline,
        "class_snapshot": class_snapshot,
        "macro": macro_cards,
        "top_events": [
            {
                "id": e.id,
                "title": e.title,
                "category": e.category,
                "importance": e.importance,
                "direction": e.direction,
                "confidence": e.confidence,
                "published_at": e.published_at,
                "engine": e.analysis_engine,
            }
            for e in events
        ],
        "signals": {
            "as_of": as_of.isoformat() if as_of else None,
            "count": len(signals),
            "actionable": len(actionable),
            "top": sorted(
                [
                    {
                        "symbol": s.symbol,
                        "score": s.total_score,
                        "action": s.action,
                        "confidence": s.confidence,
                        "class": s.asset_class_code,
                    }
                    for s in signals
                    if s.total_score is not None
                ],
                key=lambda x: x["score"],
                reverse=True,
            )[:5],
        },
        "data_health": {
            "instruments_total": total,
            "auto_tracked": len(automatic),
            "fresh": len(fresh),
            "manual": len(manual),
            "restricted": len(restricted),
            "missing": len(missing),
            "coverage": round(len(fresh) / len(automatic), 3) if automatic else 0,
            "sources_total": len(sources),
            "sources_healthy": healthy_sources,
            "sources_unconfigured": len([s for s in sources if s["status"] == "unconfigured"]),
        },
        "notices": _notices(regime, sources, missing),
    }


def _notices(regime: dict, sources: list[dict], missing: list) -> list[dict]:
    notices = []
    if regime.get("state") == "极端事件风险":
        notices.append({"level": "critical", "text": "出现极端事件风险信号，优先控制风险敞口。"})
    elif regime.get("state") == "数据不足":
        notices.append({"level": "warning", "text": regime.get("explanation", "关键指标数据不足。")})
    unconfigured = [s["name"] for s in sources if s["status"] == "unconfigured"]
    if unconfigured:
        notices.append({"level": "info", "text": "以下数据源未配置或缺少依赖：" + "、".join(unconfigured[:4])})
    failed = [s["name"] for s in sources if s["status"] == "error"]
    if failed:
        notices.append({"level": "warning", "text": "采集异常：" + "、".join(failed[:4])})
    if missing:
        notices.append({"level": "info", "text": f"{len(missing)} 个自动跟踪品种尚无行情数据，评分会显示数据不足。"})
    return notices


@router.get("/regime")
def get_regime(db: Session = Depends(get_db), refresh: bool = False):
    from app.services.regime_service import compute_regime

    return compute_regime(db) if refresh else _regime(db)


@router.get("/regime/history")
def regime_history(db: Session = Depends(get_db)):
    row = db.get(SystemSetting, "regime_history")
    items = (row.value or {}).get("items", []) if row else []
    return {"items": items, "total": len(items)}


@router.get("/quotes")
def quotes(db: Session = Depends(get_db), asset_class: Optional[str] = None, limit: int = 40):
    stmt = select(Instrument).where(Instrument.enabled.is_(True))
    if asset_class:
        stmt = stmt.where(Instrument.asset_class_code.like(f"{asset_class}%"))
    instruments = db.scalars(stmt.limit(limit)).all()
    items = []
    for inst in instruments:
        obs = db.scalar(
            select(MarketObservation)
            .where(MarketObservation.symbol == inst.symbol)
            .order_by(MarketObservation.trade_date.desc(), MarketObservation.observed_at.desc())
        )
        items.append(
            {
                "symbol": inst.symbol,
                "name": inst.name,
                "class": inst.asset_class_code,
                "close": obs.close if obs else None,
                "change_pct": change,
                "trade_date": obs.trade_date.isoformat() if obs else None,
                "data_type": obs.data_type if obs else None,
                "status": inst.data_status,
                "source": obs.source_code if obs else None,
            }
        )
    return {"items": items, "total": len(items), "as_of": now_cn().isoformat(sep=" ", timespec="seconds")}


macro_router = APIRouter(prefix="/api/macro", tags=["macro"])


@macro_router.get("/indicators")
def macro_indicators(db: Session = Depends(get_db)):
    rows = db.execute(
        select(MacroObservation)
        .order_by(MacroObservation.indicator_code, MacroObservation.period.desc())
    ).scalars().all()
    grouped: dict[str, dict] = {}
    for obs in rows:
        key = f"{obs.indicator_code}|{obs.source_code}"
        entry = grouped.get(key)
        if entry is None:
            grouped[key] = {
                "code": obs.indicator_code,
                "name": obs.name,
                "country": obs.country,
                "unit": obs.unit,
                "source": obs.source_code,
                "latest_value": obs.value,
                "period": obs.period,
                "fetched_at": obs.fetched_at,
                "release_at": obs.release_at,
                "previous_value": obs.previous_value,
                "staleness": round(staleness_factor(obs.period_date), 2),
                "points": 1,
                "source_url": obs.source_url,
            }
        else:
            entry["points"] += 1
    items = sorted(grouped.values(), key=lambda x: (x["source"], x["code"]))
    return {"items": items, "total": len(items)}


@macro_router.get("/series/{code}")
def macro_series(code: str, db: Session = Depends(get_db), limit: int = Query(default=60, ge=2, le=400)):
    rows = db.scalars(
        select(MacroObservation)
        .where(MacroObservation.indicator_code == code)
        .order_by(MacroObservation.period.desc())
        .limit(limit)
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="未找到该宏观指标")
    rows = list(reversed(rows))
    return {
        "code": code,
        "name": rows[-1].name,
        "unit": rows[-1].unit,
        "source": rows[-1].source_code,
        "series": [
            {
                "period": r.period,
                "value": r.value,
                "fetched_at": r.fetched_at,
                "release_at": r.release_at,
                "revised": r.revised,
            }
            for r in rows
        ],
        "note": "period 为数据所属期间，fetched_at 为系统采集时间，两者不可混用。",
    }


@macro_router.get("/overview")
def macro_overview(db: Session = Depends(get_db)):
    rows = db.execute(
        select(MacroObservation).order_by(MacroObservation.indicator_code, MacroObservation.period.desc())
    ).scalars().all()
    grouped: dict[str, list] = {}
    for obs in rows:
        grouped.setdefault(obs.indicator_code, []).append(obs)
    cards = []
    for code, items in grouped.items():
        # 最新期间可能尚未发布数值，取最近一个有值的观测
        latest = next((item for item in items if item.value is not None), None)
        if latest is None:
            continue
        history = list(reversed(items[:12]))
        cards.append(
            {
                "code": code,
                "name": latest.name,
                "country": latest.country,
                "unit": latest.unit,
                "value": latest.value,
                "previous_value": latest.previous_value,
                "period": latest.period,
                "source": latest.source_code,
                "fetched_at": latest.fetched_at.isoformat(sep=" ", timespec="minutes"),
                "staleness": round(staleness_factor(latest.period_date), 2),
                "series": [{"period": h.period, "value": h.value} for h in history],
            }
        )
    return {"items": cards, "total": len(cards)}
