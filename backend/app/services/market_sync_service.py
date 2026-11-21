"""全市场标的同步。

只做标的目录的同步，不拉行情（行情按需实时读，避免把 981 万行搬进本地库）。

设计要点：
- 以 `instrument_id` 为唯一键做 upsert，不用 symbol（000001 有两个标的）
- 不删除库中已存在但上游消失的标的（保留历史，标记 status 即可）
- 每次同步写一条日志，记录每类数量与变化数
- 可重复执行，结果幂等
"""
from __future__ import annotations

from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.timeutil import now_cn
from app.datasource.investdata import InvestDataClient
from app.models.market_models import MarketInstrument, MarketSyncLog

# 沿用 InvestData 的枚举值，不重新定义
SYNC_TYPES = ("stock", "etf", "index", "industry_index", "concept_index")

# 需要比对是否变化的字段（name 可能变化，status 会变）
COMPARE_FIELDS = ("symbol", "name", "instrument_type", "market", "exchange",
                  "status", "sector_type", "currency", "listing_date", "delisting_date")


def _client() -> InvestDataClient:
    return InvestDataClient(
        base_url=settings.INVESTDATA_BASE_URL,
        timeout=settings.INVESTDATA_TIMEOUT,
    )


def sync_instruments(
    db: Session,
    client: Optional[InvestDataClient] = None,
    types: Iterable[str] = SYNC_TYPES,
) -> dict:
    """同步标的目录。返回统计结果。"""
    own_client = client is None
    client = client or _client()

    log = MarketSyncLog(started_at=now_cn(), status="running")
    db.add(log)
    db.commit()

    try:
        fetched: list[dict] = []
        per_type: dict[str, int] = {}
        for t in types:
            items = client.list_instruments(instrument_type=t)
            per_type[t] = len(items)
            fetched.extend(items)

        existing = {
            row.instrument_id: row
            for row in db.scalars(select(MarketInstrument)).all()
        }

        inserted = updated = unchanged = 0
        to_insert: list[dict] = []

        for item in fetched:
            iid = item.get("instrument_id")
            if not iid:
                continue
            payload = {
                "instrument_id": iid,
                "symbol": item.get("symbol") or "",
                "name": item.get("name"),
                "instrument_type": item.get("instrument_type") or "",
                "market": item.get("market"),
                "exchange": item.get("exchange"),
                "status": item.get("status"),
                "sector_type": item.get("sector_type"),
                "currency": item.get("currency"),
                "listing_date": item.get("listing_date"),
                "delisting_date": item.get("delisting_date"),
            }
            row = existing.get(iid)
            if row is None:
                payload["source"] = "investdata"
                payload["last_synced_at"] = now_cn()
                payload["created_at"] = now_cn()
                to_insert.append(payload)
                inserted += 1
                continue

            changed = False
            for field in COMPARE_FIELDS:
                if getattr(row, field) != payload[field]:
                    setattr(row, field, payload[field])
                    changed = True
            # 无论是否变化都记录同步时间，用于判断数据新鲜度
            row.last_synced_at = now_cn()
            if changed:
                updated += 1
            else:
                unchanged += 1

        if to_insert:
            db.bulk_insert_mappings(MarketInstrument, to_insert)
        db.commit()

        log.finished_at = now_cn()
        log.status = "success"
        log.total_fetched = len(fetched)
        log.inserted = inserted
        log.updated = updated
        log.unchanged = unchanged
        import json
        log.by_type = json.dumps(per_type, ensure_ascii=False)
        db.commit()

        return {
            "ok": True,
            "total": len(fetched),
            "inserted": inserted,
            "updated": updated,
            "unchanged": unchanged,
            "by_type": per_type,
        }
    except Exception as exc:  # noqa: BLE001
        log.finished_at = now_cn()
        log.status = "failed"
        log.error = str(exc)[:500]
        db.commit()
        return {"ok": False, "error": str(exc)[:300]}
    finally:
        if own_client:
            client.close()


def count_by_type(db: Session) -> dict:
    """本地表按类型计数，用于核对与上游是否一致。"""
    from sqlalchemy import func

    rows = db.execute(
        select(MarketInstrument.instrument_type, func.count())
        .group_by(MarketInstrument.instrument_type)
    ).all()
    return {r[0]: r[1] for r in rows}
