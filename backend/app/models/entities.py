"""SQLAlchemy 数据模型。

设计原则：
1. 原始证据（raw_documents / event_evidence）、事件结论（events，带 analysis_version）
   和历史信号（asset_signals）分开存储，新结论不覆盖旧判断。
2. 所有行情与宏观数据都带 source_code 与采集时间，支持追溯。
3. 唯一约束保证重复采集不会写入重复记录（幂等）。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.timeutil import now_cn


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=now_cn, onupdate=now_cn, nullable=False
    )


# --------------------------------------------------------------------------
# 资产目录
# --------------------------------------------------------------------------
class AssetClass(Base, TimestampMixin):
    __tablename__ = "asset_classes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    level: Mapped[int] = mapped_column(Integer, default=1)  # 1=一级 2=二级
    parent_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    description: Mapped[str] = mapped_column(Text, default="")
    analysis_module: Mapped[str] = mapped_column(String(32), default="generic")
    tradable: Mapped[bool] = mapped_column(Boolean, default=True)
    default_min_weight: Mapped[float] = mapped_column(Float, default=0.0)
    default_max_weight: Mapped[float] = mapped_column(Float, default=1.0)
    liquidity_tier: Mapped[str] = mapped_column(String(16), default="high")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class Instrument(Base, TimestampMixin):
    __tablename__ = "instrument_master"
    __table_args__ = (
        UniqueConstraint("symbol", name="uq_instrument_symbol"),
        Index("ix_instrument_class_status", "asset_class_code", "data_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(96))
    asset_class_code: Mapped[str] = mapped_column(String(32), index=True)
    market: Mapped[str] = mapped_column(String(24), default="CN")
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    instrument_type: Mapped[str] = mapped_column(String(32), default="index")
    # normal / delayed / missing / manual / restricted / unverified
    data_status: Mapped[str] = mapped_column(String(16), default="missing")
    data_source_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    provider_symbol: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    analysis_module: Mapped[str] = mapped_column(String(32), default="generic")
    risk_level: Mapped[str] = mapped_column(String(16), default="medium")
    liquidity_tier: Mapped[str] = mapped_column(String(16), default="high")
    qualification_required: Mapped[str] = mapped_column(String(32), default="无")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    last_data_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class InstrumentAlias(Base):
    __tablename__ = "instrument_aliases"
    __table_args__ = (UniqueConstraint("instrument_id", "alias", "source", name="uq_alias"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument_master.id", ondelete="CASCADE"))
    alias: Mapped[str] = mapped_column(String(96), index=True)
    alias_type: Mapped[str] = mapped_column(String(24), default="keyword")
    source: Mapped[str] = mapped_column(String(32), default="manual")


# --------------------------------------------------------------------------
# 数据源
# --------------------------------------------------------------------------
class DataSource(Base, TimestampMixin):
    __tablename__ = "data_sources"
    __table_args__ = (UniqueConstraint("code", name="uq_source_code"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(24))  # market/macro/news/disclosure/fund/alt
    provider: Mapped[str] = mapped_column(String(48), default="")
    endpoint: Mapped[str] = mapped_column(Text, default="")
    auth_type: Mapped[str] = mapped_column(String(24), default="none")
    requires_key: Mapped[bool] = mapped_column(Boolean, default=False)
    rate_limit: Mapped[str] = mapped_column(String(96), default="")
    terms_url: Mapped[str] = mapped_column(Text, default="")
    coverage: Mapped[str] = mapped_column(Text, default="")
    # healthy / degraded / unconfigured / disabled / error
    status: Mapped[str] = mapped_column(String(16), default="unconfigured")
    status_message: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=60)
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    last_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class DataSourceRun(Base):
    __tablename__ = "data_source_runs"
    __table_args__ = (Index("ix_run_source_started", "source_code", "started_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_code: Mapped[str] = mapped_column(String(32), index=True)
    job_name: Mapped[str] = mapped_column(String(48), index=True)
    trigger: Mapped[str] = mapped_column(String(16), default="schedule")  # schedule/manual/api
    status: Mapped[str] = mapped_column(String(16), default="running")  # success/partial/failed/running
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    records: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


# --------------------------------------------------------------------------
# 行情 / 宏观
# --------------------------------------------------------------------------
class MarketObservation(Base):
    __tablename__ = "market_observations"
    __table_args__ = (
        UniqueConstraint("symbol", "trade_date", "source_code", name="uq_market_obs"),
        Index("ix_market_symbol_date", "symbol", "trade_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    # Phase 1：来源与复权口径。旧数据为 legacy_em / qfq_unverified（当时未记录，无法自证）
    source_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    adjustment_type: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)
    open: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    high: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    low: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    close: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    prev_close: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    volume: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    change_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # close / realtime / delayed
    data_type: Mapped[str] = mapped_column(String(16), default="close")
    currency: Mapped[str] = mapped_column(String(8), default="CNY")
    source_code: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str] = mapped_column(Text, default="")
    observed_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)


class MacroObservation(Base):
    __tablename__ = "macro_observations"
    __table_args__ = (
        UniqueConstraint("indicator_code", "period", "source_code", name="uq_macro_obs"),
        Index("ix_macro_indicator_period", "indicator_code", "period"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    indicator_code: Mapped[str] = mapped_column(String(48), index=True)
    name: Mapped[str] = mapped_column(String(96))
    country: Mapped[str] = mapped_column(String(24), default="CN")
    unit: Mapped[str] = mapped_column(String(24), default="")
    period: Mapped[str] = mapped_column(String(24))  # 数据所属期间：2024 / 2026-08
    period_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    release_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)
    value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    previous_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    forecast_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    forecast_source: Mapped[str] = mapped_column(String(64), default="")
    revised: Mapped[bool] = mapped_column(Boolean, default=False)
    source_code: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str] = mapped_column(Text, default="")


# --------------------------------------------------------------------------
# 新闻与事件
# --------------------------------------------------------------------------
class RawDocument(Base):
    __tablename__ = "raw_documents"
    __table_args__ = (
        UniqueConstraint("source_code", "external_id", name="uq_doc_external"),
        Index("ix_doc_hash", "content_hash"),
        Index("ix_doc_published", "published_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_code: Mapped[str] = mapped_column(String(32), index=True)
    external_id: Mapped[str] = mapped_column(String(96))
    title: Mapped[str] = mapped_column(String(512))
    summary: Mapped[str] = mapped_column(Text, default="")
    url: Mapped[str] = mapped_column(Text, default="")
    media: Mapped[str] = mapped_column(String(96), default="")
    language: Mapped[str] = mapped_column(String(8), default="zh")
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    title_hash: Mapped[str] = mapped_column(String(64), index=True)
    cluster_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    event_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    reliability: Mapped[str] = mapped_column(String(16), default="medium")
    status: Mapped[str] = mapped_column(String(16), default="new")  # new/clustered/analyzed/ignored
    raw_payload: Mapped[dict] = mapped_column(JSON, default=dict)


class Event(Base, TimestampMixin):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("event_key", name="uq_event_key"),
        Index("ix_event_published", "published_at"),
        Index("ix_event_cat_importance", "category", "importance"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_key: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(512))
    summary: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(32), default="other", index=True)
    occurred_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)
    countries: Mapped[list] = mapped_column(JSON, default=list)
    institutions: Mapped[list] = mapped_column(JSON, default=list)
    companies: Mapped[list] = mapped_column(JSON, default=list)
    industries: Mapped[list] = mapped_column(JSON, default=list)

    importance: Mapped[float] = mapped_column(Float, default=0.0)  # 0-100
    novelty: Mapped[float] = mapped_column(Float, default=0.0)
    direction: Mapped[str] = mapped_column(String(16), default="unknown")  # bullish/bearish/mixed/unknown
    signal_strength: Mapped[float] = mapped_column(Float, default=0.0)  # -1 ~ 1
    confidence: Mapped[float] = mapped_column(Float, default=0.0)  # 0-1 证据置信度
    data_quality: Mapped[float] = mapped_column(Float, default=0.0)
    source_count: Mapped[int] = mapped_column(Integer, default=0)
    evidence_count: Mapped[int] = mapped_column(Integer, default=0)
    reliability: Mapped[str] = mapped_column(String(16), default="medium")
    horizon: Mapped[str] = mapped_column(String(16), default="unknown")  # short/mid/long/unknown
    priced_in: Mapped[str] = mapped_column(String(16), default="unknown")
    status: Mapped[str] = mapped_column(String(16), default="auto")  # auto/reviewed/archived
    analysis_version: Mapped[int] = mapped_column(Integer, default=1)
    analyzed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    analysis_engine: Mapped[str] = mapped_column(String(24), default="rule")  # rule/llm
    analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    transmission: Mapped[list] = mapped_column(JSON, default=list)
    counter_evidence: Mapped[list] = mapped_column(JSON, default=list)
    invalidation: Mapped[str] = mapped_column(Text, default="")
    what_happened: Mapped[str] = mapped_column(Text, default="")
    what_uncertain: Mapped[str] = mapped_column(Text, default="")
    what_changed: Mapped[str] = mapped_column(Text, default="")
    watch_items: Mapped[list] = mapped_column(JSON, default=list)


class EventEvidence(Base):
    __tablename__ = "event_evidence"
    __table_args__ = (UniqueConstraint("event_id", "document_id", name="uq_evidence"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    stance: Mapped[str] = mapped_column(String(16), default="support")
    source_name: Mapped[str] = mapped_column(String(96), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    quote: Mapped[str] = mapped_column(Text, default="")
    reliability: Mapped[str] = mapped_column(String(16), default="medium")
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)


class EventImpact(Base):
    __tablename__ = "event_impacts"
    __table_args__ = (UniqueConstraint("event_id", "instrument_symbol", name="uq_impact"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    instrument_symbol: Mapped[str] = mapped_column(String(32), index=True)
    asset_class_code: Mapped[str] = mapped_column(String(32), default="")
    direction: Mapped[str] = mapped_column(String(16), default="unknown")
    mechanism: Mapped[str] = mapped_column(Text, default="")
    horizon: Mapped[str] = mapped_column(String(16), default="unknown")
    magnitude: Mapped[float] = mapped_column(Float, default=0.0)  # -1 ~ 1
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)


class EventAnalysisVersion(Base):
    """事件结论版本快照：新结论不覆盖旧判断，可追溯每次变化原因。"""

    __tablename__ = "event_analysis_versions"
    __table_args__ = (UniqueConstraint("event_id", "version", name="uq_event_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    engine: Mapped[str] = mapped_column(String(24), default="rule")
    direction: Mapped[str] = mapped_column(String(16), default="unknown")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    importance: Mapped[float] = mapped_column(Float, default=0.0)
    analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    change_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)


class AssetSignal(Base):
    __tablename__ = "asset_signals"
    __table_args__ = (
        UniqueConstraint("instrument_id", "as_of", "version", name="uq_signal"),
        Index("ix_signal_asof", "as_of"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument_master.id"), index=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    asset_class_code: Mapped[str] = mapped_column(String(32), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    fundamental: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    valuation: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trend: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    macro: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    liquidity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    catalyst: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    risk: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    data_quality: Mapped[float] = mapped_column(Float, default=0.0)
    total_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    action: Mapped[str] = mapped_column(String(24), default="暂不判断")
    regime: Mapped[str] = mapped_column(String(24), default="数据不足")
    components: Mapped[dict] = mapped_column(JSON, default=dict)
    rationale: Mapped[dict] = mapped_column(JSON, default=dict)
    missing_inputs: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)


# --------------------------------------------------------------------------
# 组合与配置
# --------------------------------------------------------------------------
class Portfolio(Base, TimestampMixin):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    owner: Mapped[str] = mapped_column(String(48), default="local")
    base_currency: Mapped[str] = mapped_column(String(8), default="CNY")
    total_value: Mapped[float] = mapped_column(Float, default=0.0)
    cash_value: Mapped[float] = mapped_column(Float, default=0.0)
    liabilities: Mapped[float] = mapped_column(Float, default=0.0)
    is_example: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")


class PortfolioHolding(Base, TimestampMixin):
    __tablename__ = "portfolio_holdings"
    __table_args__ = (UniqueConstraint("portfolio_id", "instrument_id", name="uq_holding"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instrument_master.id"))
    symbol: Mapped[str] = mapped_column(String(32))
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    cost_price: Mapped[float] = mapped_column(Float, default=0.0)
    market_value: Mapped[float] = mapped_column(Float, default=0.0)
    weight: Mapped[float] = mapped_column(Float, default=0.0)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual/csv


class AllocationTarget(Base):
    __tablename__ = "allocation_targets"
    __table_args__ = (Index("ix_alloc_portfolio_asof", "portfolio_id", "as_of"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"))
    asset_class_code: Mapped[str] = mapped_column(String(32), index=True)
    base_weight: Mapped[float] = mapped_column(Float, default=0.0)
    tactical_weight: Mapped[float] = mapped_column(Float, default=0.0)
    target_weight: Mapped[float] = mapped_column(Float, default=0.0)
    prev_weight: Mapped[float] = mapped_column(Float, default=0.0)
    delta: Mapped[float] = mapped_column(Float, default=0.0)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[dict] = mapped_column(JSON, default=dict)
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)
    as_of: Mapped[date] = mapped_column(Date, default=date.today, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    applied: Mapped[bool] = mapped_column(Boolean, default=False)


class RiskConstraint(Base, TimestampMixin):
    __tablename__ = "risk_constraints"
    __table_args__ = (UniqueConstraint("portfolio_id", "asset_class_code", name="uq_risk_constraint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"))
    asset_class_code: Mapped[str] = mapped_column(String(32))
    min_weight: Mapped[float] = mapped_column(Float, default=0.0)
    max_weight: Mapped[float] = mapped_column(Float, default=1.0)
    max_single_instrument: Mapped[float] = mapped_column(Float, default=0.25)
    max_industry: Mapped[float] = mapped_column(Float, default=0.35)
    max_turnover_per_rebalance: Mapped[float] = mapped_column(Float, default=0.10)
    max_tactical_tilt: Mapped[float] = mapped_column(Float, default=0.05)
    min_confidence: Mapped[float] = mapped_column(Float, default=0.35)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class InvestorProfile(Base, TimestampMixin):
    __tablename__ = "investor_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"))
    risk_tolerance: Mapped[str] = mapped_column(String(16), default="balanced")
    horizon_years: Mapped[float] = mapped_column(Float, default=3.0)
    max_drawdown: Mapped[float] = mapped_column(Float, default=0.15)
    liquidity_need_ratio: Mapped[float] = mapped_column(Float, default=0.10)
    emergency_cash_months: Mapped[float] = mapped_column(Float, default=6.0)
    monthly_income: Mapped[float] = mapped_column(Float, default=0.0)
    monthly_expense: Mapped[float] = mapped_column(Float, default=0.0)
    allowed_classes: Mapped[list] = mapped_column(JSON, default=list)
    excluded_symbols: Mapped[list] = mapped_column(JSON, default=list)
    qualifications: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str] = mapped_column(Text, default="")
    confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class StrategyConfig(Base, TimestampMixin):
    __tablename__ = "strategy_configs"
    __table_args__ = (UniqueConstraint("code", "version", name="uq_strategy_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(48), index=True)
    name: Mapped[str] = mapped_column(String(96), default="")
    version: Mapped[int] = mapped_column(Integer, default=1)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str] = mapped_column(Text, default="")


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    strategy_code: Mapped[str] = mapped_column(String(48), default="score_tilt")
    status: Mapped[str] = mapped_column(String(16), default="running")
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    equity_curve: Mapped[list] = mapped_column(JSON, default=list)
    benchmark_curve: Mapped[list] = mapped_column(JSON, default=list)
    rebalances: Mapped[list] = mapped_column(JSON, default=list)
    assumptions: Mapped[dict] = mapped_column(JSON, default=dict)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


# --------------------------------------------------------------------------
# 运维
# --------------------------------------------------------------------------
class ModelUsage(Base):
    __tablename__ = "model_usage"
    __table_args__ = (Index("ix_usage_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(48), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    task: Mapped[str] = mapped_column(String(48), default="")
    event_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    cached: Mapped[bool] = mapped_column(Boolean, default=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_notification_dedupe"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel: Mapped[str] = mapped_column(String(24), default="inapp")
    level: Mapped[str] = mapped_column(String(16), default="info")  # info/warning/critical
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text, default="")
    event_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(96), index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending/sent/failed
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
    description: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn, onupdate=now_cn)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_created", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor: Mapped[str] = mapped_column(String(48), default="system")
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(96), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)


class JobRun(Base):
    """调度任务执行记录（与数据源采集记录分开，便于分别查看）。"""

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[str] = mapped_column(String(48), index=True)
    trigger: Mapped[str] = mapped_column(String(16), default="schedule")
    status: Mapped[str] = mapped_column(String(16), default="running")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    records: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class EtfUniverse(Base, TimestampMixin):
    """ETF 候选池（C 层）。

    全市场 ETF 均登记，但只有通过分类门槛与同指数去重的记录才是 candidate；
    被拒绝的保留在库中并写明原因，便于追溯与调参，不做物理删除。
    """

    __tablename__ = "etf_universe"
    __table_args__ = (
        UniqueConstraint("code", name="uq_etf_code"),
        Index("ix_etf_status", "status"),
        Index("ix_etf_category_status", "category", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    symbol: Mapped[str] = mapped_column(String(12), index=True)
    name: Mapped[str] = mapped_column(String(96))
    market: Mapped[str] = mapped_column(String(4), default="SH")
    # broad / sector / cross_border / bond / money / commodity / other
    category: Mapped[str] = mapped_column(String(24), default="other", index=True)
    tracked_index: Mapped[str] = mapped_column(String(96), default="")
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    change_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    amount_20d: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    amount_basis: Mapped[str] = mapped_column(String(8), default="1d")
    mktcap: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    endnav: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    nav: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    premium_est: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # 折溢价口径：prev_close=前收盘对净值（同一时点），price=实时价对净值（会混入当日涨跌）
    premium_basis: Mapped[str] = mapped_column(String(16), default="")
    list_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    listed_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="rejected", index=True)
    reject_reasons: Mapped[list] = mapped_column(JSON, default=list)
    data_coverage: Mapped[dict] = mapped_column(JSON, default=dict)
    quality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    quality_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    quality_breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class TradeJournal(Base, TimestampMixin):
    """交易日志：记录「系统建议 → 是否执行 → 实际成交 → 结果」。

    这张表存在的唯一理由：区分「策略错了」和「我没执行」。
    没有 decision 字段，两者永远分不清，也就无法判断系统到底有没有用。

    - decision=followed    照信号执行
    - decision=skipped     信号出现但没做（要能在统计里体现"如果做了会怎样"）
    - decision=manual      自己临时决定，与信号无关
    """

    __tablename__ = "trade_journal"
    __table_args__ = (
        Index("ix_journal_status_time", "status", "opened_at"),
        Index("ix_journal_symbol", "symbol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(96), default="")
    asset_class_code: Mapped[str] = mapped_column(String(32), default="")
    portfolio_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 信号快照：记录下单当时系统是怎么说的，事后不能改
    source: Mapped[str] = mapped_column(String(16), default="manual")  # radar/plan/manual
    signal_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    signal_state: Mapped[str] = mapped_column(String(16), default="")
    decision: Mapped[str] = mapped_column(String(16), default="manual", index=True)

    action: Mapped[str] = mapped_column(String(8), default="buy")
    planned_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    actual_price: Mapped[float] = mapped_column(Float, default=0.0)
    quantity: Mapped[float] = mapped_column(Float, default=0.0)
    amount: Mapped[float] = mapped_column(Float, default=0.0)
    fee: Mapped[float] = mapped_column(Float, default=0.0)

    thesis: Mapped[str] = mapped_column(Text, default="")       # 买入理由
    invalidation: Mapped[str] = mapped_column(Text, default="")  # 失效条件，下单时就写
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)

    opened_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)
    closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[str] = mapped_column(String(32), default="")
    pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    pnl_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    holding_days: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")


class RadarSnapshot(Base):
    """雷达每日快照：用于回答「相比上期变化」「首次发现时间」。

    没有快照就只能说"今天 73 分"，说不出"从 68 涨到 73"或"已连续 5 天候选"，
    而后者恰恰是判断信号是否稳定的关键。
    """

    __tablename__ = "radar_snapshots"
    __table_args__ = (
        UniqueConstraint("as_of", "code", name="uq_radar_snapshot"),
        Index("ix_radar_code_date", "code", "as_of"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    code: Mapped[str] = mapped_column(String(6), index=True)
    name: Mapped[str] = mapped_column(String(96), default="")
    state: Mapped[str] = mapped_column(String(20), default="")
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    quality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)
