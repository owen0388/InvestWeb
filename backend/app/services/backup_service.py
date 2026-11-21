"""数据库备份与运维告警。

备份用 sqlite3 的 backup API，而不是直接复制文件：
数据库开了 WAL，直接 cp 可能拿到「主库已提交但 WAL 未合并」的不一致快照。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import DATA_DIR, settings
from app.core.timeutil import now_cn
from app.models.entities import DataSource, JobRun, Notification


def _db_path() -> Path:
    url = settings.DATABASE_URL
    if not url.startswith("sqlite"):
        return Path("")
    raw = url.split("sqlite:///", 1)[-1]
    path = Path(raw)
    return path if path.is_absolute() else Path.cwd() / path


def run_backup(keep: int = 7) -> dict:
    source_path = _db_path()
    if not source_path or not source_path.exists():
        return {"ok": False, "message": "当前数据库不是本地 SQLite，或文件不存在"}

    target_dir = DATA_DIR / "backups"
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = now_cn().strftime("%Y%m%d-%H%M%S")
    target = target_dir / f"invest-{stamp}.db"

    try:
        with sqlite3.connect(source_path) as src, sqlite3.connect(target) as dst:
            src.backup(dst)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "message": f"备份失败：{exc}"}

    size_mb = round(target.stat().st_size / 1024 / 1024, 2)
    removed = _prune(target_dir, keep)
    return {
        "ok": True,
        "path": str(target),
        "size_mb": size_mb,
        "removed": removed,
        "message": f"已备份 {target.name}（{size_mb} MB），清理旧备份 {removed} 个",
    }


def _prune(target_dir: Path, keep: int) -> int:
    files = sorted(target_dir.glob("invest-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    removed = 0
    for old in files[max(keep, 1):]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def list_backups(limit: int = 20) -> list[dict]:
    target_dir = DATA_DIR / "backups"
    if not target_dir.exists():
        return []
    files = sorted(target_dir.glob("invest-*.db"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for path in files[:limit]:
        stat = path.stat()
        out.append(
            {
                "name": path.name,
                "size_mb": round(stat.st_size / 1024 / 1024, 2),
                "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat(sep=" ", timespec="seconds"),
            }
        )
    return out


def collect_alerts(db: Session, hours: int = 24) -> list[dict]:
    """检查需要告警的异常。只报告有证据的问题，不猜。"""
    alerts: list[dict] = []
    since = now_cn() - timedelta(hours=hours)

    failed_jobs = db.scalars(
        select(JobRun).where(JobRun.started_at >= since, JobRun.status == "failed")
    ).all()
    if failed_jobs:
        names = sorted({run.job_name for run in failed_jobs})
        alerts.append(
            {
                "level": "warning",
                "title": f"定时任务失败 {len(failed_jobs)} 次",
                "body": "涉及任务：" + "、".join(names[:5]),
                "dedupe": f"job_fail:{since.date()}",
            }
        )

    broken_sources = db.scalars(
        select(DataSource).where(DataSource.status.in_(["error", "degraded"]))
    ).all()
    if broken_sources:
        alerts.append(
            {
                "level": "warning",
                "title": f"{len(broken_sources)} 个数据源异常",
                "body": "、".join(f"{s.code}({s.status})" for s in broken_sources[:5]),
                "dedupe": f"source_bad:{since.date()}",
            }
        )
    return alerts


def push_alerts(db: Session) -> dict:
    """把告警写入站内通知（去重），由 dispatch_pending 负责推送。"""
    created = 0
    for alert in collect_alerts(db):
        exists = db.scalar(
            select(Notification).where(Notification.dedupe_key == alert["dedupe"])
        )
        if exists:
            continue
        db.add(
            Notification(
                level=alert["level"],
                title=alert["title"],
                body=alert["body"],
                status="pending",
                dedupe_key=alert["dedupe"],
            )
        )
        created += 1
    db.commit()
    return {"created": created}
