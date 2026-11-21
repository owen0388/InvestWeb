"""InvestData → 本地行情同步。

Phase 1 选的是「同步到本地库」而不是「下游直连」，原因：
- 现有评分、雷达、回测全部读 market_observations，改读取路径风险面太大
- 同步后下游零改动即可吃到全市场数据

设计要点：
- 只同步能从 InvestData 映射到的 A 股标的（sh/sz 前缀）；
  海外指数、期货、港股、手工录入的标的没有对应数据源，原样保留不动
- 写入用 source_code='investdata_kline'，与旧数据（eastmoney_kline 等）并存，
  靠 UNIQUE(symbol, trade_date, source_code) 天然隔离
- load_close_series 会自动选「记录最多的来源」，InvestData 1212 根 > 东财 785 根，
  因此会自动成为主源，无需改动下游
- 只取最近 5 年（1212 根），与线上口径一致
"""
from __future__ import annotations

import json
from datetime import date
from typing import Optional

from sqlalchemy import select, func
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.timeutil import now_cn
from app.datasource.investdata import InvestDataClient
from app.models.entities import Instrument, MarketObservation

SOURCE_CODE = "investdata_kline"
ADJUSTMENT = "qfq"
MAX_DAYS = 1212  # 5 年，与线上库口径一致

# instrument_master.instrument_type → InvestData.instrument_type
TYPE_MAP = {
    "etf": "etf",
    "index": "index",
    "stock": "stock",
    "money_fund": "etf",
}

# 本地代码前缀 → 市场
MARKET_MAP = {"sh": "SH", "sz": "SZ", "bj": "BJ"}


def _resolve_target(instrument: Instrument) -> Optional[tuple[str, str, str]]:
    """把本地标的映射成 (纯代码, 市场, InvestData 类型)。无法映射返回 None。"""
    symbol = instrument.symbol or ""
    prefix = symbol[:2].lower()
    market = MARKET_MAP.get(prefix)
    if not market:
        return None  # 港股 hk00700 / 美股 gb_$dji / 期货 hf_GC / 手工 WM_CASH_01
    itype = TYPE_MAP.get(instrument.instrument_type)
    if not itype:
        return None
    return symbol[2:], market, itype


def sync_daily_bars(
    db: Session,
    client: Optional[InvestDataClient] = None,
    days: int = MAX_DAYS,
    incremental: bool = True,
) -> dict:
    """把 A 股标的的日线从 InvestData 同步到 market_observations。

    incremental=True 时只补本地缺失的日期；首次运行会全量拉 5 年。
    """
    own = client is None
    client = client or InvestDataClient(
        base_url=settings.INVESTDATA_BASE_URL, timeout=settings.INVESTDATA_TIMEOUT
    )
    days = min(days, MAX_DAYS)

    instruments = db.scalars(
        select(Instrument).where(Instrument.enabled.is_(True))
    ).all()

    stats = {"tried": 0, "synced": 0, "skipped_no_source": 0,
             "rows_inserted": 0, "rows_updated": 0, "errors": []}
    skipped: list[str] = []

    for inst in instruments:
        target = _resolve_target(inst)
        if target is None:
            stats["skipped_no_source"] += 1
            skipped.append(inst.symbol)
            continue

        code, market, itype = target
        stats["tried"] += 1
        try:
            info = client.resolve(code, instrument_type=itype)
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append(f"{inst.symbol}: {str(exc)[:60]}")
            continue

        try:
            series = client.bars(info["instrument_id"], days=days, adjustment=ADJUSTMENT)
        except Exception as exc:  # noqa: BLE001
            stats["errors"].append(f"{inst.symbol}: {str(exc)[:60]}")
            continue
        if not series.bars:
            stats["errors"].append(f"{inst.symbol}: 无返回数据")
            continue

        # 增量模式：找出本地已有最新日期，只写更新的部分
        cutoff = None
        if incremental:
            cutoff = db.scalar(
                select(func.max(MarketObservation.trade_date)).where(
                    MarketObservation.symbol == inst.symbol,
                    MarketObservation.source_code == SOURCE_CODE,
                )
            )

        rows = []
        for b in series.bars:
            if cutoff and b.trade_date <= str(cutoff):
                continue
            rows.append({
                "instrument_id": inst.id,
                "symbol": inst.symbol,
                "trade_date": date.fromisoformat(b.trade_date),
                "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                "prev_close": b.pre_close,
                "volume": b.volume, "amount": b.amount,
                "change_pct": b.change_pct,
                "data_type": "close",
                "currency": inst.currency or "CNY",
                "source_code": SOURCE_CODE,
                "source_url": settings.INVESTDATA_BASE_URL,
                "observed_at": now_cn(),
                "source_provider": "investdata",
                "adjustment_type": ADJUSTMENT,
            })

        if not rows:
            stats["synced"] += 1
            continue

        # 幂等写入：冲突时更新（重复跑同一天不会产生重复行）
        stmt = sqlite_insert(MarketObservation).values(rows)
        stmt = stmt.on_conflict_do_update(
            index_elements=["symbol", "trade_date", "source_code"],
            set_={
                "close": stmt.excluded.close,
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "prev_close": stmt.excluded.prev_close,
                "volume": stmt.excluded.volume,
                "amount": stmt.excluded.amount,
                "change_pct": stmt.excluded.change_pct,
                "observed_at": stmt.excluded.observed_at,
                "adjustment_type": stmt.excluded.adjustment_type,
            },
        )
        db.execute(stmt)
        db.commit()
        stats["rows_inserted"] += len(rows)
        stats["synced"] += 1

    stats["skipped_symbols"] = skipped[:20]
    return stats


def coverage_report(db: Session) -> dict:
    """各来源的覆盖情况，用于核对同步结果。"""
    rows = db.execute(
        select(
            MarketObservation.source_provider,
            func.count(func.distinct(MarketObservation.symbol)),
            func.count(),
            func.max(MarketObservation.trade_date),
        ).group_by(MarketObservation.source_provider)
    ).all()
    return {
        r[0] or "(未标记)": {"symbols": r[1], "rows": r[2], "latest": str(r[3])}
        for r in rows
    }
