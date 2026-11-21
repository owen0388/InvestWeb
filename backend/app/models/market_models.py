"""全市场标的表（Phase 1 新增）。

刻意与现有 `instrument_master` 分开：
- `instrument_master` 是 InvestWeb 精管的 61 个研究标的，被评分/雷达/回测依赖，不能动
- 本表是全市场 1 万+ 标的的同步镜像，只用于识别与索引

两者通过 `symbol + instrument_type` 关联，通过 `instrument_id` 与外部行情对齐。

关键约束：`symbol` 不是唯一键。`000001` 同时是平安银行（SZ）与上证指数（SH），
唯一识别必须用 `instrument_id`（InvestData 的 UUID）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.timeutil import now_cn


class MarketInstrument(Base):
    __tablename__ = "market_instruments"
    __table_args__ = (
        # 同一市场内的同代码同类型只应有一条；允许不同市场同代码共存
        UniqueConstraint("symbol", "instrument_type", "market", name="uq_market_instrument"),
        Index("ix_market_instruments_type", "instrument_type"),
        Index("ix_market_instruments_symbol", "symbol"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # InvestData 的 UUID，唯一识别依据
    instrument_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False, index=True)
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # stock / etf / index / industry_index / concept_index（沿用 InvestData 枚举，不重新定义）
    instrument_type: Mapped[str] = mapped_column(String(20), nullable=False)
    market: Mapped[str | None] = mapped_column(String(8), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # active / inactive / delisted …（以 InvestData 为准，不自行映射）
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # 仅板块有：tdx_research / concept / style / region
    sector_type: Mapped[str | None] = mapped_column(String(24), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    listing_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    delisting_date: Mapped[str | None] = mapped_column(String(10), nullable=True)

    source: Mapped[str] = mapped_column(String(16), default="investdata")
    last_synced_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn, onupdate=now_cn)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)


class MarketSyncLog(Base):
    """标的同步日志。每次同步留一条记录，便于排查"为什么少了几只"。"""

    __tablename__ = "market_sync_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now_cn)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")
    total_fetched: Mapped[int] = mapped_column(default=0)
    inserted: Mapped[int] = mapped_column(default=0)
    updated: Mapped[int] = mapped_column(default=0)
    unchanged: Mapped[int] = mapped_column(default=0)
    # 每类的数量快照，JSON 字符串
    by_type: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
