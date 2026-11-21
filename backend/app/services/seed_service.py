"""资产目录 / 数据源 / 示例组合的初始化（幂等）。

新增 YAML 条目重启即可生效；已有条目的用户自定义字段（收藏、启用状态、
人工备注）不会被覆盖。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import now_cn
from app.models.entities import (
    AssetClass,
    DataSource,
    Instrument,
    InstrumentAlias,
    InvestorProfile,
    Portfolio,
    PortfolioHolding,
    RiskConstraint,
    StrategyConfig,
    SystemSetting,
)
from app.services import config_service


def _flatten_classes() -> list[dict]:
    result: list[dict] = []
    for idx, top in enumerate(config_service.asset_classes_config().get("classes", [])):
        result.append({**top, "level": 1, "parent": None, "sort": idx * 100})
        for cidx, child in enumerate(top.get("children", [])):
            result.append({**child, "level": 2, "parent": top["code"], "sort": idx * 100 + cidx + 1})
    return result


def seed_asset_classes(db: Session) -> int:
    changed = 0
    for row in _flatten_classes():
        obj = db.scalar(select(AssetClass).where(AssetClass.code == row["code"]))
        if obj is None:
            obj = AssetClass(code=row["code"])
            db.add(obj)
        obj.name = row.get("name", row["code"])
        obj.level = row["level"]
        obj.parent_code = row["parent"]
        obj.description = row.get("description", obj.description or "")
        obj.analysis_module = row.get("module", "generic")
        obj.tradable = bool(row.get("tradable", True))
        obj.default_min_weight = float(row.get("min", 0.0))
        obj.default_max_weight = float(row.get("max", 1.0))
        obj.liquidity_tier = row.get("liquidity", "high")
        obj.sort_order = row["sort"]
        obj.enabled = True
        changed += 1
    db.flush()
    return changed


def seed_instruments(db: Session) -> int:
    changed = 0
    for row in config_service.instruments_config().get("instruments", []):
        symbol = row["symbol"]
        obj = db.scalar(select(Instrument).where(Instrument.symbol == symbol))
        if obj is None:
            obj = Instrument(symbol=symbol, name=row["name"], asset_class_code=row["class"])
            db.add(obj)
        obj.name = row["name"]
        obj.asset_class_code = row["class"]
        obj.market = row.get("market", "CN")
        obj.currency = row.get("currency", "CNY")
        obj.instrument_type = row.get("type", "index")
        obj.data_source_code = _source_code(row.get("provider"))
        obj.provider_symbol = row.get("provider_symbol") or symbol
        obj.analysis_module = row.get("module") or _module_for(db, row["class"])
        obj.risk_level = row.get("risk", "medium")
        obj.liquidity_tier = row.get("liquidity", "high")
        obj.qualification_required = row.get("qualification", "无")
        obj.tags = row.get("tags", [])
        obj.notes = row.get("notes", obj.notes or "")
        if obj.data_status in (None, "") or obj.last_data_at is None:
            obj.data_status = row.get("status", "unverified" if row.get("provider") != "manual" else "manual")
        obj.enabled = True if obj.id is None else obj.enabled
        changed += 1

        # 名称作为默认检索别名
        alias = db.scalar(
            select(InstrumentAlias).where(
                InstrumentAlias.alias == row["name"], InstrumentAlias.source == "name"
            )
        )
        if alias is None:
            db.flush()
            db.add(
                InstrumentAlias(
                    instrument_id=obj.id, alias=row["name"], alias_type="name", source="name"
                )
            )
    db.flush()
    return changed


PROVIDER_TO_SOURCE = {
    "sina": "sina_quote",
    "eastmoney": "eastmoney_kline",
    "manual": "manual_entry",
    "worldbank": "worldbank_macro",
    "fred": "fred_macro",
}


def _source_code(provider: str | None) -> str | None:
    """YAML 里的 provider 是厂商名，数据库存的是 data_sources.code。"""
    if not provider:
        return None
    return PROVIDER_TO_SOURCE.get(provider, provider)


def _module_for(db: Session, class_code: str) -> str:
    obj = db.scalar(select(AssetClass).where(AssetClass.code == class_code))
    return obj.analysis_module if obj else "generic"


def seed_data_sources(db: Session) -> int:
    changed = 0
    for row in config_service.data_sources_config().get("sources", []):
        obj = db.scalar(select(DataSource).where(DataSource.code == row["code"]))
        if obj is None:
            obj = DataSource(code=row["code"], name=row["name"], category=row.get("category", "market"))
            db.add(obj)
        obj.name = row["name"]
        obj.category = row.get("category", "market")
        obj.provider = row.get("provider", "")
        obj.endpoint = row.get("endpoint", "")
        obj.auth_type = row.get("auth_type", "none")
        obj.requires_key = bool(row.get("requires_key", False))
        obj.rate_limit = row.get("rate_limit", "")
        obj.terms_url = row.get("terms_url", "")
        obj.coverage = row.get("coverage", "")
        obj.interval_minutes = int(row.get("interval_minutes", 60))
        obj.enabled = obj.enabled if obj.id else True
        if obj.last_run_at is None:
            obj.status = "unconfigured" if obj.requires_key else "idle"
            obj.status_message = row.get("note", "")
        changed += 1
    db.flush()
    return changed


def seed_example_portfolio(db: Session) -> Portfolio:
    cfg = config_service.risk_limits()
    example = cfg.get("example_portfolio", {})
    portfolio = db.scalar(select(Portfolio).where(Portfolio.owner == "local").order_by(Portfolio.id))
    if portfolio is None:
        portfolio = Portfolio(
            name=example.get("name", "示例组合（非个性化建议）"),
            owner="local",
            total_value=float(example.get("total_value", 1_000_000)),
            cash_value=float(example.get("cash_value", 80_000)),
            liabilities=0.0,
            is_example=True,
            is_default=True,
            notes="系统初始化示例组合，用于展示研究流程，不构成个性化建议。",
        )
        db.add(portfolio)
        db.flush()

    profile = db.scalar(select(InvestorProfile).where(InvestorProfile.portfolio_id == portfolio.id))
    if profile is None:
        profile = InvestorProfile(portfolio_id=portfolio.id, confirmed=False)
        db.add(profile)

    # 示例持仓：按示例基准权重的近似金额，持仓价格使用真实收盘价（采集后回填）
    base_weights = example.get("base_weights", {})
    limits = cfg.get("class_limits", {})
    for class_code, weight in base_weights.items():
        constraint = db.scalar(
            select(RiskConstraint).where(
                RiskConstraint.portfolio_id == portfolio.id,
                RiskConstraint.asset_class_code == class_code,
            )
        )
        if constraint is None:
            cls = db.scalar(select(AssetClass).where(AssetClass.code == class_code))
            cmin = float(limits.get(class_code, {}).get("min", cls.default_min_weight if cls else 0.0))
            cmax = float(limits.get(class_code, {}).get("max", cls.default_max_weight if cls else 1.0))
            db.add(
                RiskConstraint(
                    portfolio_id=portfolio.id,
                    asset_class_code=class_code,
                    min_weight=cmin,
                    max_weight=cmax,
                    max_single_instrument=float(cfg.get("portfolio", {}).get("max_single_instrument", 0.2)),
                    max_industry=float(cfg.get("portfolio", {}).get("max_single_industry", 0.35)),
                    max_turnover_per_rebalance=float(
                        cfg.get("portfolio", {}).get("max_turnover_per_rebalance", 0.1)
                    ),
                    max_tactical_tilt=float(cfg.get("portfolio", {}).get("max_tactical_tilt", 0.05)),
                    min_confidence=float(cfg.get("portfolio", {}).get("min_confidence", 0.35)),
                )
            )

    # 每类选一个代表品种作为示例持仓（真实价格乘以数量，权重后续按市值重算）
    classes = db.scalars(select(AssetClass)).all()
    by_parent = {c.code: c.parent_code for c in classes}
    def top_of(code: str) -> str:
        node, depth = code, 0
        while by_parent.get(node) and depth < 5:
            node = by_parent[node]
            depth += 1
        return node

    for class_code, weight in base_weights.items():
        member_codes = [c.code for c in classes if top_of(c.code) == class_code]
        instrument = db.scalar(
            select(Instrument)
            .where(Instrument.asset_class_code.in_(member_codes))
            .order_by(Instrument.id)
        )
        if instrument is None:
            continue
        exists = db.scalar(
            select(PortfolioHolding).where(
                PortfolioHolding.portfolio_id == portfolio.id,
                PortfolioHolding.instrument_id == instrument.id,
            )
        )
        if exists is not None:
            continue
        value = float(portfolio.total_value) * float(weight)
        db.add(
            PortfolioHolding(
                portfolio_id=portfolio.id,
                instrument_id=instrument.id,
                symbol=instrument.symbol,
                quantity=0.0,
                cost_price=0.0,
                market_value=value,
                weight=float(weight),
                source="example",
            )
        )

    # 策略参数版本记录
    if db.scalar(select(StrategyConfig).where(StrategyConfig.code == "score_tilt")) is None:
        db.add(
            StrategyConfig(
                code="score_tilt",
                name="评分倾斜 + 约束投影",
                version=1,
                params={
                    "max_tilt": cfg.get("portfolio", {}).get("max_tactical_tilt", 0.05),
                    "transaction_cost_bps": cfg.get("portfolio", {}).get("transaction_cost_bps", 8),
                    "rebalance_days": cfg.get("portfolio", {}).get("rebalance_days", 20),
                },
                active=True,
                note="基础权重 + 评分倾斜，经上下限与集中度约束投影后归一化。",
            )
        )

    for key, description, value in [
        ("notifications", "通知阈值与渠道", {"min_importance": 72, "channels": ["inapp"], "dedupe_hours": 12}),
        ("data_retention", "数据保留策略", {"market_days": 900, "news_days": 180, "runs_days": 60}),
        ("ai_budget", "AI 预算", {"daily_usd": 0.5, "monthly_usd": 10.0, "enabled": False}),
        ("scheduler", "调度开关", {"enabled": True}),
    ]:
        if db.scalar(select(SystemSetting).where(SystemSetting.key == key)) is None:
            db.add(SystemSetting(key=key, value=value, description=description))

    db.flush()
    return portfolio


def seed_all(db: Session) -> dict:
    counts = {
        "asset_classes": seed_asset_classes(db),
        "instruments": seed_instruments(db),
        "data_sources": seed_data_sources(db),
    }
    db.commit()
    portfolio = seed_example_portfolio(db)
    db.commit()
    counts["portfolio_id"] = portfolio.id
    counts["seeded_at"] = now_cn().isoformat(sep=" ", timespec="seconds")
    return counts
