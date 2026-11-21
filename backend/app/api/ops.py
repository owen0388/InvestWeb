"""运维接口：健康检查、数据源、任务、Token 成本、通知与系统设置。"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ai.event_analyst import budget_status
from app.api.schemas import SettingsIn, SourceUpdate
from app.core.config import settings
from app.core.db import get_db
from app.core.timeutil import now_cn
from app.jobs import scheduler as job_scheduler
from app.models.entities import (
    AssetClass,
    AssetSignal,
    BacktestRun,
    DataSource,
    DataSourceRun,
    Event,
    Instrument,
    JobRun,
    MacroObservation,
    MarketObservation,
    ModelUsage,
    Notification,
    Portfolio,
    RawDocument,
    SystemSetting,
)
from app.services import audit, backup_service, collector_service, notification_service
from app.services.config_service import config_version

router = APIRouter(prefix="/api", tags=["ops"])


@router.get("/health")
def health(db: Session = Depends(get_db)):
    counts = {
        "instruments": db.scalar(select(func.count(Instrument.id))),
        "asset_classes": db.scalar(select(func.count(AssetClass.id))),
        "docs": db.scalar(select(func.count(RawDocument.id))),
        "events": db.scalar(select(func.count(Event.id))),
        "market_obs": db.scalar(select(func.count(MarketObservation.id))),
        "macro_obs": db.scalar(select(func.count(MacroObservation.id))),
        "signals": db.scalar(select(func.count(AssetSignal.id))),
        "backtests": db.scalar(select(func.count(BacktestRun.id))),
    }
    last_market = db.scalar(select(func.max(MarketObservation.observed_at)))
    last_news = db.scalar(select(func.max(RawDocument.fetched_at)))
    last_event = db.scalar(select(func.max(Event.collected_at)))
    failures = db.scalar(
        select(func.count(DataSource.id)).where(DataSource.status == "error")
    )
    status = "degraded" if (failures or 0) > 0 or not counts["market_obs"] else "ok"
    return {
        "status": status,
        "time": now_cn().isoformat(sep=" ", timespec="seconds"),
        "database": settings.DATABASE_URL.split("://")[0],
        "config_version": config_version(),
        "counts": counts,
        "freshness": {
            "market": last_market,
            "news": last_news,
            "event": last_event,
        },
        "sources": {
            "failed": failures or 0,
            "unconfigured": db.scalar(
                select(func.count(DataSource.id)).where(DataSource.status == "unconfigured")
            ),
        },
        "ai": budget_status(db),
        "scheduler": job_scheduler.scheduler_status()["running"],
        "disclaimer": "系统仅提供研究与模拟建议，不执行真实交易。",
    }


@router.get("/system/info")
def system_info(db: Session = Depends(get_db)):
    return {
        "app": settings.APP_NAME,
        "env": settings.APP_ENV,
        "timezone": settings.TIMEZONE,
        "database": settings.DATABASE_URL.split("://")[0],
        "scheduler_enabled": bool(settings.SCHEDULER_ENABLED),
        "llm": {
            "enabled": bool(settings.LLM_ENABLED),
            "provider": settings.LLM_PROVIDER,
            "base_url": settings.LLM_BASE_URL,
            "cheap_model": settings.LLM_MODEL_CHEAP,
            "strong_model": settings.LLM_MODEL_STRONG,
            "configured": budget_status(db)["configured"],
            "reason": budget_status(db)["reason"],
        },
        "data_keys": {
            "fred": bool(settings.FRED_API_KEY),
            "tushare": bool(settings.TUSHARE_TOKEN),
            "gdelt_enabled": bool(settings.GDELT_ENABLED),
        },
        "notifications": {"webhook": bool(settings.WEBHOOK_URL), "email": bool(settings.SMTP_HOST)},
    }


# ---------------------------------------------------------------------------
# 数据源
# ---------------------------------------------------------------------------
@router.get("/sources")
def list_sources(db: Session = Depends(get_db)):
    return {"items": collector_service.source_health_snapshot(db)}


@router.patch("/sources/{code}")
def update_source(code: str, payload: SourceUpdate, db: Session = Depends(get_db)):
    source = db.scalar(select(DataSource).where(DataSource.code == code))
    if not source:
        raise HTTPException(status_code=404, detail="数据源不存在")
    if payload.enabled is not None:
        source.enabled = payload.enabled
        if not payload.enabled:
            source.status = "disabled"
    if payload.interval_minutes is not None:
        source.interval_minutes = payload.interval_minutes
    audit.log(db, "source.update", code, payload.model_dump(exclude_none=True))
    db.commit()
    return {"ok": True, "source": collector_service.source_health_snapshot(db)}


@router.get("/sources/{code}/runs")
def source_runs(code: str, db: Session = Depends(get_db), limit: int = 20):
    runs = db.scalars(
        select(DataSourceRun)
        .where(DataSourceRun.source_code == code)
        .order_by(DataSourceRun.started_at.desc())
        .limit(min(limit, 100))
    ).all()
    return {
        "items": [
            {
                "id": r.id,
                "job_name": r.job_name,
                "trigger": r.trigger,
                "status": r.status,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "duration_ms": r.duration_ms,
                "records": r.records,
                "success_count": r.success_count,
                "failed_count": r.failed_count,
                "error": r.error,
                "detail": r.detail,
            }
            for r in runs
        ]
    }


@router.post("/sources/{code}/run")
def trigger_source(code: str, db: Session = Depends(get_db)):
    mapping = {
        "sina_quote": lambda: collector_service.run_market_quotes(db, trigger="manual"),
        "eastmoney_kline": lambda: collector_service.run_market_history(db, trigger="manual"),
        "eastmoney_news": lambda: collector_service.run_news(db, trigger="manual"),
        "worldbank_macro": lambda: collector_service.run_macro(db, trigger="manual"),
        "fred_macro": lambda: collector_service.run_macro(db, trigger="manual"),
    }
    if code not in mapping:
        raise HTTPException(
            status_code=400,
            detail="该数据源暂不支持手动触发（需要密钥或依赖，或为人工维护类型）",
        )
    # 采集函数是同步的，直接在当前请求中执行；新闻触发后顺便做事件分析
    audit.log(db, "source.trigger", code, {})
    db.commit()
    result = mapping[code]()
    extra = {}
    if code == "eastmoney_news":
        from app.services.event_engine import analyze_pending_events

        extra = analyze_pending_events(db)
        extra.pop("event_ids", None)
        extra["notifications"] = notification_service.sync_recent_notifications(db)
        notification_service.dispatch_pending(db)
    return {
        "ok": result.ok,
        "message": result.message,
        "records": result.records,
        "failed": result.failed,
        "errors": result.errors[:5],
        **extra,
    }


# ---------------------------------------------------------------------------
# 任务
# ---------------------------------------------------------------------------
@router.get("/jobs")
def list_jobs(db: Session = Depends(get_db)):
    status = job_scheduler.scheduler_status()
    recent = db.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(30)).all()
    by_id: dict[str, dict] = {}
    for run in recent:
        by_id.setdefault(
            run.job_id,
            {
                "status": run.status,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "error": run.error,
                "records": run.records,
            },
        )
    for job in status["jobs"]:
        job["last_run"] = by_id.get(job["id"])
    return status


@router.post("/jobs/{job_id}/run")
def trigger_job(job_id: str):
    if job_id not in job_scheduler.JOBS:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job_scheduler.run_job(job_id, trigger="manual")


@router.get("/jobs/runs")
def job_runs(db: Session = Depends(get_db), limit: int = 50):
    runs = db.scalars(select(JobRun).order_by(JobRun.started_at.desc()).limit(min(limit, 200))).all()
    return {
        "items": [
            {
                "id": r.id,
                "job_id": r.job_id,
                "status": r.status,
                "trigger": r.trigger,
                "started_at": r.started_at,
                "finished_at": r.finished_at,
                "records": r.records,
                "error": r.error,
                "detail": r.detail,
            }
            for r in runs
        ]
    }


# ---------------------------------------------------------------------------
# Token 成本
# ---------------------------------------------------------------------------
@router.get("/usage")
def usage(db: Session = Depends(get_db), days: int = 14):
    since = now_cn() - timedelta(days=days)
    rows = db.scalars(select(ModelUsage).where(ModelUsage.created_at >= since)).all()
    by_day: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    for row in rows:
        day = row.created_at.strftime("%Y-%m-%d")
        entry = by_day.setdefault(day, {"date": day, "calls": 0, "tokens": 0, "cost": 0.0, "cached": 0, "failed": 0})
        entry["calls"] += 1
        entry["tokens"] += row.prompt_tokens + row.completion_tokens
        entry["cost"] += row.cost_usd
        entry["cached"] += int(bool(row.cached))
        entry["failed"] += int(not row.success)
        model = by_model.setdefault(row.model or "-", {"model": row.model or "-", "calls": 0, "tokens": 0, "cost": 0.0})
        model["calls"] += 1
        model["tokens"] += row.prompt_tokens + row.completion_tokens
        model["cost"] += row.cost_usd
    for entry in by_day.values():
        entry["cost"] = round(entry["cost"], 4)
    for entry in by_model.values():
        entry["cost"] = round(entry["cost"], 4)
    return {
        "budget": budget_status(db),
        "totals": {
            "calls": len(rows),
            "tokens": sum(r.prompt_tokens + r.completion_tokens for r in rows),
            "cost_usd": round(sum(r.cost_usd for r in rows), 4),
            "failed": len([r for r in rows if not r.success]),
            "cached": len([r for r in rows if r.cached]),
        },
        "by_day": sorted(by_day.values(), key=lambda x: x["date"]),
        "by_model": sorted(by_model.values(), key=lambda x: x["cost"], reverse=True),
        "recent": [
            {
                "id": r.id,
                "model": r.model,
                "task": r.task,
                "prompt_tokens": r.prompt_tokens,
                "completion_tokens": r.completion_tokens,
                "cost_usd": r.cost_usd,
                "cached": r.cached,
                "success": r.success,
                "error": r.error,
                "created_at": r.created_at,
            }
            for r in sorted(rows, key=lambda x: x.created_at, reverse=True)[:20]
        ],
    }


# ---------------------------------------------------------------------------
# 通知
# ---------------------------------------------------------------------------
@router.get("/notifications")
def list_notifications(db: Session = Depends(get_db), limit: int = 30, level: Optional[str] = None):
    stmt = select(Notification)
    if level:
        stmt = stmt.where(Notification.level == level)
    notes = db.scalars(stmt.order_by(Notification.created_at.desc()).limit(min(limit, 100))).all()
    return {
        "items": [
            {
                "id": n.id,
                "channel": n.channel,
                "level": n.level,
                "title": n.title,
                "body": n.body,
                "event_id": n.event_id,
                "status": n.status,
                "created_at": n.created_at,
                "sent_at": n.sent_at,
            }
            for n in notes
        ],
        "unread": db.scalar(
            select(func.count(Notification.id)).where(Notification.status == "pending")
        ),
    }


@router.post("/notifications/dispatch")
def dispatch_notifications(db: Session = Depends(get_db)):
    return notification_service.dispatch_pending(db)


@router.post("/notifications/digest")
def create_digest(db: Session = Depends(get_db)):
    result = notification_service.build_daily_digest(db)
    notification_service.dispatch_pending(db)
    return result


# ---------------------------------------------------------------------------
# 设置
# ---------------------------------------------------------------------------
@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    rows = db.scalars(select(SystemSetting).where(SystemSetting.key.notlike("llm_cache:%"))).all()
    items = {}
    for row in rows:
        if isinstance(row.value, dict) and set(row.value.keys()) == {"value"}:
            items[row.key] = row.value["value"]
        else:
            items[row.key] = row.value
    portfolios = db.scalars(select(Portfolio)).all()
    return {
        "settings": items,
        "meta": {
            "llm": {
                "enabled": bool(settings.LLM_ENABLED),
                "base_url": settings.LLM_BASE_URL,
                "cheap_model": settings.LLM_MODEL_CHEAP,
                "strong_model": settings.LLM_MODEL_STRONG,
                "api_key_configured": bool(settings.LLM_API_KEY),
                "daily_budget": settings.LLM_DAILY_BUDGET_USD,
                "monthly_budget": settings.LLM_MONTHLY_BUDGET_USD,
            },
            "data_sources": {
                "fred_configured": bool(settings.FRED_API_KEY),
                "tushare_configured": bool(settings.TUSHARE_TOKEN),
                "gdelt_enabled": bool(settings.GDELT_ENABLED),
            },
            "notifications": {
                "webhook_configured": bool(settings.WEBHOOK_URL),
                "email_configured": bool(settings.SMTP_HOST),
            },
            "scheduler": {"enabled": bool(settings.SCHEDULER_ENABLED), "timezone": settings.TIMEZONE},
            "portfolios": [{"id": p.id, "name": p.name} for p in portfolios],
            "config_version": config_version(),
        },
    }


@router.put("/settings")
def update_settings(payload: SettingsIn, db: Session = Depends(get_db)):
    updates = payload.model_dump(exclude_none=True)
    threshold = updates.pop("notification_threshold", None)
    if threshold is not None:
        current = db.get(SystemSetting, "notifications")
        value = dict(current.value) if current and isinstance(current.value, dict) else {}
        value["min_importance"] = threshold
        if current is None:
            db.add(SystemSetting(key="notifications", value=value, description="通知阈值与渠道"))
        else:
            current.value = value
    for key, value in updates.items():
        row = db.get(SystemSetting, key)
        if row is None:
            db.add(SystemSetting(key=key, value=value, description=key))
        else:
            row.value = value
    audit.log(db, "settings.update", "system", {"keys": list(updates.keys()), "threshold": threshold})
    db.commit()
    return get_settings(db)


@router.post("/notifications/test")
def test_notification(payload: dict | None = None):
    """发送一条测试消息。未配置 webhook 时明确返回未配置，不假装成功。"""
    return notification_service.send_test_message((payload or {}).get("text", ""))


@router.post("/ops/backup")
def trigger_backup(db: Session = Depends(get_db)):
    result = backup_service.run_backup(keep=7)
    audit.log(db, "ops.backup", "sqlite", {"ok": result.get("ok")})
    db.commit()
    return result


@router.get("/ops/backups")
def backups():
    return {"items": backup_service.list_backups()}
