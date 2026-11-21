"""事件引擎：新闻聚类 → 分类 → 重要性/置信度 → 证据与传导路径 → 事件入库。

这里全部是可解释的规则计算（可测试），不冒充 AI 结论。
配置了模型密钥后，AI 层可对高重要性事件追加结构化分析，但基础字段仍由
本模块产出，保证没有密钥时系统功能不缺失。
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import timedelta
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import now_cn
from app.models.entities import (
    Event,
    EventAnalysisVersion,
    EventEvidence,
    EventImpact,
    Instrument,
    RawDocument,
)
from app.services import config_service
from app.services.news_pipeline import normalize_title

# 类别 → 影响期限
HORIZON = {
    "monetary_policy": "mid",
    "fiscal_policy": "mid",
    "economic_data": "short",
    "liquidity": "short",
    "credit_risk": "mid",
    "geopolitics": "short",
    "military": "short",
    "energy_supply": "short",
    "trade_tariff": "mid",
    "technology": "long",
    "company": "short",
    "regulation": "mid",
    "disaster": "short",
    "other": "unknown",
}

# 避险类资产：在风险偏好下降的地缘/军事/贸易事件中方向相反
HAVEN_CLASSES = {"GOLD", "BOND", "CASH"}
RISK_OFF_CATEGORIES = {"geopolitics", "military", "trade_tariff", "credit_risk", "disaster"}

EASING_WORDS = ["降息", "降准", "宽松", "下调", "刺激", "扩表", "注入流动性"]
TIGHTENING_WORDS = ["加息", "收紧", "上调", "缩表", "紧缩", "抑制"]

NEGATIVE_WORDS = ["下跌", "暴跌", "下滑", "减少", "亏损", "违约", "制裁", "冲突", "升级", "风险", "警告"]
POSITIVE_WORDS = ["上涨", "增长", "超预期", "改善", "回暖", "利好", "突破", "扩张", "订单"]


def _rules() -> dict:
    return config_service.signal_rules()


def _categories() -> list[dict]:
    return _rules().get("event_categories", [])


def classify_text(text: str) -> tuple[dict, int]:
    """返回 (类别配置, 命中关键词数)。命中数为 0 时归入 other。"""
    lowered = text.lower()
    best = None
    best_hits = 0
    best_score = 0.0
    for cat in _categories():
        hits = sum(1 for kw in cat.get("keywords", []) if kw.lower() in lowered)
        if hits == 0:
            continue
        score = hits * 10 + float(cat.get("base_importance", 40)) * 0.3
        if score > best_score:
            best, best_hits, best_score = cat, hits, score
    if best is None:
        other = next((c for c in _categories() if c["code"] == "other"), None) or {
            "code": "other",
            "name": "其他市场重要事件",
            "base_importance": 40,
            "bias": "mixed",
            "asset_classes": [],
            "mechanism": "需结合具体内容判断",
        }
        return other, 0
    return best, best_hits


def _similarity(a: str, b: str) -> float:
    """标题相似度：编辑距离 + 包含关系（中文标题常见“核心事实+不同后缀”）。"""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) >= 6 and len(b) >= 6:
        shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
        if shorter in longer and len(shorter) / len(longer) >= 0.55:
            return 0.9
        prefix = 0
        for x, y in zip(a, b):
            if x != y:
                break
            prefix += 1
        if prefix >= 10 and prefix / len(shorter) >= 0.5:
            return 0.85
    return SequenceMatcher(None, a, b).ratio()


def cluster_documents(docs: list[RawDocument]) -> list[list[RawDocument]]:
    threshold = float(_rules().get("event_scoring", {}).get("cluster_similarity_threshold", 0.62))
    window = float(_rules().get("event_scoring", {}).get("cluster_window_hours", 48))
    clusters: list[list[RawDocument]] = []
    seeds: list[tuple[str, "object"]] = []  # (normalized title, published_at)
    for doc in sorted(docs, key=lambda d: d.published_at or d.fetched_at):
        norm = normalize_title(doc.title)
        placed = False
        for idx, (seed_norm, seed_time) in enumerate(seeds):
            ref = doc.published_at or doc.fetched_at
            if seed_time and ref and abs((ref - seed_time).total_seconds()) > window * 3600:
                continue
            if _similarity(norm, seed_norm) >= threshold:
                clusters[idx].append(doc)
                placed = True
                break
        if not placed:
            clusters.append([doc])
            seeds.append((norm, doc.published_at or doc.fetched_at))
    return clusters


def _direction(text: str, category_bias: str, class_code: str, category_code: str) -> str:
    lowered = text.lower()
    neg = sum(1 for w in NEGATIVE_WORDS if w in lowered)
    pos = sum(1 for w in POSITIVE_WORDS if w in lowered)
    base = category_bias
    if category_code == "monetary_policy":
        easing = sum(1 for w in EASING_WORDS if w in text)
        tightening = sum(1 for w in TIGHTENING_WORDS if w in text)
        if easing > tightening:
            base = "bullish"
        elif tightening > easing:
            base = "bearish"
    if category_code in RISK_OFF_CATEGORIES and class_code.split("_")[0] in HAVEN_CLASSES:
        base = {"bearish": "bullish", "bullish": "bearish"}.get(base, base)
    if neg and pos and abs(neg - pos) <= 1:
        return "mixed"
    if neg > pos + 1:
        return "bearish"
    if pos > neg + 1:
        return "bullish"
    return base


def _importance(text: str, category: dict, source_count: int) -> float:
    cfg = _rules().get("event_scoring", {})
    priority = cfg.get("priority_keywords", {})
    bonus = sum(v for k, v in priority.items() if k in text)
    source_bonus = min(
        source_count * float(cfg.get("source_bonus_per_extra_source", 3)),
        float(cfg.get("max_source_bonus", 12)),
    )
    value = float(category.get("base_importance", 40)) + bonus + source_bonus
    return max(0.0, min(100.0, round(value, 1)))


def _confidence(docs: list[RawDocument], hits: int) -> float:
    medias = {d.media for d in docs if d.media}
    reliability_map = {"high": 0.12, "medium": 0.06, "low": 0.0}
    best_rel = max((reliability_map.get(d.reliability, 0.0) for d in docs), default=0.0)
    value = 0.22 + min(len(docs), 4) * 0.07 + min(len(medias), 3) * 0.06 + best_rel
    value += 0.06 if any(d.summary for d in docs) else 0
    value += 0.04 if any(d.published_at for d in docs) else 0
    value += min(hits, 3) * 0.02
    return round(min(value, 0.86), 3)


def _event_key(seed_title: str, published_at) -> str:
    day = (published_at or now_cn()).strftime("%Y%m%d")
    digest = hashlib.sha1(f"{normalize_title(seed_title)}|{day}".encode("utf-8")).hexdigest()[:16]
    return f"evt_{digest}"


def _find_duplicate_event(db: Session, seed_title: str, published_at) -> Event | None:
    since = (published_at or now_cn()) - timedelta(hours=48)
    candidates = db.scalars(select(Event).where(Event.published_at >= since)).all()
    norm = normalize_title(seed_title)
    for event in candidates:
        if _similarity(norm, normalize_title(event.title)) >= 0.72:
            return event
    return None


def _match_instruments(db: Session, text: str, category: dict) -> list[tuple[Instrument, float, str]]:
    """直接命中优先，其次按类别默认关联资产。返回 (品种, 强度, 依据)。"""
    lowered = text.lower()
    instruments = db.scalars(select(Instrument).where(Instrument.enabled.is_(True))).all()
    direct: list[tuple[Instrument, float, str]] = []
    for inst in instruments:
        score = 0.0
        reason = ""
        if inst.name and inst.name.lower() in lowered:
            score += 3.0
            reason = f"文中提及「{inst.name}」"
        tag_hits = [t for t in (inst.tags or []) if t and t.lower() in lowered]
        if tag_hits:
            score += 1.0 * len(tag_hits)
            reason = reason or f"行业/主题关键词：{'、'.join(tag_hits)}"
        if score > 0:
            direct.append((inst, score, reason))
    direct.sort(key=lambda item: item[1], reverse=True)
    selected = direct[:6]

    if len(selected) < 4:
        for class_code in category.get("asset_classes", []):
            candidates = [
                i
                for i in instruments
                if i.asset_class_code.startswith(class_code.split("_")[0])
                and i.id not in {s[0].id for s in selected}
                and i.instrument_type in ("index", "etf")
            ]
            for inst in candidates[:2]:
                selected.append(
                    (inst, 0.8, f"该类事件通常通过「{category.get('name')}」渠道影响{inst.name}")
                )
    return selected[:8]


def _analysis_text(category: dict, docs: list[RawDocument], importance: float, confidence: float) -> dict:
    medias = sorted({d.media for d in docs if d.media})
    return {
        "what_happened": f"{docs[0].title}（共 {len(docs)} 条来源，{len(medias)} 家媒体）",
        "what_confirmed": "、".join(medias[:4]) if medias else "单一来源，尚未交叉确认",
        "what_uncertain": (
            "目前仅有单一来源报道，规模、持续时间与细节仍需交叉确认。"
            if len(docs) < 2
            else "多源报道在核心事实上较为一致，但具体规模、执行力度和持续时间仍不确定。"
        ),
        "what_changed": (
            "该事件在近 48 小时内首次进入监测范围，属于新增信息。"
            if importance >= 60
            else "属于常规信息更新，相对市场既有认知的变化有限。"
        ),
        "mechanism": category.get("mechanism", "需结合具体内容判断"),
        "counter_evidence": (
            ["暂无明确反面证据；单一来源事件需等待更多独立来源确认。"]
            if len(docs) < 2
            else ["尚无公开的反向证据，但市场可能已部分计价，需观察价格与成交是否同步反应。"]
        ),
        "invalidation": (
            "若后续权威来源否认或修正核心事实、或价格与成交未出现与逻辑一致的反应，"
            "则当前判断失效。"
        ),
        "priced_in": "unknown",
        "engine_note": "规则引擎输出，用于初筛与研究排序，不构成买卖建议。",
        "confidence_basis": f"来源数 {len(docs)}，媒体数 {len(medias)}，置信度 {confidence:.2f} 仅为证据质量，不代表收益概率。",
    }


def analyze_pending_events(db: Session, trigger: str = "schedule", limit: int = 200) -> dict:
    rules = _rules().get("event_scoring", {})
    min_importance = float(rules.get("min_importance_to_analyze", 45))
    docs = db.scalars(
        select(RawDocument)
        .where(RawDocument.event_id.is_(None), RawDocument.status == "new")
        .order_by(RawDocument.published_at.desc())
        .limit(limit)
    ).all()
    if not docs:
        return {"clusters": 0, "events_created": 0, "events_updated": 0, "documents": 0}

    clusters = cluster_documents(docs)
    created = updated = skipped = 0
    touched_events: list[int] = []

    for cluster in clusters:
        cluster.sort(key=lambda d: d.published_at or d.fetched_at)
        # 代表标题：取信息量最大的（长度适中且包含类别关键词）
        texts = [f"{d.title} {d.summary}" for d in cluster]
        joined = " ".join(texts)
        category, hits = classify_text(joined)
        primary_doc = max(cluster, key=lambda d: len(d.title))
        published = min((d.published_at or d.fetched_at for d in cluster), default=now_cn())
        importance = _importance(joined, category, len(cluster))
        confidence = _confidence(cluster, hits)
        if importance < min_importance:
            for doc in cluster:
                doc.status = "ignored"
            skipped += 1
            continue

        event = _find_duplicate_event(db, primary_doc.title, published)
        is_new = event is None
        if is_new:
            event = Event(
                event_key=_event_key(primary_doc.title, published),
                title=primary_doc.title,
                published_at=published,
                collected_at=now_cn(),
            )
            db.add(event)
            db.flush()
        else:
            existing_docs = {e.document_id for e in db.scalars(
                select(EventEvidence).where(EventEvidence.event_id == event.id)
            ).all()}
            new_docs = [d for d in cluster if d.id not in existing_docs]
            if new_docs:
                event.analysis_version += 1
                event.collected_at = now_cn()

        media_names = sorted({d.media for d in cluster if d.media})
        # 事件层面的方向：类别先验 + 文本情绪 + 政策措辞（降息/加息等）
        event_direction = _direction(
            joined, category.get("bias", "mixed"), "RISK", category.get("code", "other")
        )
        event.title = primary_doc.title
        event.summary = (primary_doc.summary or primary_doc.title)[:1000]
        event.category = category.get("code", "other")
        event.published_at = published
        event.occurred_at = published
        event.countries = sorted({t for t in ["中国", "美国", "欧盟", "日本"] if t in joined}) or ["未明确"]
        event.institutions = _extract_entities(joined, ["央行", "美联储", "财政部", "证监会", "国务院", "发改委", "OPEC"])
        event.companies = _extract_companies(db, joined)
        event.industries = _extract_industries(joined)
        event.importance = importance
        event.novelty = 70.0 if is_new else 45.0
        event.direction = event_direction
        event.signal_strength = round((importance / 100.0) * {"bullish": 1, "bearish": -1}.get(event_direction, 0) * confidence, 3)
        event.confidence = confidence
        event.data_quality = round(min(1.0, 0.3 + len(cluster) * 0.12 + len(media_names) * 0.08), 2)
        event.source_count = len(cluster)
        event.evidence_count = len(cluster)
        event.reliability = "high" if len(media_names) >= 3 else ("medium" if len(media_names) >= 2 else "low")
        event.horizon = HORIZON.get(event.category, "unknown")
        event.analysis_version = event.analysis_version or 1
        event.analyzed_at = now_cn()
        event.analysis_engine = "rule"
        event.analysis = _analysis_text(category, cluster, importance, confidence)
        event.transmission = [
            {
                "step": 1,
                "from": category.get("name", "事件"),
                "to": "市场预期",
                "detail": category.get("mechanism", ""),
            },
            {
                "step": 2,
                "from": "市场预期",
                "to": "资产价格",
                "detail": "通过折现率、风险溢价与盈利预期传导，方向与幅度取决于预期差与既有仓位。",
            },
        ]
        event.counter_evidence = event.analysis.get("counter_evidence", [])
        event.invalidation = event.analysis.get("invalidation", "")
        event.what_happened = event.analysis.get("what_happened", "")
        event.what_uncertain = event.analysis.get("what_uncertain", "")
        event.what_changed = event.analysis.get("what_changed", "")
        event.watch_items = _watch_items(joined, category)

        # 证据与文档关联
        for doc in cluster:
            doc.event_id = event.id
            doc.status = "analyzed"
            doc.cluster_key = event.event_key
            exists = db.scalar(
                select(EventEvidence).where(
                    EventEvidence.event_id == event.id, EventEvidence.document_id == doc.id
                )
            )
            if not exists:
                db.add(
                    EventEvidence(
                        event_id=event.id,
                        document_id=doc.id,
                        stance="support",
                        source_name=doc.media or doc.source_code,
                        url=doc.url,
                        quote=(doc.summary or doc.title)[:500],
                        reliability=doc.reliability,
                        published_at=doc.published_at,
                    )
                )
        event.evidence_count = len(cluster)

        # 影响资产
        for inst, strength, reason in _match_instruments(db, joined, category):
            impact_direction = _direction(joined, category.get("bias", "mixed"), inst.asset_class_code, category.get("code", "other"))
            exists = db.scalar(
                select(EventImpact).where(
                    EventImpact.event_id == event.id, EventImpact.instrument_symbol == inst.symbol
                )
            )
            if exists:
                continue
            db.add(
                EventImpact(
                    event_id=event.id,
                    instrument_id=inst.id,
                    instrument_symbol=inst.symbol,
                    asset_class_code=inst.asset_class_code,
                    direction=impact_direction,
                    mechanism=reason,
                    horizon=event.horizon,
                    magnitude=round(min(1.0, strength / 4.0) * (importance / 100.0), 3),
                    confidence=round(confidence * 0.85, 3),
                )
            )

        touched_events.append(event.id)
        _snapshot_version(
            db,
            event,
            change_reason="首次规则分析" if is_new else f"新增 {len(cluster)} 条来源，重新计算",
        )
        if is_new:
            created += 1
        else:
            updated += 1

    db.commit()
    return {
        "clusters": len(clusters),
        "events_created": created,
        "events_updated": updated,
        "events_skipped": skipped,
        "documents": len(docs),
        "event_ids": touched_events,
    }


def _extract_entities(text: str, candidates: list[str]) -> list[str]:
    return [c for c in candidates if c in text]


def _snapshot_version(db: Session, event: Event, change_reason: str) -> None:
    exists = db.scalar(
        select(EventAnalysisVersion).where(
            EventAnalysisVersion.event_id == event.id,
            EventAnalysisVersion.version == (event.analysis_version or 1),
        )
    )
    if exists:
        return
    db.add(
        EventAnalysisVersion(
            event_id=event.id,
            version=event.analysis_version or 1,
            engine=event.analysis_engine,
            direction=event.direction,
            confidence=event.confidence,
            importance=event.importance,
            analysis=event.analysis or {},
            change_reason=change_reason,
        )
    )


def _extract_companies(db: Session, text: str) -> list[str]:
    names = db.scalars(select(Instrument.name).where(Instrument.instrument_type == "stock")).all()
    return [n for n in names if n in text][:5]


def _extract_industries(text: str) -> list[str]:
    pool = ["银行", "证券", "保险", "白酒", "半导体", "新能源", "光伏", "医药", "地产", "煤炭", "有色", "军工", "互联网", "汽车", "电力"]
    return [p for p in pool if p in text][:5]


def _watch_items(text: str, category: dict) -> list[str]:
    items = [kw for kw in category.get("keywords", []) if kw in text][:3]
    items.append("后续官方表态与实施细则")
    items.append("相关资产成交量与波动率变化")
    return items[:5]
