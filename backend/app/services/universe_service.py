"""ETF 候选池（C 层）：全量拉取 → 分类 → 门槛 → 同指数去重 → 落库。

设计要点：
- 全市场 1,657 只全部登记，被拒绝的保留原因，不物理删除。
- 只有「通过门槛 + 去重」的才是 candidate，进入后续评分。
- 不同资产类别门槛不同（跨境溢价可到 10%，货币不计算折溢价）。
- 用当日成交额近似 20 日均额（首期），字段 amount_basis 明确标注。
"""
from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import now_cn
from app.models.entities import EtfUniverse
from app.providers.etf_universe import (
    EtfRow,
    EtfSupplementProvider,
    EtfUniverseProvider,
    listed_days,
)
from app.services import config_service

CROSS_KEYWORDS = [
    "纳指", "标普", "纳斯达克", "恒生", "港股", "中概", "日经", "德国", "法国",
    "美国", "亚太", "全球", "东南亚", "沙特", "英国", "欧洲", "越南", "印度", "韩国",
    "香港",  # 513090 香港证券 ETF：名称不含"港股"，但属于跨境
    "巴西", "日本", "亚洲",
    # 注意：不能加"新兴"——"战略新兴 ETF"是国内主题，会被误判为跨境
]
BROAD_KEYWORDS = [
    "沪深300", "中证300", "中证500", "中证1000", "中证2000", "中证A500", "中证A50",
    "A500", "A50", "上证50", "上证180", "上证380", "深证100", "创业板", "科创50",
    "科创100", "科创200", "红利", "价值", "成长", "等权", "全指", "MSCI",
    "上证指数", "上证综指", "中证800", "中证100", "富时", "罗素",
]
# 强行业词：名称命中即归行业主题，优先于宽基。
# 解决「创业板软件/创业板新能源」被"创业板"前缀误判成宽基的问题。
# 注意不要把"成长/价值/红利"放进来——它们本身就是宽基风格。
SECTOR_STRONG_KEYWORDS = [
    "软件", "人工智能", "新能源", "芯片", "半导体", "医药", "医疗", "军工",
    "证券", "券商", "银行", "保险", "消费", "酒", "食品", "饮料", "地产",
    "有色", "钢铁", "煤炭", "化工", "通信", "计算机", "机器人", "游戏",
    "传媒", "光伏", "电池", "储能", "稀土", "农业", "养殖", "航空", "物流",
]
COMMODITY_KEYWORDS = ["黄金", "白银", "原油", "商品", "豆粕", "有色", "能源化工", "铜", "铝"]
FUND_COMPANIES = [
    "华夏", "易方达", "华泰柏瑞", "国泰", "广发", "南方", "嘉实", "富国", "汇添富",
    "博时", "招商", "工银瑞信", "工银", "建信", "景顺长城", "景顺", "银华", "华安",
    "鹏华", "天弘", "中欧", "平安", "兴业", "浦银安盛", "国联安", "海富通", "万家",
    "永赢", "中银", "西部利得", "前海开源", "民生", "东财", "华宝", "国投瑞银",
    "泰康", "新华", "方正富邦", "中金", "浙商", "湘财", "华富", "长信", "诺安",
    "大成", "国寿安保", "中信保诚", "中邮", "光大保德信", "申万菱信", "融通",
    "长城", "宝盈", "金鹰", "德邦", "红土创新", "兴银", "国金", "华润元大",
    "蜂巢", "惠升", "尚正", "百嘉", "苏新", "京管泰富", "博远", "国新国证",
    "华泰证券资管", "东方", "华商", "摩根", "富达", "贝莱德", "施罗德", "路博迈",
]


def rules() -> dict:
    return config_service.load_yaml("etf_universe_rules.yaml")


def classify(name: str, fund_type: str = "") -> str:
    """按名称 + 基金类型分类。顺序很重要：货币/债券优先，跨境优先于行业。"""
    text = f"{name} {fund_type}"
    if "货币" in text or "现金" in name or "日利" in name or "添益" in name:
        return "money"
    if "REIT" in name.upper() or "不动产投资信托" in name:
        return "reit"
    # 短融/融资券/存单/票据同属债券型，名称里没有"债"字，必须单列
    if (
        "债券" in fund_type
        or "债" in name
        or "政金" in name
        or "信用" in name
        or "转债" in name
        or "短融" in name
        or "融资券" in name
        or "存单" in name
        or "票据" in name
    ):
        return "bond"
    if any(k in name for k in COMMODITY_KEYWORDS):
        return "commodity"
    if any(k in name for k in CROSS_KEYWORDS):
        return "cross_border"
    # 强行业词优先于宽基前缀：「创业板软件」是行业，不是宽基
    if any(k in name for k in SECTOR_STRONG_KEYWORDS):
        return "sector"
    if any(k in name for k in BROAD_KEYWORDS):
        return "broad"
    # 兜底必须是 sector：分类不能依赖「是否拉到基金详情」，
    # 否则没详情的一律掉进 other，门槛与统计全部失真。
    return "sector"


def clean_theme(name: str) -> str:
    """去掉 ETF 后缀与基金公司名，作为无跟踪指数时的去重键。"""
    text = name.replace("ETF", "").replace("基金", "").strip()
    for company in sorted(FUND_COMPANIES, key=len, reverse=True):
        if text.endswith(company):
            text = text[: -len(company)]
            break
    return text.strip() or name


def _reject(record: dict, reason: str) -> None:
    record["status"] = "rejected"
    if reason not in record["reject_reasons"]:
        record["reject_reasons"].append(reason)


def build_records(rows: list[EtfRow], details: dict[str, dict], cfg: dict) -> dict:
    """纯函数：分类 + 门槛 + 去重。方便单测，不碰数据库。"""
    classes_cfg = cfg.get("classes", {})
    gate = cfg.get("global_exclude", {})
    min_days_global = int(gate.get("min_listed_days", 250))
    dedupe_cfg = cfg.get("dedupe", {})
    max_per_index = int(dedupe_cfg.get("max_per_index", 2))
    second_ratio = float(dedupe_cfg.get("second_place_min_ratio", 0.5))
    today = now_cn().date()

    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        detail = details.get(row.code, {})
        category = classify(row.name, detail.get("fund_type", ""))
        days = listed_days(row.list_date)
        nav = detail.get("nav")
        premium = None
        premium_basis = None
        if nav and row.price and category != "money":
            # 净值为 T-1（或更早）发布，而盘中价格是 T 日实时价。
            # 直接用 price/nav 会把当日涨跌算成溢价：实测 2026-11-05 盘中
            # 一次刷新，因当日大涨导致 premium>3% 的标的从 8 只暴增到 49 只。
            # 因此先用涨跌幅还原前收盘价，再用前收盘对净值——两者才是同一时点。
            prev_close = None
            if row.change_pct is not None and row.change_pct > -100:
                prev_close = row.price / (1 + row.change_pct / 100)
            if prev_close and prev_close > 0:
                premium = prev_close / nav - 1
                premium_basis = "prev_close"
            else:
                premium = row.price / nav - 1
                premium_basis = "price"

        record = {
            "code": row.code,
            "symbol": row.symbol,
            "name": row.name,
            "market": row.market,
            "category": category,
            "tracked_index": (detail.get("index_name") or "").strip(),
            "price": row.price,
            "change_pct": row.change_pct,
            "amount": row.amount,
            "amount_20d": None,
            "amount_basis": cfg.get("amount_basis", "1d"),
            "mktcap": row.mktcap,
            "endnav": detail.get("endnav"),
            "nav": nav,
            "premium_est": premium,
            "premium_basis": premium_basis,
            "list_date": row.list_date,
            "listed_days": days,
            "status": "candidate",
            "reject_reasons": [],
            "data_coverage": {
                "amount": row.amount is not None,
                "mktcap": row.mktcap is not None,
                "list_date": row.list_date is not None,
                "nav": nav is not None,
                "endnav": detail.get("endnav") is not None,
                "premium": premium is not None,
                "tracked_index": bool(detail.get("index_name")),
                "fee": False,
                "constituents": False,
            },
            "quality_score": None,
            "snapshot_at": now_cn(),
        }

        # 0) 全局名称排除（退市 / 终止上市 / REITs）。
        #    必须在门槛之前生效：REITs 若不先拦下，会掉进行业 ETF 继续参与
        #    打分与去重，属于错误纳入而不只是漏判。
        excluded_hit = next(
            (kw for kw in gate.get("name_keywords", []) if kw and kw in row.name),
            None,
        )
        if excluded_hit:
            _reject(record, f"name_excluded:{excluded_hit}")
            buckets[category].append(record)
            continue

        # 1) 上市时间
        if days is None or days < min_days_global:
            _reject(record, "listed_days<250")

        # 2) 成交额
        rule = classes_cfg.get(category, {})
        min_amount = float(rule.get("min_amount_20d", 5e7))
        if (row.amount or 0) < min_amount:
            _reject(record, f"amount<{min_amount / 1e4:.0f}万")

        # 3) 未拉到详情的（超出请求预算）不进候选，避免用不完整数据
        if record["status"] == "candidate" and row.code not in details:
            _reject(record, "detail_budget_exceeded")

        # 4) 折溢价（只对已通过前几项的标的检查，避免原因统计失真）
        if record["status"] == "candidate":
            max_premium = rule.get("max_premium_abs")
            if category == "money":
                pass  # 货币基金净值口径不同，不做折溢价门禁
            elif premium is None:
                _reject(record, "premium_unavailable")
            elif max_premium is not None and abs(premium) > float(max_premium):
                _reject(record, f"premium>{float(max_premium) * 100:.0f}%")

        buckets[category].append(record)

    candidate_count = 0
    for category, records in buckets.items():
        groups: dict[str, list[dict]] = defaultdict(list)
        for record in records:
            if record["status"] != "candidate":
                continue
            key = record["tracked_index"] or clean_theme(record["name"])
            groups[key].append(record)
        for key, items in groups.items():
            items.sort(key=lambda r: (r["amount"] or 0, r["mktcap"] or 0), reverse=True)
            keep = items[:1]
            if (
                len(items) > 1
                and max_per_index >= 2
                and (items[1]["amount"] or 0) >= (items[0]["amount"] or 0) * second_ratio
                and (items[1]["mktcap"] or 0) >= (items[0]["mktcap"] or 0) * second_ratio
            ):
                keep.append(items[1])
            for item in items:
                if item in keep:
                    candidate_count += 1
                else:
                    _reject(item, f"duplicate_index:{key[:20]}")

    reason_counter: Counter[str] = Counter()
    for records in buckets.values():
        for record in records:
            for reason in record["reject_reasons"]:
                reason_counter[reason.split(":")[0]] += 1

    by_category = {
        category: {
            "label": classes_cfg.get(category, {}).get("label", category),
            "total": len(records),
            "candidate": len([r for r in records if r["status"] == "candidate"]),
        }
        for category, records in buckets.items()
    }
    return {
        "buckets": dict(buckets),
        "candidate": candidate_count,
        "reject_reasons": dict(reason_counter.most_common(12)),
        "by_category": dict(sorted(by_category.items(), key=lambda x: -x[1]["total"])),
    }


async def _fetch_all(
    provider: EtfUniverseProvider, detail_limit: int, interval: float, cfg: dict
) -> tuple[list[EtfRow], int, list[str], dict[str, dict], int]:
    rows, total, errors = await provider.fetch_list()

    # 补充扫描：列表接口不含债券 ETF / REIT，用代码段批量补齐
    supplement_count = 0
    supp_cfg = cfg.get("supplement", {})
    if supp_cfg.get("enabled"):
        supp_provider = EtfSupplementProvider()
        supp_rows, supp_errors = await supp_provider.scan(
            supp_cfg.get("segments", []),
            batch_size=int(supp_cfg.get("batch_size", 50)),
            interval=0.25,
        )
        errors = errors + supp_errors
        known = {r.code for r in rows}
        extra = [r for r in supp_rows if r.code not in known]
        supplement_count = len(extra)
        rows.extend(extra)
        total = len(rows)
    classes_cfg = cfg.get("classes", {})
    min_amount_global = min(
        [float(c.get("min_amount_20d", 0)) for c in classes_cfg.values()] or [0]
    )
    min_days = int(cfg.get("global_exclude", {}).get("min_listed_days", 250))
    today = now_cn().date()
    prelim = [
        r for r in rows
        if (r.amount or 0) >= min_amount_global
        and r.list_date
        and (today - r.list_date).days >= min_days
    ]
    # 按类别配额选详情对象，避免低流动性类别被全局排序挤掉
    quota = (cfg.get("fetch", {}) or {}).get("detail_quota", {}) or {}
    grouped: dict[str, list[EtfRow]] = defaultdict(list)
    for row in prelim:
        grouped[classify(row.name)].append(row)
    selected: list[EtfRow] = []
    for category, items in grouped.items():
        items.sort(key=lambda r: r.amount or 0, reverse=True)
        selected.extend(items[: int(quota.get(category, 40))])
    selected.sort(key=lambda r: r.amount or 0, reverse=True)
    details: dict[str, dict] = {}
    for row in selected[:detail_limit]:
        details[row.code] = await provider.fetch_detail(row.code, interval=interval)
    return rows, total, errors, details, supplement_count


def refresh_universe(db: Session, detail_limit: Optional[int] = None) -> dict:
    cfg = rules()
    started = now_cn()
    provider = EtfUniverseProvider()
    fetch_cfg = cfg.get("fetch", {})
    limit = int(detail_limit or fetch_cfg.get("max_detail_checks", 520))
    interval = float(fetch_cfg.get("detail_interval_seconds", 0.4))

    rows, total, errors, details, supplement_count = asyncio.run(
        _fetch_all(provider, limit, interval, cfg)
    )
    built = build_records(rows, details, cfg)

    existing = {row.code: row for row in db.scalars(select(EtfUniverse)).all()}
    created = updated = 0
    for records in built["buckets"].values():
        for record in records:
            obj = existing.get(record["code"])
            if obj is None:
                obj = EtfUniverse(code=record["code"], symbol=record["symbol"], name=record["name"])
                db.add(obj)
                created += 1
            else:
                updated += 1
            for field in (
                "symbol", "name", "market", "category", "tracked_index", "price", "change_pct",
                "amount", "amount_20d", "amount_basis", "mktcap", "endnav", "nav",
                "premium_est", "premium_basis",
                "list_date", "listed_days", "status", "reject_reasons", "data_coverage", "snapshot_at",
            ):
                setattr(obj, field, record[field])
    db.commit()

    return {
        "ok": True,
        "list_total": total,
        "parsed": len(rows),
        "detail_fetched": len(details),
        "supplement_added": supplement_count,
        "candidate": built["candidate"],
        "rejected": len(rows) - built["candidate"],
        "by_category": built["by_category"],
        "reject_reasons": built["reject_reasons"],
        "created": created,
        "updated": updated,
        "errors": errors[:5],
        "elapsed_seconds": round((now_cn() - started).total_seconds(), 1),
        "amount_basis": cfg.get("amount_basis", "1d"),
    }


def session_state(now) -> str:
    """当日数据是否完整。

    A 股成交额门槛按「全天量」设定，盘中（含午休）只有半日量，候选数必然偏低。
    这不是数据错误，但必须让使用者看见，否则会把盘中快照当成结论。
    """
    if now.weekday() >= 5:
        return "closed"          # 周末：持有的是上个交易日完整数据
    minutes = now.hour * 60 + now.minute
    if minutes < 9 * 60 + 30:
        return "pre_open"        # 盘前：昨日完整数据
    if minutes >= 15 * 60 + 5:
        return "after_close"     # 收盘后：完整交易日
    return "intraday"            # 盘中或午休：成交额不完整


def universe_summary(db: Session) -> dict:
    rows = db.scalars(select(EtfUniverse)).all()
    if not rows:
        return {"total": 0, "candidate": 0, "by_category": [], "reject_reasons": {}, "snapshot_at": None}
    counter: Counter[str] = Counter()
    for row in rows:
        for reason in row.reject_reasons or []:
            counter[reason.split(":")[0]] += 1
    grouped: dict[str, dict] = {}
    for row in rows:
        entry = grouped.setdefault(row.category, {"category": row.category, "total": 0, "candidate": 0})
        entry["total"] += 1
        if row.status == "candidate":
            entry["candidate"] += 1
    latest = max((row.snapshot_at for row in rows if row.snapshot_at), default=None)
    state = session_state(now_cn())
    intraday = state == "intraday"
    return {
        "total": len(rows),
        "candidate": len([r for r in rows if r.status == "candidate"]),
        "by_category": sorted(grouped.values(), key=lambda x: -x["total"]),
        "reject_reasons": dict(counter.most_common(10)),
        "snapshot_at": latest.isoformat(sep=" ", timespec="seconds") if latest else None,
        "amount_basis": rows[0].amount_basis,
        "intraday": intraday,
        "session_state": state,
        "session_note": (
            "当前为盘中快照：成交额只含半日，候选数会低于收盘后。建议 15:05 后再刷新，"
            "此时数据才代表完整交易日。"
            if intraday
            else ""
        ),
    }
