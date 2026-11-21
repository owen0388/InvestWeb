"""采集编排：统一入口、任务记录、幂等写入、数据源健康状态更新。

原则：
- 单个数据源失败不影响其他数据源。
- 所有写入使用 (symbol, trade_date, source) 唯一键，重复采集只更新不重复插入。
- 页面展示的数据类型必须与接口实际能力一致（收盘/实时/延迟）。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.timeutil import now_cn
from app.models.entities import (
    DataSource,
    DataSourceRun,
    Instrument,
    MacroObservation,
    MarketObservation,
    RawDocument,
)
from app.providers.base import ProviderResult
from app.providers.eastmoney import (
    TENCENT_HISTORY_SYMBOLS,
    EastmoneyKlineProvider,
    EastmoneyNewsProvider,
)
from app.providers.macro import FredProvider, WorldBankProvider
from app.providers.sina_quote import SinaQuoteProvider
from app.services.news_pipeline import store_documents


def _now() -> "object":
    return now_cn()


class DataSourceManager:
    """负责运行记录与状态回写。"""

    def __init__(self, db: Session):
        self.db = db

    def start(self, source_code: str, job_name: str, trigger: str = "schedule") -> DataSourceRun:
        run = DataSourceRun(
            source_code=source_code,
            job_name=job_name,
            trigger=trigger,
            status="running",
            started_at=now_cn(),
        )
        self.db.add(run)
        source = self.db.scalar(select(DataSource).where(DataSource.code == source_code))
        if source:
            source.last_run_at = now_cn()
        self.db.commit()
        return run

    def finish(
        self,
        run: DataSourceRun,
        source_code: str,
        result: ProviderResult,
        trigger: str = "schedule",
    ) -> None:
        run.finished_at = now_cn()
        run.duration_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)
        run.records = result.records
        run.success_count = result.records
        run.failed_count = result.failed
        run.skipped_count = result.skipped
        run.error = ("; ".join(result.errors))[:2000] if result.errors else result.message[:2000]
        run.detail = {k: v for k, v in result.detail.items() if k not in ("quotes", "bars", "items", "points")}
        if result.ok and result.records > 0:
            run.status = "success"
        elif result.records > 0:
            run.status = "partial"
        elif result.skipped:
            run.status = "skipped"
        else:
            run.status = "failed"

        source = self.db.scalar(select(DataSource).where(DataSource.code == source_code))
        if source:
            source.last_error = run.error if run.status in ("failed", "partial") else ""
            source.status_message = result.message[:500]
            if run.status == "success":
                source.status = "healthy"
                source.last_success_at = now_cn()
            elif run.status == "partial":
                source.status = "degraded"
                if result.records:
                    source.last_success_at = now_cn()
            elif run.status == "skipped":
                source.status = "unconfigured"
            else:
                source.status = "error"
            source.next_run_at = now_cn() + timedelta(minutes=max(source.interval_minutes, 5))
        self.db.commit()


def _upsert_market(db: Session, **kwargs) -> bool:
    key = dict(
        symbol=kwargs["symbol"],
        trade_date=kwargs["trade_date"],
        source_code=kwargs["source_code"],
    )
    row = db.scalar(select(MarketObservation).where(*[getattr(MarketObservation, k) == v for k, v in key.items()]))
    created = row is None
    if created:
        row = MarketObservation(**kwargs)
        db.add(row)
    else:
        for field, value in kwargs.items():
            if value is not None:
                setattr(row, field, value)
    return created


def _update_instrument_freshness(db: Session, symbols: Iterable[str], status: str, when=None) -> None:
    for symbol in symbols:
        inst = db.scalar(select(Instrument).where(Instrument.symbol == symbol))
        if inst:
            inst.last_data_at = when or now_cn()
            if status:
                inst.data_status = status


def run_market_quotes(db: Session, trigger: str = "schedule") -> ProviderResult:
    manager = DataSourceManager(db)
    run = manager.start("sina_quote", "market_quotes", trigger)
    instruments = db.scalars(
        select(Instrument).where(
            Instrument.enabled.is_(True),
            Instrument.data_source_code == "sina_quote",
        )
    ).all()
    provider = SinaQuoteProvider()
    result = provider_quotes = None
    import asyncio

    try:
        result = asyncio.run(provider.fetch_quotes([i.provider_symbol or i.symbol for i in instruments]))
    except Exception as exc:  # noqa: BLE001
        result = ProviderResult(provider="sina_quote", ok=False, message=f"行情采集异常: {exc}"[:300])
    assert result is not None
    by_symbol = {q.symbol: q for q in (result.detail.get("quotes") or [])}

    created = updated = 0
    for inst in instruments:
        quote = by_symbol.get(inst.provider_symbol or inst.symbol)
        if not quote or not quote.trade_date:
            inst.data_status = "missing" if inst.last_data_at is None else inst.data_status
            continue
        is_new = _upsert_market(
            db,
            instrument_id=inst.id,
            symbol=inst.symbol,
            trade_date=quote.trade_date,
            open=quote.open,
            high=quote.high,
            low=quote.low,
            close=quote.price,
            prev_close=quote.prev_close,
            volume=quote.volume,
            amount=quote.amount,
            change_pct=quote.change_pct,
            data_type=quote.data_type,
            currency=quote.currency or inst.currency,
            source_code="sina_quote",
            source_url="https://finance.sina.com.cn/",
            observed_at=now_cn(),
        )
        created += int(is_new)
        updated += int(not is_new)
        inst.last_data_at = now_cn()
        inst.data_status = "normal" if quote.data_type in ("realtime", "close") else "delayed"
        if inst.instrument_type in ("bank_wealth", "insurance", "real_estate", "deposit", "cd"):
            inst.data_status = "manual"
    db.commit()

    result.detail = {}
    manager.finish(run, "sina_quote", result, trigger)
    result.detail = {"created": created, "updated": updated}
    return result


def run_market_history(db: Session, trigger: str = "schedule", limit: int = 260) -> ProviderResult:
    import asyncio

    manager = DataSourceManager(db)
    run = manager.start("eastmoney_kline", "market_history", trigger)
    instruments = db.scalars(
        select(Instrument).where(
            Instrument.enabled.is_(True),
            or_(
                Instrument.market == "CN",
                Instrument.symbol.in_(TENCENT_HISTORY_SYMBOLS),
            ),
            Instrument.data_source_code.in_(["sina_quote", "eastmoney_kline"]),
            Instrument.instrument_type.in_(["index", "etf", "stock", "reit", "money_fund", "future"]),
        )
    ).all()
    provider = EastmoneyKlineProvider()
    total = 0
    errors: list[str] = []
    ok_symbols: list[str] = []
    used_sources: set[str] = set()

    async def _fetch_all():
        semaphore = asyncio.Semaphore(4)

        async def one(inst):
            async with semaphore:
                try:
                    res = await provider.fetch_history(inst.symbol, limit=limit)
                except Exception as exc:  # noqa: BLE001
                    res = ProviderResult(provider="eastmoney_kline", ok=False, message=str(exc)[:160])
                return inst, res

        return await asyncio.gather(*[one(inst) for inst in instruments])

    try:
        results = asyncio.run(_fetch_all())
    except Exception as exc:  # noqa: BLE001
        results = []
        errors.append(str(exc)[:200])

    for inst, res in results:
        if not res.ok:
            errors.append(f"{inst.symbol}: {res.message}"[:160])
            continue
        bar_source = res.detail.get("source_code") or "eastmoney_kline"
        for bar in res.detail.get("bars", []):
            _upsert_market(
                db,
                instrument_id=inst.id,
                symbol=inst.symbol,
                trade_date=bar.trade_date,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                amount=bar.amount,
                change_pct=bar.change_pct,
                data_type="close",
                currency=inst.currency,
                source_code=bar_source,
                source_url="https://quote.eastmoney.com/" if bar_source == "eastmoney_kline" else "",
                observed_at=now_cn(),
            )
            total += 1
        ok_symbols.append(inst.symbol)
        used_sources.add(bar_source)
        if inst.data_status in ("missing", "unverified"):
            inst.data_status = "normal"
        inst.last_data_at = now_cn()
    db.commit()

    result = ProviderResult(
        provider="eastmoney_kline",
        ok=bool(ok_symbols),
        records=total,
        failed=len(instruments) - len(ok_symbols),
        message=(f"{len(ok_symbols)}/{len(instruments)} 个品种历史行情已更新，共 {total} 条日线"
                 + (f"；实际来源：{'、'.join(sorted(used_sources))}" if used_sources else "")),
        errors=errors[:10],
    )
    manager.finish(run, "eastmoney_kline", result, trigger)
    return result


def run_macro(db: Session, trigger: str = "schedule") -> ProviderResult:
    import asyncio

    manager = DataSourceManager(db)
    run = manager.start("worldbank_macro", "macro_refresh", trigger)
    provider = WorldBankProvider()
    try:
        result = asyncio.run(provider.fetch_macro(years=6))
    except Exception as exc:  # noqa: BLE001
        result = ProviderResult(provider="worldbank_macro", ok=False, message=str(exc)[:200])
    inserted = 0
    for point in result.detail.get("points", []):
        row = db.scalar(
            select(MacroObservation).where(
                MacroObservation.indicator_code == point.indicator_code,
                MacroObservation.period == point.period,
                MacroObservation.source_code == "worldbank_macro",
            )
        )
        if row is None:
            row = MacroObservation(
                indicator_code=point.indicator_code,
                period=point.period,
                source_code="worldbank_macro",
                name=point.name,
                country=point.country,
                unit=point.unit,
            )
            db.add(row)
            inserted += 1
        row.name = point.name
        row.country = point.country
        row.unit = point.unit
        row.period_date = point.period_date
        row.release_at = point.release_at
        row.fetched_at = now_cn()
        row.value = point.value
        row.previous_value = point.previous_value
        row.revised = point.revised
        row.source_url = point.source_url
    db.commit()
    result.detail = {"inserted": inserted}
    manager.finish(run, "worldbank_macro", result, trigger)

    # FRED 作为独立数据源，失败不影响世界银行结果
    fred = FredProvider()
    ok, reason = fred.available()
    fred_run = manager.start("fred_macro", "macro_refresh", trigger)
    if not ok:
        manager.finish(
            fred_run,
            "fred_macro",
            ProviderResult(provider="fred_macro", ok=False, skipped=1, message=reason),
            trigger,
        )
        result.detail["fred"] = reason
        return result
    try:
        fred_result = asyncio.run(fred.fetch_macro(limit=24))
    except Exception as exc:  # noqa: BLE001
        fred_result = ProviderResult(provider="fred_macro", ok=False, message=str(exc)[:200])
    for point in fred_result.detail.get("points", []):
        row = db.scalar(
            select(MacroObservation).where(
                MacroObservation.indicator_code == point.indicator_code,
                MacroObservation.period == point.period,
                MacroObservation.source_code == "fred_macro",
            )
        )
        if row is None:
            row = MacroObservation(
                indicator_code=point.indicator_code,
                period=point.period,
                source_code="fred_macro",
                name=point.name,
                country="US",
                unit=point.unit,
            )
            db.add(row)
        row.fetched_at = now_cn()
        row.value = point.value
        row.previous_value = point.previous_value
        row.source_url = point.source_url
    db.commit()
    manager.finish(fred_run, "fred_macro", fred_result, trigger)
    result.detail["fred"] = fred_result.message
    return result


def run_news(db: Session, trigger: str = "schedule") -> ProviderResult:
    import asyncio

    manager = DataSourceManager(db)
    run = manager.start("eastmoney_news", "news_collect", trigger)
    provider = EastmoneyNewsProvider()
    try:
        result = asyncio.run(provider.fetch_news(page_size=settings.NEWS_MAX_ITEMS_PER_RUN))
    except Exception as exc:  # noqa: BLE001
        result = ProviderResult(provider="eastmoney_news", ok=False, message=f"新闻采集异常: {exc}"[:300])
    items = result.detail.get("items", [])
    stats = store_documents(db, items)
    db.commit()
    result.detail = stats
    result.message = f"获取 {len(items)} 条，新增 {stats['created']} 条，重复 {stats['duplicate']} 条"
    manager.finish(run, "eastmoney_news", result, trigger)
    return result


def source_health_snapshot(db: Session) -> list[dict]:
    sources = db.scalars(select(DataSource).order_by(DataSource.category, DataSource.id)).all()
    out = []
    for src in sources:
        last_run = db.scalar(
            select(DataSourceRun)
            .where(DataSourceRun.source_code == src.code)
            .order_by(DataSourceRun.started_at.desc())
        )
        out.append(
            {
                "code": src.code,
                "name": src.name,
                "category": src.category,
                "status": src.status,
                "status_message": src.status_message,
                "enabled": src.enabled,
                "requires_key": src.requires_key,
                "last_success_at": src.last_success_at,
                "last_run_at": src.last_run_at,
                "next_run_at": src.next_run_at,
                "last_error": src.last_error,
                "last_run": {
                    "status": last_run.status if last_run else None,
                    "started_at": last_run.started_at if last_run else None,
                    "finished_at": last_run.finished_at if last_run else None,
                    "records": last_run.records if last_run else 0,
                    "duration_ms": last_run.duration_ms if last_run else None,
                },
            }
        )
    return out
