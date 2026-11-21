"""新闻规范化、去重与入库。

安全说明：新闻标题/摘要/正文一律视为不可信外部数据，只做文本处理与
关键字统计，不解析其中的指令，不据此改变系统规则或读取任何配置。
"""
from __future__ import annotations

import hashlib
import html
import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import now_cn
from app.models.entities import RawDocument
from app.providers.base import NewsItem

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\u4e00-\u9fff]+")
_TAG = re.compile(r"<[^>]+>")
# 聚类用：去掉不承载事实的报道性动词，降低同一事件的标题差异
_FILLER = re.compile(r"(宣布|表示|称|显示|据悉|消息称|记者)")


def clean_text(value: str, limit: int = 1500) -> str:
    text = html.unescape(_TAG.sub(" ", value or ""))
    text = _WS.sub(" ", text).strip()
    return text[:limit]


def normalize_title(title: str) -> str:
    """去掉媒体前缀/栏目前缀与标点，用于相似度与去重。"""
    text = clean_text(title, 300)
    text = re.sub(r"^[【\[][^】\]]{1,12}[】\]]", "", text).strip()
    text = re.sub(r"^(快讯|独家|重磅|突发)[:：\s]*", "", text)
    text = _FILLER.sub("", text)
    return _PUNCT.sub("", text).lower()


def content_hash(title: str, summary: str = "") -> str:
    return hashlib.sha256(f"{normalize_title(title)}|{clean_text(summary, 300)}".encode("utf-8")).hexdigest()


def title_hash(title: str) -> str:
    return hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()


def store_documents(db: Session, items: list[NewsItem]) -> dict:
    created = duplicate = skipped = 0
    since = now_cn() - timedelta(days=7)
    for item in items:
        title = clean_text(item.title, 500)
        if not title:
            skipped += 1
            continue
        summary = clean_text(item.summary, 1200)
        chash = content_hash(title, summary)
        thash = title_hash(title)

        exists = db.scalar(
            select(RawDocument).where(
                RawDocument.source_code == item.source_code,
                RawDocument.external_id == item.external_id,
            )
        )
        if exists:
            duplicate += 1
            continue
        # 同源近 7 天内标题完全相同的新闻视为重复转载
        same_title = db.scalar(
            select(RawDocument).where(
                RawDocument.title_hash == thash,
                RawDocument.fetched_at >= since,
            )
        )
        if same_title:
            duplicate += 1
            continue
        db.add(
            RawDocument(
                source_code=item.source_code,
                external_id=item.external_id,
                title=title,
                summary=summary,
                url=item.url,
                media=item.media,
                language=item.language,
                published_at=item.published_at,
                fetched_at=now_cn(),
                content_hash=chash,
                title_hash=thash,
                reliability=item.reliability,
                status="new",
                raw_payload=item.raw or {},
            )
        )
        created += 1
    db.flush()
    return {"created": created, "duplicate": duplicate, "skipped": skipped, "total": len(items)}
