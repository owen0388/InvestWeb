"""新闻、事件与机会雷达接口。"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.utils import paginate
from app.core.db import get_db
from app.core.timeutil import now_cn
from app.models.entities import (
    AssetClass,
    AssetSignal,
    Event,
    EventAnalysisVersion,
    EventEvidence,
    EventImpact,
    Instrument,
    RawDocument,
)
from app.services import config_service

router = APIRouter(prefix="/api", tags=["events"])

CATEGORY_LABELS = {c["code"]: c["name"] for c in config_service.signal_rules().get("event_categories", [])}
ACTION_SET = {"考虑提高权重", "积极研究", "考虑降低权重", "降低风险"}


def serialize_news(doc: RawDocument) -> dict:
    return {
        "id": doc.id,
        "title": doc.title,
        "summary": doc.summary,
        "url": doc.url,
        "media": doc.media,
        "language": doc.language,
        "published_at": doc.published_at,
        "fetched_at": doc.fetched_at,
        "reliability": doc.reliability,
        "source": doc.source_code,
        "event_id": doc.event_id,
        "status": doc.status,
    }


def serialize_event(event: Event) -> dict:
    return {
        "id": event.id,
        "event_key": event.event_key,
        "title": event.title,
        "summary": event.summary,
        "category": event.category,
        "category_label": CATEGORY_LABELS.get(event.category, event.category),
        "published_at": event.published_at,
        "collected_at": event.collected_at,
        "importance": event.importance,
        "novelty": event.novelty,
        "direction": event.direction,
        "signal_strength": event.signal_strength,
        "confidence": event.confidence,
        "data_quality": event.data_quality,
        "source_count": event.source_count,
        "evidence_count": event.evidence_count,
        "reliability": event.reliability,
        "horizon": event.horizon,
        "priced_in": event.priced_in,
        "status": event.status,
        "analysis_version": event.analysis_version,
        "analysis_engine": event.analysis_engine,
        "analyzed_at": event.analyzed_at,
        "what_happened": event.what_happened,
        "what_uncertain": event.what_uncertain,
        "what_changed": event.what_changed,
        "invalidation": event.invalidation,
        "countries": event.countries,
        "institutions": event.institutions,
        "companies": event.companies,
        "industries": event.industries,
        "watch_items": event.watch_items,
    }


@router.get("/news")
def list_news(
    db: Session = Depends(get_db),
    q: Optional[str] = None,
    source: Optional[str] = None,
    media: Optional[str] = None,
    days: int = Query(default=7, ge=1, le=90),
    page: int = 1,
    page_size: int = 20,
):
    stmt = select(RawDocument).where(RawDocument.fetched_at >= now_cn() - timedelta(days=days))
    if q:
        keyword = f"%{q.strip()}%"
        stmt = stmt.where(or_(RawDocument.title.like(keyword), RawDocument.summary.like(keyword)))
    if source:
        stmt = stmt.where(RawDocument.source_code == source)
    if media:
        stmt = stmt.where(RawDocument.media == media)
    stmt = stmt.order_by(RawDocument.published_at.desc(), RawDocument.fetched_at.desc())
    result = paginate(db, stmt, page, page_size, serialize_news)
    return result


@router.get("/news/sources")
def news_sources(db: Session = Depends(get_db)):
    rows = db.execute(
        select(RawDocument.media, func.count(RawDocument.id))
        .group_by(RawDocument.media)
        .order_by(func.count(RawDocument.id).desc())
        .limit(20)
    ).all()
    return {"items": [{"media": m, "count": c} for m, c in rows]}


@router.get("/events")
def list_events(
    db: Session = Depends(get_db),
    q: Optional[str] = None,
    category: Optional[str] = None,
    direction: Optional[str] = None,
    min_importance: float = 0,
    days: int = Query(default=7, ge=1, le=90),
    engine: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    stmt = select(Event).where(Event.published_at >= now_cn() - timedelta(days=days))
    if q:
        keyword = f"%{q.strip()}%"
        stmt = stmt.where(or_(Event.title.like(keyword), Event.summary.like(keyword)))
    if category:
        stmt = stmt.where(Event.category == category)
    if direction:
        stmt = stmt.where(Event.direction == direction)
    if min_importance:
        stmt = stmt.where(Event.importance >= min_importance)
    if engine:
        stmt = stmt.where(Event.analysis_engine.like(f"{engine}%"))
    stmt = stmt.order_by(Event.importance.desc(), Event.published_at.desc())
    return paginate(db, stmt, page, page_size, serialize_event)


@router.get("/events/categories")
def event_categories(db: Session = Depends(get_db)):
    rows = db.execute(
        select(Event.category, func.count(Event.id)).group_by(Event.category)
    ).all()
    counts = {code: count for code, count in rows}
    return {
        "items": [
            {
                "code": cat["code"],
                "name": cat["name"],
                "count": counts.get(cat["code"], 0),
            }
            for cat in config_service.signal_rules().get("event_categories", [])
        ]
    }


@router.get("/events/{event_id}")
def event_detail(event_id: int, db: Session = Depends(get_db)):
    event = db.get(Event, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="事件不存在")
    evidence = db.scalars(
        select(EventEvidence).where(EventEvidence.event_id == event.id).order_by(EventEvidence.published_at)
    ).all()
    impacts = db.scalars(select(EventImpact).where(EventImpact.event_id == event.id)).all()
    versions = db.scalars(
        select(EventAnalysisVersion)
        .where(EventAnalysisVersion.event_id == event.id)
        .order_by(EventAnalysisVersion.version.desc())
    ).all()
    documents = {
        doc.id: doc
        for doc in db.scalars(
            select(RawDocument).where(RawDocument.event_id == event.id)
        ).all()
    }
    impacts_payload = []
    for impact in impacts:
        inst = db.scalar(select(Instrument).where(Instrument.symbol == impact.instrument_symbol))
        cls = db.scalar(select(AssetClass).where(AssetClass.code == inst.asset_class_code)) if inst else None
        impacts_payload.append(
            {
                "symbol": impact.instrument_symbol,
                "name": inst.name if inst else impact.instrument_symbol,
                "class": cls.name if cls else impact.asset_class_code,
                "class_code": impact.asset_class_code,
                "direction": impact.direction,
                "mechanism": impact.mechanism,
                "horizon": impact.horizon,
                "magnitude": impact.magnitude,
                "confidence": impact.confidence,
            }
        )
    return {
        "event": {
            **serialize_event(event),
            "analysis": event.analysis,
            "transmission": event.transmission,
            "counter_evidence": event.counter_evidence,
        },
        "evidence": [
            {
                "id": e.id,
                "document_id": e.document_id,
                "stance": e.stance,
                "source_name": e.source_name,
                "url": e.url,
                "quote": e.quote,
                "reliability": e.reliability,
                "published_at": e.published_at,
                "title": documents.get(e.document_id).title if e.document_id in documents else None,
            }
            for e in evidence
        ],
        "impacts": impacts_payload,
        "versions": [
            {
                "version": v.version,
                "engine": v.engine,
                "direction": v.direction,
                "confidence": v.confidence,
                "importance": v.importance,
                "change_reason": v.change_reason,
                "created_at": v.created_at,
                "analysis": v.analysis,
            }
            for v in versions
        ],
        "disclaimer": "事件结论为研究参考，方向判断允许为未知或存在冲突；置信度仅代表证据质量。",
    }


@router.get("/opportunities")
def opportunities(
    db: Session = Depends(get_db),
    asset_class: Optional[str] = None,
    action: Optional[str] = None,
    min_confidence: float = 0.0,
    direction: Optional[str] = None,
    days: int = Query(default=30, ge=1, le=180),
    page: int = 1,
    page_size: int = 12,
):
    as_of = db.scalar(select(AssetSignal.as_of).order_by(AssetSignal.as_of.desc()))
    stmt = select(AssetSignal)
    if as_of:
        stmt = stmt.where(AssetSignal.as_of == as_of)
    if asset_class:
        stmt = stmt.where(AssetSignal.asset_class_code.like(f"{asset_class}%"))
    if action:
        stmt = stmt.where(AssetSignal.action == action)
    if min_confidence:
        stmt = stmt.where(AssetSignal.confidence >= min_confidence)
    stmt = stmt.where(AssetSignal.total_score.is_not(None))
    signals = db.scalars(stmt).all()

    since = now_cn() - timedelta(days=days)
    cards = []
    for signal in signals:
        if signal.action in ("暂不判断",):
            continue
        inst = db.scalar(select(Instrument).where(Instrument.symbol == signal.symbol))
        cls = db.scalar(select(AssetClass).where(AssetClass.code == signal.asset_class_code))
        impacts = db.execute(
            select(EventImpact, Event)
            .join(Event, Event.id == EventImpact.event_id)
            .where(
                EventImpact.instrument_symbol == signal.symbol,
                Event.published_at >= since,
            )
            .order_by(Event.importance.desc())
            .limit(2)
        ).all()
        if direction and impacts:
            if not any(i.direction == direction for i, _ in impacts):
                continue
        driver = (signal.rationale or {}).get("summary", "")
        cards.append(
            {
                "type": "signal",
                "symbol": signal.symbol,
                "name": inst.name if inst else signal.symbol,
                "asset_class": cls.name if cls else signal.asset_class_code,
                "asset_class_code": signal.asset_class_code,
                "score": signal.total_score,
                "confidence": signal.confidence,
                "data_quality": signal.data_quality,
                "action": signal.action,
                "direction": (
                    "bullish" if (signal.total_score or 50) >= 55 else
                    "bearish" if (signal.total_score or 50) <= 45 else "mixed"
                ),
                "as_of": signal.as_of.isoformat(),
                "summary": driver,
                "changes": (signal.rationale or {}).get("score_change"),
                "missing_inputs": signal.missing_inputs,
                "related_events": [
                    {
                        "id": event.id,
                        "title": event.title,
                        "importance": event.importance,
                        "direction": impact.direction,
                        "published_at": event.published_at,
                    }
                    for impact, event in impacts
                ],
                "decision_hint": "研究排序结果，不构成买卖建议；需结合个人约束确认。",
            }
        )
    cards.sort(key=lambda c: abs((c["score"] or 50) - 50) * (c["confidence"] or 0), reverse=True)
    total = len(cards)
    start = (page - 1) * page_size
    return {
        "items": cards[start : start + page_size],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": (total + page_size - 1) // page_size,
        "as_of": as_of.isoformat() if as_of else None,
        "filters": {
            "asset_classes": [
                {"code": c.code, "name": c.name}
                for c in db.scalars(select(AssetClass).where(AssetClass.level == 1)).all()
            ],
            "actions": sorted(ACTION_SET | {"维持配置", "观察等待", "暂不判断"}),
        },
    }


@router.get("/events/{event_id}/etfs")
def event_etfs(event_id: int, limit: int = 10, db: Session = Depends(get_db)):
    """事件 → 候选池 ETF 映射（两级：资产类别 + 主题族）与信息差因子。"""
    from app.services.event_etf_service import map_event_to_etfs

    return map_event_to_etfs(db, event_id, limit=limit)
