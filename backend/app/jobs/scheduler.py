"""后台定时任务：采集 → 事件 → 信号 → 配置 → 简报 → 健康检查。

- 每个任务独立失败，不影响其他任务。
- 同一任务 max_instances=1 + coalesce，避免重复执行。
- 任务记录写入 job_runs，可在“数据源管理/系统设置”查看。
"""
from __future__ import annotations

import logging
import threading
from datetime import date
from typing import Callable

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.ai.event_analyst import budget_status, run_ai_batch
from app.core.config import settings
from app.core.db import session_scope
from app.core.timeutil import now_cn
from app.models.entities import DataSource, EventImpact, JobRun, Notification, Portfolio
from app.services import collector_service, config_service, notification_service
from app.services.allocation_service import compute_allocation
from app.services.event_engine import analyze_pending_events
from app.services.scoring_service import compute_all_signals
from app.services.universe_service import refresh_universe

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None
_start_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 任务实现
# ---------------------------------------------------------------------------
def job_news_collect(db) -> dict:
    result = collector_service.run_news(db, trigger="schedule")
    analysis = analyze_pending_events(db)
    notes = 0
    from app.models.entities import Event

    for event_id in analysis.get("event_ids", []):
        event = db.get(Event, event_id)
        if event and notification_service.create_for_event(db, event):
            notes += 1
    notification_service.dispatch_pending(db)
    return {"news": result.message, **{k: v for k, v in analysis.items() if k != "event_ids"}, "notifications": notes}


def job_market_quotes(db) -> dict:
    result = collector_service.run_market_quotes(db, trigger="schedule")
    return {"message": result.message, "records": result.records}


def job_market_history(db) -> dict:
    result = collector_service.run_market_history(db, trigger="schedule")
    return {"message": result.message, "records": result.records}


def job_macro_refresh(db) -> dict:
    result = collector_service.run_macro(db, trigger="schedule")
    return {"message": result.message, "records": result.records, "detail": result.detail}


def job_event_analysis(db) -> dict:
    analysis = analyze_pending_events(db)
    notes = notification_service.sync_recent_notifications(db)
    notification_service.dispatch_pending(db)
    return {**{k: v for k, v in analysis.items() if k != "event_ids"}, "notifications": notes}


def job_universe_refresh(db) -> dict:
    """每交易日收盘后刷新 ETF 候选池（全量登记 + 门槛 + 同指数去重）。"""
    return refresh_universe(db)


def job_signal_update(db) -> dict:
    return compute_all_signals(db)


def job_allocation_update(db) -> dict:
    portfolio = db.scalar(select(Portfolio).order_by(Portfolio.is_default.desc(), Portfolio.id))
    if not portfolio:
        return {"skipped": "无组合"}
    result = compute_allocation(db, portfolio, persist=True)
    return {"targets": len(result.get("targets", [])), "regime": result.get("regime")}


def job_ai_analysis(db) -> dict:
    return run_ai_batch(db)


def job_daily_digest(db) -> dict:
    result = notification_service.build_daily_digest(db)
    notification_service.dispatch_pending(db)
    return {"created": result["created"], "id": result["id"]}


def job_health_check(db) -> dict:
    """检查数据源新鲜度与最近失败，写回状态说明，不伪造健康状态。"""
    alerts = []
    for source in db.scalars(select(DataSource)).all():
        if not source.enabled:
            source.status = "disabled"
            continue
        if source.requires_key and not (
            settings.FRED_API_KEY if source.code == "fred_macro" else settings.TUSHARE_TOKEN
        ):
            source.status = "unconfigured"
            continue
        if source.last_run_at and source.interval_minutes:
            stale_minutes = (now_cn() - source.last_run_at).total_seconds() / 60
            if stale_minutes > source.interval_minutes * 3 and source.status == "healthy":
                source.status = "degraded"
                source.status_message = f"超过 {stale_minutes:.0f} 分钟未成功更新"
                alerts.append(source.code)
    db.commit()
    if alerts:
        key = f"health:{now_cn().strftime('%Y%m%d%H')}"
        if not db.scalar(select(Notification).where(Notification.dedupe_key == key)):
            db.add(
                Notification(
                    channel="inapp",
                    level="warning",
                    title="数据源更新延迟",
                    body="以下数据源超过预期间隔未更新：" + "、".join(alerts),
                    dedupe_key=key,
                    status="pending",
                )
            )
            db.commit()
    # 顺带把任务失败与数据源异常汇总成告警，否则这些信息只躺在列表里没人看
    from app.services import backup_service

    pushed = backup_service.push_alerts(db)
    notification_service.dispatch_pending(db)
    return {"alerts": alerts, "notifications": pushed}


def job_etf_quality(db) -> dict:
    """候选池日线与质量分。原本只有手动接口，导致雷达拿着过期日线算分。"""
    from app.services.etf_history_service import refresh_quality

    return refresh_quality(db)


def job_radar_snapshot(db) -> dict:
    """写入当日雷达快照。不跑的话「首次发现/环比」会断档。"""
    from app.services.opportunity_radar_service import build_daily

    result = build_daily(db)
    return {
        "message": result["message"],
        "items": len(result["items"]),
        "qualified": result.get("qualified"),
    }


def job_daily_backup(db) -> dict:
    """每日备份数据库并清理超期文件。"""
    from app.services import backup_service

    return backup_service.run_backup(keep=7)


def job_cost_stats(db) -> dict:
    status = budget_status(db)
    if status["daily_limit"] and status["today_usd"] > status["daily_limit"] * 0.8:
        key = f"budget:{now_cn().strftime('%Y%m%d')}"
        if not db.scalar(select(Notification).where(Notification.dedupe_key == key)):
            db.add(
                Notification(
                    channel="inapp",
                    level="warning",
                    title="模型预算接近上限",
                    body=f"今日已用 ${status['today_usd']:.4f} / ${status['daily_limit']:.2f}，"
                    "系统将自动降低非必要 AI 调用频率。",
                    dedupe_key=key,
                    status="pending",
                )
            )
            db.commit()
    return status


JOBS: dict[str, dict] = {
    "news_collect": {
        "name": "新闻采集与事件聚类",
        "func": job_news_collect,
        "trigger": lambda: IntervalTrigger(minutes=30),
        "description": "每 30 分钟采集财经新闻、去重、聚类并生成事件",
    },
    "market_quotes": {
        "name": "行情快照刷新",
        "func": job_market_quotes,
        "trigger": lambda: IntervalTrigger(minutes=10),
        "description": "每 10 分钟刷新可获得的行情快照（收盘后标记为 close）",
    },
    "market_history": {
        "name": "日线历史补全",
        "func": job_market_history,
        "trigger": lambda: CronTrigger(day_of_week="mon-fri", hour=16, minute=20),
        "description": "交易日 16:20 更新前复权日线，用于趋势与回测",
    },
    "macro_refresh": {
        "name": "宏观数据刷新",
        "func": job_macro_refresh,
        "trigger": lambda: CronTrigger(hour=8, minute=40),
        "description": "每日 08:40 更新世界银行（及已配置的 FRED）宏观数据",
    },
    "event_analysis": {
        "name": "候选事件分析",
        "func": job_event_analysis,
        "trigger": lambda: IntervalTrigger(minutes=45),
        "description": "每 45 分钟对新入库文档做规则分析（AI 可选）",
    },
    "ai_analysis": {
        "name": "高重要性事件 AI 分析",
        "func": job_ai_analysis,
        "trigger": lambda: IntervalTrigger(minutes=120),
        "description": "每 2 小时对满足升级条件的事件调用模型；未配置密钥时跳过",
    },
    "universe_refresh": {
        "name": "ETF 候选池刷新",
        "func": job_universe_refresh,
        "trigger": lambda: CronTrigger(day_of_week="mon-fri", hour=16, minute=15),
        "description": "交易日 16:15 刷新 ETF 全量目录、分类、准入门槛与同指数去重",
    },
    "signal_update": {
        "name": "资产评分更新",
        "func": job_signal_update,
        "trigger": lambda: CronTrigger(day_of_week="mon-fri", hour=16, minute=50),
        "description": "交易日 16:50 计算资产评分与市场风险状态",
    },
    "allocation_update": {
        "name": "目标权重计算",
        "func": job_allocation_update,
        "trigger": lambda: CronTrigger(day_of_week="mon-fri", hour=17, minute=10),
        "description": "交易日 17:10 计算战术目标权重与风险分析",
    },
    "daily_digest": {
        "name": "每日研究简报",
        "func": job_daily_digest,
        "trigger": lambda: CronTrigger(hour=8, minute=30),
        "description": "每日 08:30 基于数据库新增事件与信号生成简报",
    },
    "health_check": {
        "name": "数据健康检查",
        "func": job_health_check,
        "trigger": lambda: IntervalTrigger(hours=1),
        "description": "每小时检查数据源新鲜度与失败情况",
    },
    "etf_quality": {
        "name": "候选池日线与质量分",
        "func": job_etf_quality,
        "trigger": lambda: CronTrigger(day_of_week="mon-fri", hour=16, minute=35),
        "description": "交易日 16:35 补候选池日线、取净值、重算质量分（候选池刷新后 20 分钟）",
    },
    "radar_snapshot": {
        "name": "雷达快照",
        "func": job_radar_snapshot,
        "trigger": lambda: CronTrigger(day_of_week="mon-fri", hour=16, minute=45),
        "description": "交易日 16:45 生成当日机会快照，供环比与首次发现时间使用",
    },
    "daily_backup": {
        "name": "数据库备份",
        "func": job_daily_backup,
        "trigger": lambda: CronTrigger(hour=2, minute=30),
        "description": "每日 02:30 备份 SQLite（sqlite backup API），保留最近 7 份",
    },
    "cost_stats": {
        "name": "Token 与成本统计",
        "func": job_cost_stats,
        "trigger": lambda: CronTrigger(hour="9,15,21", minute=5),
        "description": "每日三次统计模型调用成本并预警",
    },
}


def run_job(job_id: str, trigger: str = "manual") -> dict:
    definition = JOBS.get(job_id)
    if not definition:
        return {"ok": False, "error": f"未知任务 {job_id}"}
    with session_scope() as db:
        record = JobRun(job_id=job_id, trigger=trigger, status="running", started_at=now_cn())
        db.add(record)
        db.commit()
        try:
            detail = definition["func"](db) or {}
            record.status = "success"
            record.detail = detail if isinstance(detail, dict) else {"result": detail}
            record.records = int(detail.get("records", 0) or detail.get("computed", 0) or 0) if isinstance(detail, dict) else 0
            record.error = ""
        except Exception as exc:  # noqa: BLE001
            logger.exception("任务 %s 失败", job_id)
            record.status = "failed"
            record.error = str(exc)[:1000]
            detail = {"error": str(exc)[:500]}
        record.finished_at = now_cn()
        return {"ok": record.status == "success", "job_id": job_id, "status": record.status, "detail": detail}


# ---------------------------------------------------------------------------
# 调度器
# ---------------------------------------------------------------------------
def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    if not settings.SCHEDULER_ENABLED:
        logger.info("调度器已通过 SCHEDULER_ENABLED=0 关闭")
        return None
    with _start_lock:
        if _scheduler and _scheduler.running:
            return _scheduler
        scheduler = BackgroundScheduler(
            timezone=settings.TIMEZONE,
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
        )
        for job_id, definition in JOBS.items():
            scheduler.add_job(
                _guarded,
                trigger=definition["trigger"](),
                id=job_id,
                name=definition["name"],
                args=[job_id],
                replace_existing=True,
            )
        scheduler.start()
        _scheduler = scheduler
        logger.info("调度器已启动，共 %s 个任务", len(JOBS))
        return scheduler


def _guarded(job_id: str) -> None:
    # 进程内二次保护：同一任务不并发执行
    lock = _LOCKS.setdefault(job_id, threading.Lock())
    if not lock.acquire(blocking=False):
        logger.warning("任务 %s 正在运行，跳过本次调度", job_id)
        return
    try:
        run_job(job_id, trigger="schedule")
    finally:
        lock.release()


_LOCKS: dict[str, threading.Lock] = {}


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
    _scheduler = None


def scheduler_status() -> dict:
    if not _scheduler or not _scheduler.running:
        return {"running": False, "jobs": [{"id": k, "name": v["name"], "description": v["description"]} for k, v in JOBS.items()]}
    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append(
            {
                "id": job.id,
                "name": job.name,
                "description": JOBS.get(job.id, {}).get("description", ""),
                "next_run_at": job.next_run_time.isoformat(sep=" ", timespec="seconds") if job.next_run_time else None,
                "trigger": str(job.trigger),
            }
        )
    return {"running": True, "timezone": settings.TIMEZONE, "jobs": jobs}
