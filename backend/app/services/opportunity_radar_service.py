"""机会雷达每日输出。

只做一件事：把候选池的日线算成「今天有几条值得研究的标的」。

四条硬约束（都是为了避免"看起来有用"的假信号）：
1. 最多 10 条。超过 10 条等于没有重点，宁可少给。
2. 没有合格标的时候必须明说「今日无候选」，并给出分布原因，不用空列表冒充。
3. 质量分作为**门槛**（<40 进不了列表），不作为加分项。
4. 相对强弱只在同类样本 ≥5 时计算，样本不足不排名。
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.timeutil import now_cn
from app.models.entities import EtfUniverse, MarketObservation, RadarSnapshot
from app.services import config_service, radar_card
from app.services.universe_service import clean_theme
from app.services.opportunity_service import (
    decide_state,
    opportunity_score,
    relative_strength_scores,
    state_bucket,
    trend_features,
)

MIN_QUALITY = 40.0
# 机会分绝对门槛：低于此分说明趋势强度不足，列出来只会稀释注意力。
# 宁可今天只给 3 条，也不要为凑满 10 条把 25 分的标的塞进来。
MIN_SCORE = 40.0
DEFAULT_LIMIT = 10
MAX_PER_THEME = 2
# 同族最多 2 条：同族全留会霸屏，只留 1 条又会把"30年国债/10年地方债"这类
# 期限不同、实际有区分度的标的误杀（实测只输出 4 条）。
# 每类最多几条：不做配额时，低波动且指数名各异的债券会占满 10 条，
# 因为"同类第一"在小样本类别里很容易拿到，把整类推上前排。
MAX_PER_CATEGORY = 3
# 货币 ETF 是现金管理工具，波动接近 0，放进"机会"列表没有意义
EXCLUDED_CATEGORIES = {"money"}


_FAMILY_CACHE: Optional[dict[str, list[str]]] = None


def _families() -> dict[str, list[str]]:
    global _FAMILY_CACHE
    if _FAMILY_CACHE is None:
        try:
            cfg = config_service.load_yaml("etf_theme_groups.yaml")
            _FAMILY_CACHE = cfg.get("families", {}) or {}
        except Exception:  # noqa: BLE001
            _FAMILY_CACHE = {}
    return _FAMILY_CACHE


def theme_key(tracked_index: str, name: str, families: Optional[dict[str, list[str]]] = None) -> str:
    """主题键：先按名称归族，再退回跟踪指数。

    候选池按「跟踪指数」去重，但"中证酒指数""中证消费50""中证主要消费"是三个不同
    指数、同一个主题。只按指数名去重，前 10 条会被同一板块占满。
    因此优先用主题族（config/etf_theme_groups.yaml）做键。
    """
    table = families if families is not None else _families()
    # 关键词按长度降序，保证"白酒"先于"酒"、"科创50"先于"科技"之外的更具体匹配
    flat = sorted(
        ((kw, fam) for fam, kws in table.items() for kw in kws),
        key=lambda item: -len(item[0]),
    )
    haystack = f"{name} {tracked_index}"
    for keyword, family in flat:
        if keyword and keyword in haystack:
            return f"family:{family}"
    if tracked_index:
        return tracked_index.strip()
    return re.sub(r"\d+", "", clean_theme(name)) or name


def _load_closes(db: Session, symbols: list[str]) -> dict[str, list[float]]:
    """一次读出所有候选的收盘序列，避免逐只查询。"""
    rows = db.execute(
        select(MarketObservation.symbol, MarketObservation.trade_date, MarketObservation.close)
        .where(MarketObservation.symbol.in_(symbols), MarketObservation.close.isnot(None))
        .order_by(MarketObservation.symbol, MarketObservation.trade_date)
    ).all()
    grouped: dict[str, list[float]] = {}
    for symbol, _trade_date, close in rows:
        grouped.setdefault(symbol, []).append(float(close))
    return grouped


def _evaluate_universe(db: Session, candidates: list | None = None) -> dict:
    """给候选池全部标的算特征、状态、相对强弱与机会分。

    **不做任何筛选与去重**——筛选是展示层的事。
    这样 /radar 与 /opportunities 共用同一份计算，不会各算一套。

    返回的 evaluated 每项含：code/symbol/name/category/state/features/
    quality_score/rs_score/score。score 仅在 state == CANDIDATE 时有值。
    """
    if candidates is None:
        candidates = db.scalars(
            select(EtfUniverse).where(EtfUniverse.status == "candidate")
        ).all()
    if not candidates:
        return {"evaluated": [], "buckets": Counter(), "blocked_quality": 0, "candidates": []}

    closes = _load_closes(db, [row.symbol for row in candidates])

    evaluated: list[dict] = []
    buckets: Counter[str] = Counter()
    blocked_quality = 0
    for row in candidates:
        if row.category in EXCLUDED_CATEGORIES:
            buckets["EXCLUDED_CATEGORY"] += 1
            continue
        if row.quality_score is None:
            buckets["NO_QUALITY"] += 1
            blocked_quality += 1
            continue
        if row.quality_score < MIN_QUALITY:
            buckets["LOW_QUALITY"] += 1
            blocked_quality += 1
            continue
        series = closes.get(row.symbol, [])
        feats = trend_features(series)
        state, reasons = decide_state(feats, holding=False)
        buckets[state_bucket(state, feats)] += 1
        evaluated.append(
            {
                "code": row.code,
                "symbol": row.symbol,
                "name": row.name,
                "category": row.category,
                "tracked_index": row.tracked_index or "",
                "theme": theme_key(row.tracked_index or "", row.name),
                "state": state,
                "reasons": reasons,
                "features": feats,
                "quality_score": row.quality_score,
                "quality_confidence": row.quality_confidence,
                "rs_score": None,
                "score": None,
            }
        )

    rs = relative_strength_scores(
        [
            {
                "code": item["code"],
                "category": item["category"],
                "ret20": (item["features"] or {}).get("ret20"),
                "ret60": (item["features"] or {}).get("ret60"),
            }
            for item in evaluated
        ]
    )

    for item in evaluated:
        feats = item["features"]
        rs_score = rs.get(item["code"])
        item["rs_score"] = round(rs_score, 2) if rs_score is not None else None
        if item["state"] == "CANDIDATE" and feats:
            item["score"] = opportunity_score(feats, rs_score)
            item["invalidation"] = _invalidation(feats)

    return {
        "evaluated": evaluated,
        "buckets": buckets,
        "blocked_quality": blocked_quality,
        "candidates": candidates,
    }


def build_daily(db: Session, limit: int = DEFAULT_LIMIT) -> dict:
    result = _evaluate_universe(db)
    candidates = result["candidates"]
    if not candidates:
        return {
            "as_of": now_cn().date().isoformat(),
            "universe": 0,
            "evaluated": 0,
            "items": [],
            "buckets": {},
            "message": "候选池为空，请先刷新 ETF 候选池",
            "blocking": ["候选池为空"],
        }

    evaluated = result["evaluated"]
    buckets = result["buckets"]
    blocked_quality = result["blocked_quality"]
    skipped_category = 0

    # 机会分已在 _evaluate_universe 中算好（与统一出口共用同一份计算），
    # 这里只做筛选：非 CANDIDATE 不参与，分数不足不入榜。
    picks: list[dict] = []
    below_score = 0
    for item in evaluated:
        if item["state"] != "CANDIDATE":
            continue
        if item["score"] is None or item["score"] < MIN_SCORE:
            below_score += 1
            continue
        picks.append(item)

    # 先按分数排，再做同主题去重：同一主题只保留分数最高的一只
    picks.sort(key=lambda x: x["score"], reverse=True)
    seen: Counter[str] = Counter()
    per_category: Counter[str] = Counter()
    top: list[dict] = []
    skipped_theme = 0
    for item in picks:
        if seen[item["theme"]] >= MAX_PER_THEME:
            skipped_theme += 1
            continue
        if per_category[item["category"]] >= MAX_PER_CATEGORY:
            skipped_category += 1
            continue
        seen[item["theme"]] += 1
        per_category[item["category"]] += 1
        top.append(item)
        if len(top) >= limit:
            break

    if top:
        notes = []
        if skipped_theme:
            notes.append(f"{skipped_theme} 只同主题重复")
        if skipped_category:
            notes.append(f"{skipped_category} 只超出类别配额（每类≤{MAX_PER_CATEGORY}）")
        suffix = "；已跳过：" + "、".join(notes) if notes else ""
        message = f"今日 {len(top)} 条候选（共 {len(picks)} 条达标，按机会分取前 {limit}{suffix}）"
        blocking: list[str] = []
    else:
        message = "今日无候选"
        blocking = _explain_empty(buckets, evaluated)
        if below_score:
            blocking.append(f"{below_score} 只趋势达标但机会分低于 {MIN_SCORE:.0f}")

    as_of = now_cn().date()
    # 顺序很重要：先落快照再读上下文，否则「首次进入候选」查不到今天这一条，
    # first_seen 会一直是 None。
    _save_snapshot(db, as_of, evaluated)
    context = _snapshot_context(db, as_of, [item["code"] for item in evaluated])
    cards = [
        radar_card.build_card(
            item,
            first_seen=context["first_seen"].get(item["code"]),
            prev=context["prev"].get(item["code"]),
            updated_at=now_cn().isoformat(sep=" ", timespec="seconds"),
        )
        for item in top
    ]

    return {
        "as_of": as_of.isoformat(),
        "generated_at": now_cn().isoformat(sep=" ", timespec="seconds"),
        "universe": len(candidates),
        "evaluated": len(evaluated),
        "qualified": len(picks),
        "below_min_score": below_score,
        "min_score": MIN_SCORE,
        "skipped_same_theme": skipped_theme,
        "skipped_category_quota": skipped_category,
        "max_per_category": MAX_PER_CATEGORY,
        "limit": limit,
        "items": cards,
        "card_fields": 20,
        "buckets": dict(buckets),
        "blocked_by_quality": blocked_quality,
        "min_quality": MIN_QUALITY,
        "message": message,
        "blocking": blocking,
    }


def _snapshot_context(db: Session, as_of, codes: list[str]) -> dict:
    """读取上期快照与首次发现日期。无快照时返回空 dict，不编造历史。"""
    prev_date = db.scalar(
        select(func.max(RadarSnapshot.as_of)).where(RadarSnapshot.as_of < as_of)
    )
    prev: dict[str, dict] = {}
    if prev_date:
        for row in db.scalars(select(RadarSnapshot).where(RadarSnapshot.as_of == prev_date)):
            prev[row.code] = {
                "score": row.score,
                "state": row.state,
                "as_of": prev_date.isoformat(),
            }

    first_seen: dict[str, str] = {}
    if codes:
        rows = db.execute(
            select(RadarSnapshot.code, func.min(RadarSnapshot.as_of))
            .where(RadarSnapshot.state == "CANDIDATE", RadarSnapshot.code.in_(codes))
            .group_by(RadarSnapshot.code)
        ).all()
        first_seen = {code: seen.isoformat() for code, seen in rows}
    return {"prev": prev, "first_seen": first_seen}


def _save_snapshot(db: Session, as_of, evaluated: list[dict]) -> None:
    """幂等写入当日快照：同一天重跑先删后插，避免主键冲突。"""
    db.execute(delete(RadarSnapshot).where(RadarSnapshot.as_of == as_of))
    for item in evaluated:
        db.add(
            RadarSnapshot(
                as_of=as_of,
                code=item["code"],
                name=item["name"],
                state=item["state"],
                score=item.get("score"),
                quality_score=item.get("quality_score"),
            )
        )
    db.commit()


def _invalidation(feats: dict) -> str:
    """失效条件必须可执行，不能写"行情变化时失效"这种废话。"""
    return f"收盘跌破 MA20（约 {feats['ma20']:.3f}）或距 250 日高点回撤超过 25%"


def _explain_empty(buckets: Counter, evaluated: list[dict]) -> list[str]:
    reasons: list[str] = []
    if buckets.get("NO_QUALITY"):
        reasons.append(f"{buckets['NO_QUALITY']} 只尚未计算质量分（请先运行质量分）")
    if buckets.get("LOW_QUALITY"):
        reasons.append(f"{buckets['LOW_QUALITY']} 只质量分低于 {MIN_QUALITY:.0f}，产品不达标")
    if buckets.get("TREND_BAD"):
        reasons.append(f"{buckets['TREND_BAD']} 只价格在 MA60 下方，趋势未修复")
    if buckets.get("WAIT"):
        reasons.append(f"{buckets['WAIT']} 只短期涨幅过大，处于追高区间")
    if buckets.get("WATCH"):
        reasons.append(f"{buckets['WATCH']} 只趋势刚转正，尚未确认")
    if buckets.get("NO_DATA"):
        reasons.append(f"{buckets['NO_DATA']} 只日线不足 60 根")
    if not reasons:
        reasons.append("没有标的满足全部条件")
    return reasons


def _serialize(item: dict) -> dict:
    feats = item["features"] or {}
    return {
        "code": item["code"],
        "symbol": item["symbol"],
        "name": item["name"],
        "category": item["category"],
        "theme": item["theme"],
        "state": item["state"],
        "score": item["score"],
        "rs_score": item.get("rs_score"),
        "quality_score": item["quality_score"],
        "quality_confidence": item["quality_confidence"],
        "price": feats.get("price"),
        "ma20": feats.get("ma20"),
        "ma60": feats.get("ma60"),
        "dev20": feats.get("dev20"),
        "dev60": feats.get("dev60"),
        "ret20": feats.get("ret20"),
        "ret60": feats.get("ret60"),
        "vol20": feats.get("vol20"),
        "drawdown_250": feats.get("drawdown_250"),
        "reasons": item["reasons"],
        "invalidation": item["invalidation"],
    }
