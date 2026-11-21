"""ETF 历史数据补齐与质量分落库。

职责：
1. 为候选池补齐日线（写入 market_observations，instrument_id 留空）。
   候选池是「研究范围」，不等于资产目录的精管品种，因此不往 instrument_master 里塞 250 条。
2. 拉取历史净值，计算折溢价稳定性。
3. 计算质量分并写回 etf_universe。

成交额口径说明（重要）：
腾讯日线的 volume 单位是「手」，新浪是「股」，相差 100 倍，用日线反推成交额
会得到数量级错误的结果。因此流动性指标直接使用东方财富给出的当日成交额，
amount_basis 保持 "1d" 并在页面标注；20 日均额留待统一日线口径后再升级。
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.timeutil import now_cn
from app.models.entities import DataSource, DataSourceRun, EtfUniverse, MarketObservation
from app.providers.eastmoney import EastmoneyKlineProvider
from app.providers.etf_universe import FundNavProvider
from app.services.quality_service import compute_quality, premium_median_abs


async def _fetch_one(
    kline: EastmoneyKlineProvider,
    nav: FundNavProvider,
    symbol: str,
    code: str,
    bar_limit: int,
    semaphore: asyncio.Semaphore,
    want_nav: bool,
) -> tuple[str, list, list[tuple[date, float]], Optional[str]]:
    async with semaphore:
        bars: list = []
        source: Optional[str] = None
        error: Optional[str] = None
        try:
            res = await kline.fetch_history(symbol, limit=bar_limit)
            bars = list(res.detail.get("bars") or [])
            source = res.detail.get("source_code")
            if not res.ok and res.message:
                error = res.message[:120]
        except Exception as exc:  # noqa: BLE001
            error = str(exc)[:120]

        navs: list[tuple[date, float]] = []
        if want_nav:
            try:
                navs = await nav.fetch_nav_history(code, limit=90)
            except Exception:  # noqa: BLE001
                navs = []
        # 来源与错误必须分开返回：之前用 `error or source` 二选一，
        # 导致调用方无法区分"失败原因"和"实际来源"。
        return symbol, bars, navs, source or "eastmoney_kline", error


def _record_source_runs(db: Session, per_source: dict[str, int]) -> None:
    now = now_cn()
    for code, count in per_source.items():
        source = db.scalar(select(DataSource).where(DataSource.code == code))
        if source is None:
            continue
        source.last_run_at = now
        source.last_success_at = now
        source.status = "healthy"
        source.status_message = f"ETF 候选池历史补齐：{count} 条日线"
        db.add(
            DataSourceRun(
                source_code=code,
                job_name="etf_quality_backfill",
                trigger="manual",
                status="success",
                started_at=now,
                finished_at=now,
                duration_ms=0,
                records=count,
                success_count=1,
                failed_count=0,
            )
        )
    db.commit()


def refresh_quality(
    db: Session,
    bar_limit: int = 260,
    concurrency: int = 4,
    persist_bars: bool = True,
) -> dict:
    """补齐候选池历史并计算质量分。"""
    candidates = db.scalars(
        select(EtfUniverse).where(EtfUniverse.status == "candidate")
    ).all()
    if not candidates:
        return {"ok": False, "message": "候选池为空，请先刷新 ETF 候选池", "scored": 0}

    kline = EastmoneyKlineProvider()
    nav = FundNavProvider()
    semaphore = asyncio.Semaphore(concurrency)

    async def run_all():
        return await asyncio.gather(
            *[
                _fetch_one(
                    kline,
                    nav,
                    row.symbol,
                    row.code,
                    bar_limit,
                    semaphore,
                    want_nav=row.category != "money",
                )
                for row in candidates
            ]
        )

    fetched = asyncio.run(run_all())

    bars_by_symbol: dict[str, list] = {}
    source_by_symbol: dict[str, str] = {}
    navs_by_code: dict[str, dict[date, float]] = {}
    errors: list[str] = []
    inserted = 0
    # 按「本次实际拉取量」统计，而不是「新增写入量」：
    # 幂等重跑时 inserted=0，若按写入量统计，来源状态会永远停在 idle。
    used_by_source: dict[str, int] = {}

    for (symbol, bars, navs, source_code, error), row in zip(fetched, candidates):
        if bars:
            bars_by_symbol[symbol] = bars
            # 记录真实来源（东方财富/腾讯/新浪），不写任务名
            source_by_symbol[symbol] = source_code
            used_by_source[source_code] = used_by_source.get(source_code, 0) + len(bars)
        navs_by_code[row.code] = {d: v for d, v in navs}
        if error:
            errors.append(f"{row.code}: {error}")

    if persist_bars:
        for symbol, bars in bars_by_symbol.items():
            existing = set(
                db.scalars(
                    select(MarketObservation.trade_date).where(
                        MarketObservation.symbol == symbol
                    )
                ).all()
            )
            for bar in bars:
                if bar.trade_date in existing:
                    continue
                db.add(
                    MarketObservation(
                        instrument_id=None,
                        symbol=symbol,
                        trade_date=bar.trade_date,
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        volume=bar.volume,
                        data_type="close",
                        currency="CNY",
                        source_code=source_by_symbol.get(symbol, "eastmoney_kline"),
                        source_url="",
                        observed_at=now_cn(),
                    )
                )
                inserted += 1
        db.commit()

    # 组装质量分输入：日线用于折溢价与历史长度，成交额沿用快照的精确值
    items: list[dict] = []
    for row in candidates:
        bars = bars_by_symbol.get(row.symbol, [])
        premium = None
        samples = 0
        if row.category != "money" and bars:
            premium, samples = premium_median_abs(
                [(bar.trade_date, bar.close) for bar in bars],
                navs_by_code.get(row.code, {}),
            )
        row.amount_20d = row.amount  # 口径仍是 1 日，字段保留供后续升级
        row.amount_basis = "1d"
        items.append(
            {
                "code": row.code,
                "category": row.category,
                "amount_20d": row.amount,
                "amount_basis": row.amount_basis,
                "mktcap": row.mktcap,
                "premium_median_abs": premium,
                "bar_count": len(bars),
                "premium_samples": samples,
            }
        )

    # 回退链里实际用到的来源也要落到 data_sources，否则页面显示 idle，
    # 但库里已有几万条数据——状态与实际不符同样是"来源不明"。
    if used_by_source:
        _record_source_runs(db, used_by_source)

    scores = compute_quality(items)

    scored = 0
    for row in candidates:
        result = scores.get(row.code)
        if not result:
            continue
        row.quality_score = result["quality_score"]
        row.quality_confidence = result["quality_confidence"]
        row.quality_breakdown = result["quality_breakdown"]
        if result["quality_score"] is not None:
            scored += 1
    db.commit()

    return {
        "ok": True,
        "candidates": len(candidates),
        "bars_inserted": inserted,
        "scored": scored,
        "no_score": len(candidates) - scored,
        "errors": errors[:8],
        "message": (
            f"{scored}/{len(candidates)} 只候选已生成质量分，新增日线 {inserted} 条"
            f"（流动性口径 1 日，未使用日线反推成交额）"
        ),
    }
