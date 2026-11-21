"""通知适配层：站内消息 + Webhook（+ 预留邮件）。

同一事件只通知一次（dedupe_key），避免同一事件被多家媒体转载导致轰炸。
"""
from __future__ import annotations

import json
from datetime import timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.timeutil import now_cn
from app.models.entities import Event, EventImpact, Notification, SystemSetting
from app.services import config_service


def _threshold(db: Session) -> float:
    custom = db.get(SystemSetting, "notifications")
    if custom and isinstance(custom.value, dict):
        value = custom.value.get("min_importance")
        if value is not None:
            return float(value)
    return float(config_service.signal_rules().get("event_scoring", {}).get("min_importance_to_notify", 72))


def _impact_lines(db: Session, event: Event) -> str:
    impacts = db.scalars(select(EventImpact).where(EventImpact.event_id == event.id).limit(5)).all()
    if not impacts:
        return "暂未识别明确受影响资产"
    label = {"bullish": "偏多", "bearish": "偏空", "mixed": "存在分歧", "unknown": "方向未知"}
    return "；".join(f"{i.instrument_symbol}（{label.get(i.direction, '未知')}）" for i in impacts)


def create_for_event(db: Session, event: Event) -> Notification | None:
    threshold = _threshold(db)
    if (event.importance or 0) < threshold:
        return None
    dedupe = f"event:{event.id}:{(event.published_at or now_cn()).strftime('%Y%m%d')}"
    exists = db.scalar(select(Notification).where(Notification.dedupe_key == dedupe))
    if exists:
        return exists
    level = "critical" if (event.importance or 0) >= 85 else "warning"
    body = (
        f"类别：{event.category}\n"
        f"方向：{event.direction}｜重要性 {event.importance:.0f}｜证据置信度 {event.confidence:.2f}\n"
        f"受影响资产：{_impact_lines(db, event)}\n"
        f"核心事实：{event.what_happened or event.summary}\n"
        f"不确定性：{event.what_uncertain}\n"
        f"分析引擎：{event.analysis_engine}\n"
        "置信度仅表示证据质量，不代表涨跌概率。"
    )
    notification = Notification(
        channel="inapp",
        level=level,
        title=event.title[:200],
        body=body,
        event_id=event.id,
        dedupe_key=dedupe,
        status="pending",
    )
    db.add(notification)
    db.commit()
    return notification


def sync_recent_notifications(db: Session, hours: int = 48) -> dict:
    """为近期达到阈值但尚未通知的事件补建站内消息（幂等，供手动采集后调用）。"""
    since = now_cn() - timedelta(hours=hours)
    events = db.scalars(
        select(Event).where(Event.published_at >= since).order_by(Event.importance.desc())
    ).all()
    threshold = _threshold(db)
    created = 0
    for event in events:
        if (event.importance or 0) < threshold:
            continue
        dedupe = f"event:{event.id}:{(event.published_at or now_cn()).strftime('%Y%m%d')}"
        if db.scalar(select(Notification).where(Notification.dedupe_key == dedupe)):
            continue
        if create_for_event(db, event):
            created += 1
    return {"created": created, "scanned": len(events)}


def _webhook_payload(note: Notification) -> dict:
    """企业微信群机器人要求 {"msgtype":"text","text":{"content":...}}，
    直接发自定义字段会返回 400 且不报错，必须按目标平台适配。
    """
    text = (
        f"【{note.level}】{note.title}\n"
        f"{note.body}\n"
        "（系统研究提示，不构成投资建议）"
    )
    if settings.WEBHOOK_URL and "qyapi.weixin.qq.com" in settings.WEBHOOK_URL:
        return {"msgtype": "text", "text": {"content": text}}
    return {
        "title": note.title,
        "body": note.body,
        "level": note.level,
        "event_id": note.event_id,
        "created_at": note.created_at.isoformat(sep=" ", timespec="seconds"),
        "disclaimer": "系统研究提示，不构成投资建议",
    }


def send_test_message(text: str = "") -> dict:
    """发一条测试消息。未配置时明确说明，不假装成功。"""
    if not settings.WEBHOOK_URL:
        return {
            "ok": False,
            "configured": False,
            "message": "未配置 WEBHOOK_URL，消息只会在站内可见",
        }
    body = text or f"投资情报工作台连通性测试 · {now_cn().isoformat(sep=' ', timespec='seconds')}"
    note = Notification(
        level="info",
        title="通知连通性测试",
        body=body,
        status="pending",
        dedupe_key=f"test:{now_cn().timestamp()}",
    )
    payload = _webhook_payload(note)
    try:
        resp = httpx.post(settings.WEBHOOK_URL, json=payload, timeout=10)
        resp.raise_for_status()
        # 企业微信即使返回 200，也可能在 body 里给 errcode
        detail = ""
        try:
            data = resp.json()
            if isinstance(data, dict) and data.get("errcode") not in (None, 0):
                raise RuntimeError(f"errcode={data.get('errcode')} {data.get('errmsg')}")
            detail = str(data)[:200]
        except ValueError:
            detail = resp.text[:200]
        return {"ok": True, "configured": True, "status": resp.status_code, "detail": detail}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "configured": True, "message": str(exc)[:300]}


def dispatch_pending(db: Session, limit: int = 20) -> dict:
    pending = db.scalars(
        select(Notification).where(Notification.status == "pending").order_by(Notification.created_at).limit(limit)
    ).all()
    sent = failed = 0
    for note in pending:
        if not settings.WEBHOOK_URL:
            note.status = "sent"
            note.sent_at = now_cn()
            note.error = ""
            sent += 1
            continue
        try:
            resp = httpx.post(settings.WEBHOOK_URL, json=_webhook_payload(note), timeout=10)
            resp.raise_for_status()
            note.status = "sent"
            note.sent_at = now_cn()
            sent += 1
        except Exception as exc:  # noqa: BLE001
            note.status = "failed"
            note.error = str(exc)[:300]
            failed += 1
    db.commit()
    return {"sent": sent, "failed": failed, "webhook_configured": bool(settings.WEBHOOK_URL)}


def build_daily_digest(db: Session) -> dict:
    since = now_cn() - timedelta(hours=24)
    events = db.scalars(
        select(Event).where(Event.published_at >= since).order_by(Event.importance.desc()).limit(12)
    ).all()
    from app.models.entities import AssetSignal

    as_of = db.scalar(select(AssetSignal.as_of).order_by(AssetSignal.as_of.desc()))
    signals = []
    if as_of:
        signals = db.scalars(
            select(AssetSignal)
            .where(AssetSignal.as_of == as_of, AssetSignal.total_score.is_not(None))
            .order_by(AssetSignal.total_score.desc())
            .limit(5)
        ).all()
    lines = [f"# 每日研究简报 {now_cn().strftime('%Y-%m-%d')}", ""]
    lines.append(f"近 24 小时重要事件 {len(events)} 条。")
    for event in events[:6]:
        lines.append(f"- [{event.category}] {event.title}（重要性 {event.importance:.0f}，方向 {event.direction}）")
    if signals:
        lines.append("")
        lines.append("评分靠前的资产（同类内相对比较，不代表收益预期）：")
        for signal in signals:
            lines.append(f"- {signal.symbol}: {signal.total_score:.1f}（{signal.action}）")
    lines.append("")
    lines.append("本简报由规则与已入库数据生成，不构成投资建议；AI 未配置时结论仅基于规则引擎。")
    content = "\n".join(lines)
    dedupe = f"digest:{now_cn().strftime('%Y%m%d')}"
    exists = db.scalar(select(Notification).where(Notification.dedupe_key == dedupe))
    if exists:
        return {"created": False, "id": exists.id, "content": content}
    note = Notification(
        channel="digest",
        level="info",
        title=f"每日研究简报 {now_cn().strftime('%Y-%m-%d')}",
        body=content,
        dedupe_key=dedupe,
        status="pending",
    )
    db.add(note)
    db.commit()
    return {"created": True, "id": note.id, "content": content}
