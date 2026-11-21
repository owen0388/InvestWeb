"""数据刷新调度：统一手动入口 + 数据新鲜度。

为什么需要这个模块：
候选池日线、质量分、雷达快照原本都没有定时任务，只有手动接口。
结果是雷达页面「看起来是新的」——分数是刚算的，但底层日线可能还是几天前的。
这类"假新鲜"比数据过期更危险，因为它不会被发现。

因此这里做三件事：
1. 暴露每层数据的最后更新时间与是否过期（让"旧"可见）
2. 提供统一手动刷新入口，带作用域与互斥锁
3. 支持查询当前是否有刷新在跑（避免重复触发把接口拖垮）
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.timeutil import now_cn
from app.models.entities import (
    DataSourceRun,
    EtfUniverse,
    MarketObservation,
    RadarSnapshot,
)

# scope -> (展示名, 预计秒数, 说明)
SCOPES: dict[str, tuple[str, int, str]] = {
    "quotes": ("行情快照", 15, "刷新资产目录 61 个品种的最新价"),
    "universe": ("ETF 候选池", 280, "重拉全市场 1,680 只 ETF 并重算准入门槛（约 4–5 分钟）"),
    "quality": ("候选池日线与质量分", 180, "为候选池补日线、取净值、重算质量分（约 2–3 分钟）"),
    "radar": ("雷达快照", 10, "按最新数据重算机会并写入当日快照"),
}

# 进程内互斥：同一时刻只允许一个刷新任务，避免用户连点把采集源打挂
_RUNNING: dict[str, dict] = {}


def _minutes_since(value: Optional[datetime]) -> Optional[float]:
    if value is None:
        return None
    delta = now_cn() - value
    return round(delta.total_seconds() / 60, 1)


def _layer(
    key: str,
    last_at: Optional[datetime],
    expect_minutes: float,
    detail: str = "",
    hard_stale: bool = False,
) -> dict:
    age = _minutes_since(last_at)
    label, _, _ = SCOPES.get(key, (key, 0, ""))
    if age is None:
        return {
            "key": key,
            "label": label,
            "last_at": None,
            "age_minutes": None,
            "expect_minutes": expect_minutes,
            "stale": True,
            "reason": "从未更新",
            "detail": detail,
        }
    # 超过预期 3 倍才算过期（与 health_check 的口径一致，避免刚过 1 秒就报警）
    stale = age > expect_minutes * 3
    return {
        "key": key,
        "label": label,
        "last_at": last_at.isoformat(sep=" ", timespec="seconds"),
        "age_minutes": age,
        "expect_minutes": expect_minutes,
        "stale": stale or hard_stale,
        "reason": "" if not stale else f"已 {age:.0f} 分钟未更新（预期 {expect_minutes:.0f} 分钟内）",
        "detail": detail,
    }


def status(db: Session) -> dict:
    """各层数据的最后更新时间。时间取真实写入时刻，不猜测。"""
    quote_at = db.scalar(select(func.max(MarketObservation.observed_at)))
    quote_count = db.scalar(select(func.count()).select_from(MarketObservation)) or 0

    snapshot_at = db.scalar(select(func.max(EtfUniverse.snapshot_at)))
    candidate_count = (
        db.scalar(
            select(func.count()).select_from(EtfUniverse).where(EtfUniverse.status == "candidate")
        )
        or 0
    )

    quality_at = db.scalar(
        select(func.max(DataSourceRun.finished_at)).where(
            DataSourceRun.job_name == "etf_quality_backfill"
        )
    )
    scored = (
        db.scalar(
            select(func.count())
            .select_from(EtfUniverse)
            .where(EtfUniverse.status == "candidate", EtfUniverse.quality_score.isnot(None))
        )
        or 0
    )

    radar_at = db.scalar(select(func.max(RadarSnapshot.created_at)))
    radar_date = db.scalar(select(func.max(RadarSnapshot.as_of)))

    layers = [
        _layer("quotes", quote_at, 10, f"库里共 {quote_count:,} 条行情记录"),
        _layer("universe", snapshot_at, 24 * 60, f"候选 {candidate_count} 只"),
        _layer(
            "quality",
            quality_at,
            24 * 60,
            f"{scored}/{candidate_count} 只有质量分",
            # 有候选却没有分数，等同于这一层没跑过
            hard_stale=(candidate_count > 0 and scored == 0),
        ),
        _layer(
            "radar",
            radar_at,
            24 * 60,
            f"最新快照日 {radar_date}" if radar_date else "尚无快照",
        ),
    ]
    running = list(_RUNNING.values())
    return {
        "as_of": now_cn().isoformat(sep=" ", timespec="seconds"),
        "layers": layers,
        "stale_count": sum(1 for layer in layers if layer["stale"]),
        "running": running,
        "session_state": _session_state(now_cn()),
    }


def _session_state(now: datetime) -> str:
    if now.weekday() >= 5:
        return "closed"
    minutes = now.hour * 60 + now.minute
    if minutes < 9 * 60 + 30:
        return "pre_open"
    if minutes >= 15 * 60 + 5:
        return "after_close"
    return "intraday"


def running_scopes() -> list[dict]:
    return list(_RUNNING.values())


def is_running(scope: str) -> bool:
    return any(item["scope"] == scope for item in _RUNNING.values())


def mark_start(scope: str) -> None:
    _RUNNING[scope] = {
        "scope": scope,
        "label": SCOPES.get(scope, (scope, 0, ""))[0],
        "started_at": now_cn().isoformat(sep=" ", timespec="seconds"),
    }


def mark_done(scope: str) -> None:
    _RUNNING.pop(scope, None)


def plan(scope: str) -> list[str]:
    """把 all 展开成有序步骤：先数据、再计算、最后快照。"""
    if scope != "all":
        return [scope] if scope in SCOPES else []
    return ["quotes", "universe", "quality", "radar"]


def run_scope(db: Session, scope: str) -> dict:
    """同步执行一个刷新步骤。调用方负责放进后台任务。"""
    from app.services.collector_service import run_market_quotes
    from app.services.etf_history_service import refresh_quality
    from app.services.opportunity_radar_service import build_daily
    from app.services.universe_service import refresh_universe

    if scope == "quotes":
        result = run_market_quotes(db, trigger="manual")
        return {"scope": scope, "message": result.message, "records": result.records}
    if scope == "universe":
        return {"scope": scope, **refresh_universe(db)}
    if scope == "quality":
        return {"scope": scope, **refresh_quality(db)}
    if scope == "radar":
        result = build_daily(db)
        return {
            "scope": scope,
            "message": result["message"],
            "items": len(result["items"]),
            "qualified": result.get("qualified"),
        }
    return {"scope": scope, "message": "未知作用域"}
