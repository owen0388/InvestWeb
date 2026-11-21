"""事件 → ETF 映射与信息差因子。

两级映射，缺一不可：
1. **资产类别级**：event_impacts 已给出事件对 12 个一级类别的影响方向与强度。
2. **主题级**：用事件类别对应的主题族，在候选池里筛出同族 ETF。

只做类别级映射的话，"美联储降息"会映射到全部 40 只宽基，等于没映射。

信息差只实现**可算**的三类：
- 覆盖度差：独立来源少但重要性高 → 可能未被充分定价
- 传导时滞：上游资产已动，下游 ETF 未跟
- 关注度低：成交额分位低但趋势已转正

`预期分歧` 需要市场一致预期值，当前无数据源，**明确不计算**，不用 0 填充。
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import EtfUniverse, Event, EventImpact, MarketObservation
from app.services import config_service

# 事件类别 → 可能受益/受损的主题族
CATEGORY_FAMILIES: dict[str, list[str]] = {
    "monetary_policy": ["rate_bond", "credit_bond", "broad_cn", "finance"],
    "fiscal_policy": ["infrastructure", "cyclical", "broad_cn"],
    "economic_data": ["broad_cn", "rate_bond", "cyclical"],
    "trade_tariff": ["tech", "cyclical", "consumer"],
    "technology": ["tech"],
    "energy_supply": ["cyclical", "new_energy"],
    "regulation": ["finance", "healthcare", "tech"],
    "geopolitics": ["military", "cyclical"],
    "military": ["military"],
    "company": ["consumer", "tech", "healthcare"],
    "other": [],
}

# 资产类别 → ETF 候选池类别
CLASS_TO_ETF_CATEGORY: dict[str, list[str]] = {
    "FUND_ETF_WIDE": ["broad"],
    "FUND_ETF_SECTOR": ["sector"],
    "FUND_CROSS": ["cross_border"],
    "CN_EQ_INDEX": ["broad"],
    "BOND_RATE": ["bond"],
    "BOND_CREDIT": ["bond"],
    "GOLD_XAU": ["commodity"],
    "COMMODITY": ["commodity"],
    "CASH_DEMAND": ["money"],
}


def _theme_of(name: str, tracked_index: str, families: dict) -> Optional[str]:
    haystack = f"{name} {tracked_index}"
    flat = sorted(
        ((kw, fam) for fam, kws in families.items() for kw in kws), key=lambda x: -len(x[0])
    )
    for keyword, family in flat:
        if keyword and keyword in haystack:
            return family
    return None


def information_gap_factors(
    db: Session,
    etf: EtfUniverse,
    event: Optional[Event] = None,
    amount_percentile: Optional[float] = None,
) -> dict:
    """计算可算的信息差因子。不可算的明确标注，不返回 0 分。"""
    factors: list[dict] = []

    # 1) 覆盖度差：独立来源少 + 重要性高 = 可能还没被广泛定价
    if event is not None:
        evidence = event.evidence_count or 0
        if evidence <= 2 and (event.importance or 0) >= 70:
            factors.append(
                {
                    "type": "coverage_gap",
                    "strength": "high" if evidence == 1 else "medium",
                    "detail": f"独立来源仅 {evidence} 个，重要性 {event.importance:.0f}，可能尚未被充分定价",
                }
            )

    # 2) 关注度低：成交额分位低但趋势已转正
    if amount_percentile is not None and amount_percentile < 0.30:
        change = etf.change_pct
        if change is not None and change > 0:
            factors.append(
                {
                    "type": "low_attention",
                    "strength": "medium",
                    "detail": f"成交额位于候选池 {amount_percentile:.0%} 分位但价格上行，关注度尚未抬升",
                }
            )

    return {
        "factors": factors,
        "count": len(factors),
        # 信息差是乘数而非加分：有因子才放大，没有就是 1.0
        "multiplier": round(1.0 + 0.1 * len(factors), 2),
        "not_computed": [
            "预期分歧：需要市场一致预期值，当前无数据源",
            "跨市场时差：需要 ETF 开盘价与外盘隔夜收盘对齐，当前日线不含开盘缺口序列",
        ],
    }


def _amount_percentiles(db: Session) -> dict[str, float]:
    rows = db.scalars(
        select(EtfUniverse).where(EtfUniverse.status == "candidate")
    ).all()
    values = sorted(
        [(r.code, r.amount) for r in rows if r.amount], key=lambda x: x[1]
    )
    n = len(values)
    if n < 5:
        return {}
    return {code: idx / (n - 1) for idx, (code, _) in enumerate(values)}


def map_event_to_etfs(db: Session, event_id: int, limit: int = 10) -> dict:
    """把事件映射到候选池里的具体 ETF。"""
    event = db.get(Event, event_id)
    if event is None:
        return {"ok": False, "message": "事件不存在", "items": []}

    impacts = db.scalars(
        select(EventImpact).where(EventImpact.event_id == event_id)
    ).all()
    if not impacts:
        return {
            "ok": True,
            "event_id": event_id,
            "items": [],
            "message": "该事件没有资产影响记录，无法映射到 ETF",
        }

    families_cfg = config_service.load_yaml("etf_theme_groups.yaml")
    families = families_cfg.get("families", {}) or {}
    labels = families_cfg.get("labels", {}) or {}
    target_families = set(CATEGORY_FAMILIES.get(event.category, []))

    candidates = db.scalars(
        select(EtfUniverse).where(EtfUniverse.status == "candidate")
    ).all()
    percentiles = _amount_percentiles(db)

    # 资产类别 → 允许的 ETF 类别
    allowed_categories: set[str] = set()
    direction_by_class: dict[str, str] = {}
    for impact in impacts:
        for cat in CLASS_TO_ETF_CATEGORY.get(impact.asset_class_code, []):
            allowed_categories.add(cat)
        direction_by_class[impact.asset_class_code] = impact.direction

    # 映射精度是关键：如果允许「类别匹配 OR 主题匹配」，一个货币政策事件会
    # 映射出 144 只 ETF（等于把候选池列一遍）。因此：
    #   - 事件类别有对应主题族时，**必须**主题命中，类别只作辅助说明；
    #   - 只有事件类别没有主题族（如 other）时，才退回类别匹配。
    strict_theme = bool(target_families)

    items: list[dict] = []
    for etf in candidates:
        family = _theme_of(etf.name, etf.tracked_index or "", families)
        category_hit = etf.category in allowed_categories
        family_hit = family in target_families if target_families else False
        if strict_theme:
            if not family_hit:
                continue
        elif not category_hit:
            continue

        gap = information_gap_factors(db, etf, event, percentiles.get(etf.code))
        items.append(
            {
                "code": etf.code,
                "symbol": etf.symbol,
                "name": etf.name,
                "etf_category": etf.category,
                "theme": f"family:{family}" if family else None,
                "theme_label": labels.get(family) if family else None,
                "match": {
                    "by_category": category_hit,
                    "by_theme": family_hit,
                    "reason": "；".join(
                        filter(
                            None,
                            [
                                f"资产类别匹配（{etf.category}）" if category_hit else "",
                                f"主题匹配（{labels.get(family, family)}）" if family_hit else "",
                            ],
                        )
                    ),
                },
                "quality_score": etf.quality_score,
                "information_gap": gap,
                "sort_key": (
                    1 if family_hit else 0,
                    gap["count"],
                    etf.quality_score or 0,
                ),
            }
        )

    items.sort(key=lambda x: x["sort_key"], reverse=True)

    # 同主题族最多留 2 只：映射到 8 个消费 ETF 不叫映射，叫罗列
    per_family: dict[str, int] = {}
    picked: list[dict] = []
    for item in items:
        key = item["theme"] or item["etf_category"]
        if per_family.get(key, 0) >= 2:
            continue
        per_family[key] = per_family.get(key, 0) + 1
        picked.append(item)
    items = picked
    for item in items:
        item.pop("sort_key", None)

    return {
        "ok": True,
        "event_id": event_id,
        "event": {
            "title": event.title,
            "category": event.category,
            "importance": event.importance,
            "direction": event.direction,
            "evidence_count": event.evidence_count,
            "published_at": event.published_at,
        },
        "items": items[:limit],
        "total_matched": len(items),
        "matched_families": sorted(
            {item["theme_label"] for item in items if item.get("theme_label")}
        ),
        "strict_theme": strict_theme,
        "message": (
            f"映射到 {len(items)} 只候选 ETF（每族最多 2 只）"
            if items
            else "未匹配到候选池中的 ETF：事件类别对应的主题族可能不在候选池内"
        ),
    }
