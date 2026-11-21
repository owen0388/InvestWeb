"""资产目录与单品分析接口。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.schemas import InstrumentUpdate
from app.api.utils import paginate
from app.core.db import get_db
from app.core.timeutil import now_cn
from app.models.entities import (
    AssetClass,
    AssetSignal,
    Event,
    EventImpact,
    Instrument,
    InstrumentAlias,
    MarketObservation,
)
from app.services.market_data import load_close_series, quote_with_change

router = APIRouter(prefix="/api", tags=["catalog"])

MODULE_LABELS = {
    "equity_cn": "股票模型：趋势、估值、盈利、政策与市场广度",
    "fund": "基金模型：底层资产、费用、跟踪误差、折溢价与流动性",
    "bond": "固收模型：收益率、久期、期限结构、信用利差",
    "gold": "黄金模型：实际利率、美元、央行需求与避险需求",
    "commodity": "商品模型：供需、库存、期现结构与季节性",
    "reit": "REITs 模型：现金流分派、运营、融资成本与利率敏感性",
    "cash": "现金模型：收益率、期限、赎回与流动性",
    "wealth": "资管产品模型：收益率、期限、赎回、信用与数据覆盖",
    "long_term": "长期资产模型：现金流、期限、退保/退出成本",
    "derivative": "衍生品模型：杠杆、保证金、到期、波动率与最大亏损",
    "overseas": "跨境模型：汇率、交易日、折溢价与准入",
    "generic": "通用模型：趋势、宏观、事件与流动性",
}


def _latest_obs(db: Session, symbol: str) -> Optional[MarketObservation]:
    return db.scalar(
        select(MarketObservation)
        .where(MarketObservation.symbol == symbol)
        .order_by(MarketObservation.trade_date.desc(), MarketObservation.observed_at.desc())
    )


def serialize_instrument(db: Session, inst: Instrument) -> dict:
    cls = db.scalar(select(AssetClass).where(AssetClass.code == inst.asset_class_code))
    obs, change = quote_with_change(db, inst.symbol)
    return {
        "symbol": inst.symbol,
        "name": inst.name,
        "asset_class_code": inst.asset_class_code,
        "asset_class_name": cls.name if cls else inst.asset_class_code,
        "market": inst.market,
        "currency": inst.currency,
        "instrument_type": inst.instrument_type,
        "data_status": inst.data_status,
        "data_status_label": {
            "normal": "正常",
            "delayed": "延迟",
            "missing": "缺失",
            "manual": "人工维护",
            "restricted": "受限",
            "unverified": "待核验",
        }.get(inst.data_status, inst.data_status),
        "data_source_code": inst.data_source_code,
        "analysis_module": inst.analysis_module or (cls.analysis_module if cls else "generic"),
        "analysis_module_label": MODULE_LABELS.get(
            inst.analysis_module or (cls.analysis_module if cls else "generic"), ""
        ),
        "risk_level": inst.risk_level,
        "liquidity_tier": inst.liquidity_tier,
        "qualification_required": inst.qualification_required,
        "tags": inst.tags or [],
        "notes": inst.notes,
        "enabled": inst.enabled,
        "favorite": inst.favorite,
        "last_data_at": inst.last_data_at,
        "latest": {
            "close": obs.close if obs else None,
            "change_pct": change,
            "trade_date": obs.trade_date.isoformat() if obs else None,
            "data_type": obs.data_type if obs else None,
            "source": obs.source_code if obs else None,
            "observed_at": obs.observed_at.isoformat(sep=" ", timespec="seconds") if obs else None,
        },
    }


@router.get("/asset-classes")
def list_asset_classes(db: Session = Depends(get_db)):
    classes = db.scalars(select(AssetClass).order_by(AssetClass.sort_order)).all()
    instruments = db.scalars(select(Instrument)).all()
    counts: dict[str, dict] = {}
    for inst in instruments:
        key = inst.asset_class_code
        entry = counts.setdefault(key, {"total": 0, "normal": 0, "manual": 0, "missing": 0, "enabled": 0})
        entry["total"] += 1
        entry["enabled"] += int(bool(inst.enabled))
        if inst.data_status in ("normal", "delayed"):
            entry["normal"] += 1
        elif inst.data_status == "manual":
            entry["manual"] += 1
        else:
            entry["missing"] += 1
    by_parent: dict[str, list] = {}
    for cls in classes:
        if cls.level == 2 and cls.parent_code:
            by_parent.setdefault(cls.parent_code, []).append(
                {
                    "code": cls.code,
                    "name": cls.name,
                    "counts": counts.get(cls.code, {}),
                    "analysis_module": cls.analysis_module,
                }
            )
    result = []
    for cls in classes:
        if cls.level != 1:
            continue
        result.append(
            {
                "code": cls.code,
                "name": cls.name,
                "description": cls.description,
                "analysis_module": cls.analysis_module,
                "analysis_module_label": MODULE_LABELS.get(cls.analysis_module, ""),
                "tradable": cls.tradable,
                "liquidity_tier": cls.liquidity_tier,
                "default_min_weight": cls.default_min_weight,
                "default_max_weight": cls.default_max_weight,
                "counts": counts.get(cls.code, {}),
                "children": by_parent.get(cls.code, []),
            }
        )
    return {"items": result, "total": len(result)}


@router.get("/instruments")
def list_instruments(
    db: Session = Depends(get_db),
    asset_class: Optional[str] = Query(default=None),
    q: Optional[str] = None,
    status: Optional[str] = None,
    market: Optional[str] = None,
    enabled: Optional[bool] = None,
    favorite: Optional[bool] = None,
    page: int = 1,
    page_size: int = 24,
):
    stmt = select(Instrument)
    if asset_class:
        stmt = stmt.where(Instrument.asset_class_code.like(f"{asset_class}%"))
    if q:
        keyword = f"%{q.strip()}%"
        stmt = stmt.where(or_(Instrument.name.like(keyword), Instrument.symbol.ilike(keyword)))
    if status:
        stmt = stmt.where(Instrument.data_status == status)
    if market:
        stmt = stmt.where(Instrument.market == market)
    if enabled is not None:
        stmt = stmt.where(Instrument.enabled.is_(enabled))
    if favorite:
        stmt = stmt.where(Instrument.favorite.is_(True))
    stmt = stmt.order_by(Instrument.asset_class_code, Instrument.symbol)
    return paginate(db, stmt, page, page_size, lambda row: serialize_instrument(db, row))


@router.get("/instruments/{symbol}")
def instrument_detail(
    symbol: str,
    db: Session = Depends(get_db),
    days: int = Query(default=180, ge=20, le=900),
):
    inst = db.scalar(select(Instrument).where(Instrument.symbol == symbol))
    if not inst:
        raise HTTPException(status_code=404, detail="未找到该品种")
    cls = db.scalar(select(AssetClass).where(AssetClass.code == inst.asset_class_code))
    parent = db.scalar(select(AssetClass).where(AssetClass.code == cls.parent_code)) if cls and cls.parent_code else None
    series = load_close_series(db, symbol, days)
    history = [
        {"date": idx.isoformat() if hasattr(idx, "isoformat") else str(idx), "close": round(float(value), 4)}
        for idx, value in series.items()
    ]
    signals = db.scalars(
        select(AssetSignal).where(AssetSignal.instrument_id == inst.id).order_by(AssetSignal.as_of.desc()).limit(30)
    ).all()
    impacts = db.execute(
        select(EventImpact, Event)
        .join(Event, Event.id == EventImpact.event_id)
        .where(EventImpact.instrument_symbol == symbol)
        .order_by(Event.published_at.desc())
        .limit(10)
    ).all()
    aliases = db.scalars(select(InstrumentAlias).where(InstrumentAlias.instrument_id == inst.id)).all()
    latest_signal = signals[0] if signals else None

    return {
        "instrument": serialize_instrument(db, inst),
        "class": {
            "code": cls.code if cls else None,
            "name": cls.name if cls else None,
            "parent_code": cls.parent_code if cls else None,
            "parent_name": parent.name if parent else None,
            "description": cls.description if cls else "",
            "min_weight": cls.default_min_weight if cls else None,
            "max_weight": cls.default_max_weight if cls else None,
        },
        "history": history,
        "signals": [
            {
                "as_of": s.as_of.isoformat(),
                "total_score": s.total_score,
                "confidence": s.confidence,
                "data_quality": s.data_quality,
                "action": s.action,
                "trend": s.trend,
                "macro": s.macro,
                "catalyst": s.catalyst,
                "risk": s.risk,
                "liquidity": s.liquidity,
                "valuation": s.valuation,
                "regime": s.regime,
                "components": s.components,
                "rationale": s.rationale,
                "missing_inputs": s.missing_inputs,
                "version": s.version,
            }
            for s in signals
        ],
        "latest_signal": (
            {
                "total_score": latest_signal.total_score,
                "confidence": latest_signal.confidence,
                "data_quality": latest_signal.data_quality,
                "action": latest_signal.action,
                "as_of": latest_signal.as_of.isoformat(),
                "rationale": latest_signal.rationale,
                "missing_inputs": latest_signal.missing_inputs,
            }
            if latest_signal
            else None
        ),
        "events": [
            {
                "id": event.id,
                "title": event.title,
                "published_at": event.published_at,
                "importance": event.importance,
                "direction": impact.direction,
                "category": event.category,
                "mechanism": impact.mechanism,
            }
            for impact, event in impacts
        ],
        "aliases": [{"alias": a.alias, "type": a.alias_type} for a in aliases],
        "data_notes": [
            f"数据源：{inst.data_source_code or '未配置'}",
            f"最后采集：{inst.last_data_at.isoformat(sep=' ', timespec='minutes') if inst.last_data_at else '从未成功采集'}",
            inst.notes,
        ],
        "generated_at": now_cn().isoformat(sep=" ", timespec="seconds"),
    }


@router.patch("/instruments/{symbol}")
def update_instrument(symbol: str, payload: InstrumentUpdate, db: Session = Depends(get_db)):
    inst = db.scalar(select(Instrument).where(Instrument.symbol == symbol))
    if not inst:
        raise HTTPException(status_code=404, detail="未找到该品种")
    if payload.enabled is not None:
        inst.enabled = payload.enabled
    if payload.favorite is not None:
        inst.favorite = payload.favorite
    if payload.notes is not None:
        inst.notes = payload.notes[:1000]
    if payload.manual_value is not None:
        if inst.data_source_code != "manual":
            raise HTTPException(status_code=400, detail="仅人工维护品种支持录入估值")
        from datetime import date

        obs = db.scalar(
            select(MarketObservation).where(
                MarketObservation.symbol == inst.symbol,
                MarketObservation.trade_date == date.today(),
                MarketObservation.source_code == "manual_entry",
            )
        )
        if obs is None:
            obs = MarketObservation(
                instrument_id=inst.id,
                symbol=inst.symbol,
                trade_date=date.today(),
                source_code="manual_entry",
                data_type="manual",
            )
            db.add(obs)
        obs.close = payload.manual_value
        obs.observed_at = now_cn()
        obs.source_url = ""
        inst.data_status = "manual"
        inst.last_data_at = now_cn()
    if payload.manual_note:
        inst.notes = (inst.notes + f"｜用户备注：{payload.manual_note}")[:1500]
    db.commit()
    return {"ok": True, "instrument": serialize_instrument(db, inst)}
