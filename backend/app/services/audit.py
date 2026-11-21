"""关键操作审计。所有写入只追加，不覆盖历史。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.entities import AuditLog


def log(db: Session, action: str, target: str = "", detail: dict | None = None, actor: str = "local") -> None:
    db.add(AuditLog(actor=actor, action=action, target=target[:96], detail=detail or {}))
