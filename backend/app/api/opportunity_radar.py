"""机会雷达接口：每日 0–10 条候选。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.services.opportunity_radar_service import build_daily

router = APIRouter(prefix="/api/opportunity-radar", tags=["opportunity"])

CATEGORY_LABELS = {
    "broad": "宽基",
    "sector": "行业主题",
    "cross_border": "跨境",
    "bond": "债券",
    "money": "货币",
    "commodity": "商品",
    "other": "其他",
}

STATE_LABELS = {
    "CANDIDATE": "候选",
    "WATCH": "观察",
    "WAIT": "等回调",
    "HOLD": "持有",
    "TRIM": "减仓",
    "EXIT": "退出",
    "SCAN": "未入选",
    "TREND_BAD": "趋势未修复",
    "NO_DATA": "数据不足",
    "NO_QUALITY": "未评分",
    "LOW_QUALITY": "质量不达标",
    "EXCLUDED_CATEGORY": "不参与（货币类）",
}


@router.get("/daily")
def daily(limit: int = Query(default=10, ge=1, le=10), db: Session = Depends(get_db)):
    """当日机会列表。limit 上限固定为 10：超过 10 条等于没有重点。"""
    result = build_daily(db, limit=limit)
    for item in result["items"]:
        item["category_label"] = CATEGORY_LABELS.get(item["category"], item["category"])
        item["state_label"] = STATE_LABELS.get(item["state"], item["state"])
    result["bucket_labels"] = {k: STATE_LABELS.get(k, k) for k in result.get("buckets", {})}
    return result
