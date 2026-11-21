"""交易日志接口。

支持三类记录：
- followed：照雷达信号执行
- skipped ：信号出现但我没做（用信号价记一笔"假设成交"，事后能算"如果做了会怎样"）
- manual  ：自己临时决定，与信号无关
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.timeutil import now_cn
from app.models.entities import TradeJournal
from app.services import audit, journal_service

router = APIRouter(prefix="/api/journal", tags=["journal"])

DECISIONS = {"followed", "skipped", "manual"}
ACTIONS = {"buy", "sell"}


class JournalCreate(BaseModel):
    symbol: str = Field(min_length=1, max_length=32)
    name: str = ""
    asset_class_code: str = ""
    portfolio_id: Optional[int] = None
    source: str = "manual"
    signal_score: Optional[float] = None
    signal_state: str = ""
    decision: str = "manual"
    action: str = "buy"
    planned_price: Optional[float] = None
    actual_price: float = Field(gt=0)
    quantity: float = Field(gt=0)
    fee: float = 0.0
    thesis: str = ""
    invalidation: str = ""
    opened_at: Optional[datetime] = None

    @field_validator("decision")
    @classmethod
    def _check_decision(cls, value: str) -> str:
        if value not in DECISIONS:
            raise ValueError(f"decision 必须是 {sorted(DECISIONS)} 之一")
        return value

    @field_validator("action")
    @classmethod
    def _check_action(cls, value: str) -> str:
        if value not in ACTIONS:
            raise ValueError(f"action 必须是 {sorted(ACTIONS)} 之一")
        return value

    @field_validator("invalidation")
    @classmethod
    def _require_invalidation(cls, value: str) -> str:
        # 失效条件必须在下单时写清楚，事后补等于没写
        if not value.strip():
            raise ValueError("必须填写失效条件：什么情况下承认这笔判断错了")
        return value.strip()


class JournalClose(BaseModel):
    exit_price: float = Field(gt=0)
    exit_reason: str = Field(min_length=1)
    fee: float = 0.0
    closed_at: Optional[datetime] = None
    notes: str = ""


def _serialize(row: TradeJournal) -> dict:
    return {
        "id": row.id,
        "symbol": row.symbol,
        "name": row.name,
        "asset_class_code": row.asset_class_code,
        "portfolio_id": row.portfolio_id,
        "source": row.source,
        "signal_score": row.signal_score,
        "signal_state": row.signal_state,
        "decision": row.decision,
        "action": row.action,
        "planned_price": row.planned_price,
        "actual_price": row.actual_price,
        "quantity": row.quantity,
        "amount": row.amount,
        "fee": row.fee,
        "thesis": row.thesis,
        "invalidation": row.invalidation,
        "status": row.status,
        "opened_at": row.opened_at,
        "closed_at": row.closed_at,
        "exit_price": row.exit_price,
        "exit_reason": row.exit_reason,
        "pnl": row.pnl,
        "pnl_pct": row.pnl_pct,
        "holding_days": row.holding_days,
        "notes": row.notes,
    }


@router.get("")
def list_journal(
    db: Session = Depends(get_db),
    status: Optional[str] = None,
    decision: Optional[str] = None,
    symbol: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    stmt = select(TradeJournal).order_by(TradeJournal.opened_at.desc())
    if status:
        stmt = stmt.where(TradeJournal.status == status)
    if decision:
        stmt = stmt.where(TradeJournal.decision == decision)
    if symbol:
        stmt = stmt.where(TradeJournal.symbol == symbol)
    rows = db.scalars(stmt.limit(limit)).all()
    return {"items": [_serialize(row) for row in rows], "total": len(rows)}


@router.post("")
def create_entry(payload: JournalCreate, db: Session = Depends(get_db)):
    row = TradeJournal(
        **payload.model_dump(exclude={"opened_at"}),
        amount=round(payload.actual_price * payload.quantity, 2),
        status="open",
        opened_at=payload.opened_at or now_cn(),
    )
    db.add(row)
    db.flush()
    audit.log(db, "journal.create", payload.symbol, {"decision": payload.decision})
    db.commit()
    return _serialize(row)


@router.put("/{entry_id}/close")
def close_entry(entry_id: int, payload: JournalClose, db: Session = Depends(get_db)):
    row = db.get(TradeJournal, entry_id)
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    if row.status != "open":
        raise HTTPException(status_code=409, detail="该记录已平仓，不能重复平仓")

    pnl, pnl_pct = journal_service.compute_pnl(
        entry_price=row.actual_price,
        exit_price=payload.exit_price,
        quantity=row.quantity,
        fee_open=row.fee,
        fee_close=payload.fee,
    )
    closed_at = payload.closed_at or now_cn()
    row.exit_price = payload.exit_price
    row.exit_reason = payload.exit_reason
    row.closed_at = closed_at
    row.pnl = pnl
    row.pnl_pct = pnl_pct
    row.holding_days = max((closed_at.date() - row.opened_at.date()).days, 0)
    row.status = "closed"
    if payload.notes:
        row.notes = (row.notes + "\n" + payload.notes).strip()
    audit.log(db, "journal.close", row.symbol, {"pnl": pnl, "reason": payload.exit_reason})
    db.commit()
    return _serialize(row)


@router.delete("/{entry_id}")
def delete_entry(entry_id: int, db: Session = Depends(get_db)):
    row = db.get(TradeJournal, entry_id)
    if not row:
        raise HTTPException(status_code=404, detail="记录不存在")
    audit.log(db, "journal.delete", row.symbol, {})
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    rows = db.scalars(select(TradeJournal)).all()
    payload = journal_service.summarize([_serialize(row) for row in rows])
    payload["comparison"] = journal_service.compare_followed_vs_skipped(payload)
    return payload
