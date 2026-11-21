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
from dataclasses import replace
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


def _load_via_investdata(candidates: list, days: int) -> dict:
    """用 InvestData 取日线、用天天基金取净值与规模。

    这是摆脱 push2his 的路径：push2his 高频会被封 IP，
    而 fund.eastmoney.com 是独立域名，实测可用。
    """
    from app.datasource import get_repository
    from app.providers.tiantian_fund import TiantianFundProvider

    repo = get_repository()
    fund = TiantianFundProvider()
    out: dict[str, dict] = {}

    for row in candidates:
        code = row.code
        bars = []
        try:
            iid = repo.resolve(code, instrument_type="etf")
            series = repo.bars(iid, days=days, adjustment="qfq")
            # InvestData 的 trade_date 是字符串，而净值序列的键是 date 对象。
            # 不统一类型，折溢价就永远匹配不上（实测 premium 会全是 None）。
            bars = [
                replace(b, trade_date=date.fromisoformat(str(b.trade_date)))
                if not isinstance(b.trade_date, date) else b
                for b in series.bars
            ]
        except Exception as exc:  # noqa: BLE001
            out[code] = {"bars": [], "navs": [], "scale": None,
                         "error": f"行情失败: {str(exc)[:60]}"}
            continue

        info = {"bars": bars, "navs": [], "scale": None, "error": None}
        if row.category != "money":
            nav = fund.fetch(code)
            if nav.get("ok"):
                info["navs"] = nav["navs"]
                info["scale"] = nav.get("scale")
            else:
                info["error"] = f"净值失败: {nav.get('error')}"
        out[code] = info

    fund.close()
    return out


def refresh_quality(
    db: Session,
    bar_limit: int = 260,
    concurrency: int = 4,
    persist_bars: bool = True,
    prefer: str = "investdata",
) -> dict:
    """补齐候选池历史并计算质量分。

    prefer="investdata"（默认）：日线走 InvestData，净值与规模走天天基金。
    prefer="eastmoney"：旧路径，日线走 eastmoney_kline、净值走 FundNavProvider。
    push2his 高频会被封 IP，因此默认不再使用它。
    """
    candidates = db.scalars(
        select(EtfUniverse).where(EtfUniverse.status == "candidate")
    ).all()
    if not candidates:
        return {"ok": False, "message": "候选池为空，请先刷新 ETF 候选池", "scored": 0}

    bars_by_symbol: dict[str, list] = {}
    source_by_symbol: dict[str, str] = {}
    navs_by_code: dict[str, dict[date, float]] = {}
    scale_by_code: dict[str, float] = {}
    errors: list[str] = []
    inserted = 0
    # 按「本次实际拉取量」统计，而不是「新增写入量」：
    # 幂等重跑时 inserted=0，若按写入量统计，来源状态会永远停在 idle。
    used_by_source: dict[str, int] = {}

    if prefer == "investdata":
        loaded = _load_via_investdata(candidates, bar_limit)
        for row in candidates:
            info = loaded.get(row.code) or {}
            bars = info.get("bars") or []
            if bars:
                bars_by_symbol[row.symbol] = bars
                source_by_symbol[row.symbol] = "investdata_kline"
                used_by_source["investdata_kline"] = used_by_source.get("investdata_kline", 0) + len(bars)
            navs_by_code[row.code] = {d: v for d, v in (info.get("navs") or [])}
            if info.get("scale"):
                scale_by_code[row.code] = info["scale"]
            if info.get("error"):
                errors.append(f"{row.code}: {info['error']}")
    else:
        kline = EastmoneyKlineProvider()
        nav = FundNavProvider()
        semaphore = asyncio.Semaphore(concurrency)

        async def run_all():
            return await asyncio.gather(
                *[
                    _fetch_one(
                        kline, nav, row.symbol, row.code, bar_limit,
                        semaphore, want_nav=row.category != "money",
                    )
                    for row in candidates
                ]
            )

        fetched = asyncio.run(run_all())
        for (symbol, bars, navs, source_code, error), row in zip(fetched, candidates):
            if bars:
                bars_by_symbol[symbol] = bars
                source_by_symbol[symbol] = source_code
                used_by_source[source_code] = used_by_source.get(source_code, 0) + len(bars)
            navs_by_code[row.code] = {d: v for d, v in navs}
            if error:
                errors.append(f"{row.code}: {error}")

    if persist_bars:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        for symbol, bars in bars_by_symbol.items():
            src = source_by_symbol.get(symbol, "investdata_kline")
            rows = []
            for bar in bars:
                # InvestData 的 trade_date 是字符串，东财是 date，统一成 date
                td = bar.trade_date
                if not isinstance(td, date):
                    td = date.fromisoformat(str(td))
                rows.append({
                    "instrument_id": None,
                    "symbol": symbol,
                    "trade_date": td,
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": getattr(bar, "volume", None),
                    "data_type": "close",
                    "currency": "CNY",
                    "source_code": src,
                    "source_url": "",
                    "observed_at": now_cn(),
                    "source_provider": "investdata" if src == "investdata_kline" else "legacy_em",
                    "adjustment_type": "qfq",
                })
            if not rows:
                continue
            stmt = sqlite_insert(MarketObservation).values(rows)
            stmt = stmt.on_conflict_do_update(
                index_elements=["symbol", "trade_date", "source_code"],
                set_={"close": stmt.excluded.close, "observed_at": stmt.excluded.observed_at},
            )
            db.execute(stmt)
            inserted += len(rows)
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
        # 成交额：优先用日线近 20 日均值（比单日快照稳定），无日线时退回快照值
        amounts = [getattr(b, "amount", None) for b in bars[-20:]]
        amounts = [a for a in amounts if a]
        if amounts:
            row.amount_20d = sum(amounts) / len(amounts)
            row.amount_basis = "20d"
        else:
            row.amount_20d = row.amount
            row.amount_basis = "1d"
        # 规模：天天基金季度规模（亿元），优先于 etf_universe 里的旧值
        scale = scale_by_code.get(row.code)
        if scale:
            row.mktcap = scale
        items.append(
            {
                "code": row.code,
                "category": row.category,
                "amount_20d": row.amount_20d,
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
