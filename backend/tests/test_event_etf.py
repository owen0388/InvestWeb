"""事件 → ETF 映射与信息差因子。"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import delete, select

from app.models.entities import EtfUniverse, Event, EventImpact, MarketObservation
from app.services.event_etf_service import information_gap_factors, map_event_to_etfs


def _cleanup(db) -> None:
    db.execute(delete(EventImpact).where(EventImpact.event_id.in_(select(Event.id).where(Event.event_key.like("TE%")))))
    db.execute(delete(Event).where(Event.event_key.like("TE%")))
    db.execute(delete(MarketObservation).where(MarketObservation.symbol.like("shT9%")))
    db.execute(delete(EtfUniverse).where(EtfUniverse.code.like("T9%")))
    db.commit()


def _event(db, key: str = "TE0001", evidence: int = 1) -> Event:
    row = Event(
        event_key=key,
        title="测试事件：某行业政策调整",
        category="technology",
        importance=85.0,
        evidence_count=evidence,
        direction="bullish",
        published_at=datetime(2026, 11, 4, 9, 0),
    )
    db.add(row)
    db.flush()
    db.add(
        EventImpact(
            event_id=row.id,
            instrument_symbol="",
            asset_class_code="FUND_ETF_SECTOR",
            direction="bullish",
            mechanism="政策推动行业需求",
            horizon="medium",
            magnitude=0.3,
            confidence=0.6,
        )
    )
    db.commit()
    return row


def _etf(db, code: str, name: str, tracked: str = "") -> EtfUniverse:
    base = date(2025, 9, 1)
    row = EtfUniverse(
        code=code,
        symbol=f"sh{code}",
        name=name,
        market="SH",
        category="sector",
        tracked_index=tracked,
        status="candidate",
        quality_score=70.0,
        quality_confidence=1.0,
        quality_breakdown={},
        amount=1e8,
        change_pct=0.5,
        listed_days=800,
        list_date=base - timedelta(days=800),
    )
    db.add(row)
    for i in range(30):
        db.add(
            MarketObservation(
                instrument_id=None,
                symbol=f"sh{code}",
                trade_date=base + timedelta(days=i),
                close=1.0 + i * 0.001,
                data_type="close",
                currency="CNY",
                source_code="eastmoney_kline",
            )
        )
    db.commit()
    return row


def test_missing_event_returns_reason(db):
    _cleanup(db)
    result = map_event_to_etfs(db, 999999)
    assert result["ok"] is False
    assert "不存在" in result["message"]
    _cleanup(db)


def test_event_without_impacts_is_explicit(db):
    _cleanup(db)
    event = Event(event_key="TE0002", title="无影响记录的事件", category="other", importance=60.0)
    db.add(event)
    db.commit()
    result = map_event_to_etfs(db, event.id)
    assert result["ok"] is True
    assert result["items"] == []
    assert "没有资产影响记录" in result["message"]
    _cleanup(db)


def test_maps_by_theme_and_category(db):
    _cleanup(db)
    event = _event(db)
    _etf(db, "T90001", "科创半导体ETF测试", tracked="中证半导体指数")
    _etf(db, "T90002", "红利低波ETF测试", tracked="中证红利指数")

    result = map_event_to_etfs(db, event.id)
    codes = [item["code"] for item in result["items"]]
    # 科技类事件 + FUND_ETF_SECTOR 类别 → 半导体命中，红利不应优先
    assert "T90001" in codes
    hit = next(i for i in result["items"] if i["code"] == "T90001")
    assert hit["match"]["by_theme"] is True
    assert "主题匹配" in hit["match"]["reason"]
    _cleanup(db)


def test_coverage_gap_needs_low_evidence_and_high_importance(db):
    _cleanup(db)
    event = _event(db, evidence=1)
    etf = _etf(db, "T90003", "科创芯片ETF测试")
    gap = information_gap_factors(db, etf, event, amount_percentile=0.5)
    assert gap["count"] >= 1
    assert gap["factors"][0]["type"] == "coverage_gap"
    assert gap["multiplier"] > 1.0

    high_evidence = _event(db, key="TE0003", evidence=6)
    gap2 = information_gap_factors(db, etf, high_evidence, amount_percentile=0.5)
    assert all(f["type"] != "coverage_gap" for f in gap2["factors"])
    _cleanup(db)


def test_low_attention_factor(db):
    _cleanup(db)
    etf = _etf(db, "T90004", "低关注ETF测试")
    gap = information_gap_factors(db, etf, None, amount_percentile=0.1)
    assert any(f["type"] == "low_attention" for f in gap["factors"])
    high = information_gap_factors(db, etf, None, amount_percentile=0.8)
    assert not any(f["type"] == "low_attention" for f in high["factors"])
    _cleanup(db)


def test_uncomputable_factors_are_declared(db):
    """算不出来的因子要明说，不能用 0 冒充。"""
    _cleanup(db)
    etf = _etf(db, "T90005", "测试ETF")
    gap = information_gap_factors(db, etf, None, amount_percentile=0.5)
    assert any("预期分歧" in n for n in gap["not_computed"])
    _cleanup(db)
