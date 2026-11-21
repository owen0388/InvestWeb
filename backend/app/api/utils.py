"""分页与序列化辅助。"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select


def iso(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    return value


def paginate(db, stmt, page: int, page_size: int, serializer) -> dict:
    page = max(1, page)
    page_size = max(1, min(page_size, 200))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    return {
        "items": [serializer(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": (total + page_size - 1) // page_size,
    }


def clamp_text(value: str | None, limit: int = 400) -> str:
    text = (value or "").strip()
    return text[:limit]
