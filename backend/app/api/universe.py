"""ETF 候选池（C 层）接口。"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.utils import paginate
from app.core.db import get_db, session_scope
from app.models.entities import EtfUniverse
from app.services.etf_history_service import refresh_quality
from app.services.universe_service import refresh_universe, universe_summary

router = APIRouter(prefix="/api/universe", tags=["universe"])

CATEGORY_LABELS = {
    "broad": "宽基",
    "sector": "行业主题",
    "cross_border": "跨境",
    "bond": "债券",
    "money": "货币",
    "commodity": "商品",
    "reit": "REITs",
    "other": "其他",
}


def serialize(row: EtfUniverse) -> dict:
    return {
        "code": row.code,
        "symbol": row.symbol,
        "name": row.name,
        "market": row.market,
        "category": row.category,
        "category_label": CATEGORY_LABELS.get(row.category, row.category),
        "tracked_index": row.tracked_index,
        "price": row.price,
        "change_pct": row.change_pct,
        "amount": row.amount,
        "amount_20d": row.amount_20d,
        "amount_basis": row.amount_basis,
        "mktcap": row.mktcap,
        "endnav": row.endnav,
        "nav": row.nav,
        "premium_est": row.premium_est,
        "premium_basis": row.premium_basis,
        "list_date": row.list_date.isoformat() if row.list_date else None,
        "listed_days": row.listed_days,
        "status": row.status,
        "reject_reasons": row.reject_reasons or [],
        "data_coverage": row.data_coverage or {},
        "quality_score": row.quality_score,
        "quality_confidence": row.quality_confidence,
        "quality_breakdown": row.quality_breakdown or {},
        "snapshot_at": row.snapshot_at,
    }


@router.get("")
def list_universe(
    db: Session = Depends(get_db),
    status: Optional[str] = None,
    category: Optional[str] = None,
    q: Optional[str] = None,
    sort: str = "default",
    page: int = 1,
    page_size: int = 30,
):
    stmt = select(EtfUniverse)
    if status:
        stmt = stmt.where(EtfUniverse.status == status)
    if category:
        stmt = stmt.where(EtfUniverse.category == category)
    if q:
        keyword = f"%{q.strip()}%"
        stmt = stmt.where(or_(EtfUniverse.name.like(keyword), EtfUniverse.code.like(keyword)))
    if sort == "quality":
        # 质量分未知的排在后面，不用 0 冒充，避免"没算过"看起来像"很差"
        stmt = stmt.order_by(
            EtfUniverse.quality_score.desc().nullslast(),
            EtfUniverse.amount.desc().nullslast(),
        )
    elif sort == "category":
        stmt = stmt.order_by(EtfUniverse.category, EtfUniverse.amount.desc().nullslast())
    else:
        stmt = stmt.order_by(EtfUniverse.status.desc(), EtfUniverse.amount.desc().nullslast())
    return paginate(db, stmt, page, page_size, serialize)


@router.get("/summary")
def summary(db: Session = Depends(get_db)):
    return universe_summary(db)


@router.get("/{code}")
def detail(code: str, db: Session = Depends(get_db)):
    row = db.scalar(select(EtfUniverse).where(EtfUniverse.code == code))
    if not row:
        raise HTTPException(status_code=404, detail="该 ETF 尚未进入候选池，请先刷新")
    return serialize(row)


def _run_refresh(limit: Optional[int] = None) -> None:
    with session_scope() as db:
        refresh_universe(db, detail_limit=limit)


def _run_quality() -> None:
    with session_scope() as db:
        refresh_quality(db)


@router.post("/quality")
def trigger_quality(background: BackgroundTasks):
    """补齐候选池日线与净值并计算质量分（约 1–2 分钟，后台执行）。"""
    background.add_task(_run_quality)
    return {"accepted": True, "message": "质量分计算已启动，完成后可查看 /api/universe?sort=quality"}


@router.post("/refresh")
def trigger_refresh(background: BackgroundTasks, limit: Optional[int] = Query(default=None, ge=10, le=1600)):
    """后台刷新：全量拉取约 16 秒，详情约 0.4 秒/只，立即返回 accepted。"""
    background.add_task(_run_refresh, limit)
    return {"accepted": True, "message": "ETF 候选池刷新已在后台启动，完成后可查看 summary"}
