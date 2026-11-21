"""新闻去重、事件聚类与分类规则测试（不依赖网络）。"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.core.timeutil import now_cn
from app.models.entities import Event, RawDocument
from app.providers.base import NewsItem
from app.services.event_engine import analyze_pending_events, classify_text, cluster_documents
from app.services.news_pipeline import content_hash, normalize_title, store_documents, title_hash


def _item(ext_id: str, title: str, summary: str = "", media: str = "测试媒体", published=None):
    return NewsItem(
        source_code="test_news",
        external_id=ext_id,
        title=title,
        summary=summary,
        url=f"https://example.com/{ext_id}",
        media=media,
        published_at=published or now_cn(),
        reliability="medium",
    )


def test_normalize_and_hash_are_stable():
    assert normalize_title("【快讯】央行 宣布降准！") == "央行降准"
    assert title_hash("【快讯】央行宣布降准") == title_hash("央行宣布降准！")
    assert content_hash("标题", "摘要") != content_hash("标题", "另一个摘要")


def test_store_documents_is_idempotent(db):
    before = db.scalar(select(func.count(RawDocument.id))) or 0
    items = [_item("dedupe-1", "测试去重新闻标题一", "摘要内容")]
    first = store_documents(db, items)
    db.commit()
    second = store_documents(db, items)
    db.commit()
    after = db.scalar(select(func.count(RawDocument.id))) or 0
    assert first["created"] == 1
    assert second["created"] == 0
    assert second["duplicate"] == 1
    assert after - before == 1


def test_classify_monetary_policy():
    category, hits = classify_text("央行宣布降准 0.5 个百分点，释放流动性")
    assert category["code"] == "monetary_policy"
    assert hits >= 1


def test_classify_unknown_falls_back_to_other():
    category, hits = classify_text("某地举办了一场与市场无关的活动")
    assert category["code"] == "other"
    assert hits == 0


def test_cluster_similar_titles(db):
    now = now_cn()
    docs = [
        RawDocument(
            source_code="test_news", external_id="c1", title="央行宣布全面降准0.5个百分点",
            published_at=now, fetched_at=now, content_hash="h1", title_hash="t1",
        ),
        RawDocument(
            source_code="test_news", external_id="c2", title="央行宣布全面降准0.5个百分点 释放长期资金",
            published_at=now, fetched_at=now, content_hash="h2", title_hash="t2",
        ),
        RawDocument(
            source_code="test_news", external_id="c3", title="某公司发布新款手机产品",
            published_at=now, fetched_at=now, content_hash="h3", title_hash="t3",
        ),
    ]
    clusters = cluster_documents(docs)
    assert len(clusters) == 2


def test_event_pipeline_clusters_and_persists(db):
    now = now_cn() - timedelta(hours=1)
    items = [
        _item("evt-a1", "美联储宣布降息25个基点，市场风险偏好上升", "美联储议息会议决定降息。", "媒体A", now),
        _item("evt-a2", "美联储宣布降息25个基点 暗示年内仍有空间", "政策声明偏鸽。", "媒体B", now),
        _item("evt-a3", "美联储降息25个基点，美元指数走弱", "美元与黄金同步反应。", "媒体C", now),
    ]
    store_documents(db, items)
    db.commit()
    result = analyze_pending_events(db)
    assert result["clusters"] >= 1
    created = db.scalars(
        select(Event).where(Event.title.like("%美联储%降息%")).order_by(Event.id.desc())
    ).all()
    assert created, "应生成降息相关事件"
    event = created[0]
    assert event.source_count >= 2
    assert event.category == "monetary_policy"
    assert event.importance > 40
    assert event.analysis_version >= 1
    assert event.analysis.get("engine_note")

    # 再次运行不应重复创建同一事件
    before = db.scalar(select(func.count(Event.id))) or 0
    again = analyze_pending_events(db)
    after = db.scalar(select(func.count(Event.id))) or 0
    assert again["events_created"] == 0
    assert after == before
