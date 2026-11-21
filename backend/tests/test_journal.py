"""交易日志：录入校验、平仓盈亏、归因统计与样本保护。"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete

from app.models.entities import TradeJournal
from app.services.journal_service import compute_pnl, summarize


def _cleanup(db) -> None:
    db.execute(delete(TradeJournal).where(TradeJournal.symbol.like("TJ%")))
    db.commit()


def _payload(symbol: str, **overrides) -> dict:
    base = {
        "symbol": symbol,
        "name": f"测试{symbol}",
        "asset_class_code": "FUND_ETF_WIDE",
        "decision": "followed",
        "actual_price": 1.0,
        "quantity": 10000,
        "fee": 5.0,
        "invalidation": "收盘跌破 MA20",
    }
    base.update(overrides)
    return base


def test_compute_pnl_deducts_fees():
    pnl, pct = compute_pnl(1.0, 1.1, 10000, fee_open=5, fee_close=5)
    assert pnl == 990.0  # 1000 毛利 − 10 费用
    assert round(pct, 6) == 0.099


def test_create_requires_invalidation(client, db):
    _cleanup(db)
    resp = client.post("/api/journal", json=_payload("TJ001", invalidation=""))
    assert resp.status_code == 422
    assert "失效条件" in resp.text


def test_create_and_close_computes_pnl(client, db):
    _cleanup(db)
    created = client.post("/api/journal", json=_payload("TJ002")).json()
    assert created["status"] == "open"
    assert created["amount"] == 10000.0

    closed = client.put(
        f"/api/journal/{created['id']}/close",
        json={"exit_price": 1.08, "exit_reason": "target", "fee": 5},
    ).json()
    assert closed["status"] == "closed"
    assert closed["pnl"] == 790.0  # 800 − 5 − 5
    assert closed["holding_days"] == 0
    _cleanup(db)


def test_double_close_rejected(client, db):
    _cleanup(db)
    created = client.post("/api/journal", json=_payload("TJ003")).json()
    body = {"exit_price": 1.05, "exit_reason": "manual"}
    assert client.put(f"/api/journal/{created['id']}/close", json=body).status_code == 200
    assert client.put(f"/api/journal/{created['id']}/close", json=body).status_code == 409
    _cleanup(db)


def test_skipped_trade_still_records_hypothetical_pnl(client, db):
    """没执行的信号也要算「如果做了会怎样」，否则无法评估信号本身。"""
    _cleanup(db)
    created = client.post(
        "/api/journal",
        json=_payload("TJ004", decision="skipped", signal_score=72.0, signal_state="CANDIDATE"),
    ).json()
    closed = client.put(
        f"/api/journal/{created['id']}/close",
        json={"exit_price": 0.95, "exit_reason": "invalidation"},
    ).json()
    assert closed["decision"] == "skipped"
    assert closed["pnl"] < 0
    _cleanup(db)


def test_stats_flags_small_sample(client, db):
    _cleanup(db)
    created = client.post("/api/journal", json=_payload("TJ005")).json()
    client.put(
        f"/api/journal/{created['id']}/close",
        json={"exit_price": 1.05, "exit_reason": "target"},
    )
    stats = client.get("/api/journal/stats").json()
    assert stats["overall"]["sample"] >= 1
    assert stats["overall"]["reliable"] is False
    assert any("不足" in w for w in stats["warnings"])
    assert stats["comparison"] is None, "样本不足时不得给出'照做更好'的结论"
    _cleanup(db)


def test_summarize_groups_by_decision():
    rows = [
        {"pnl": 100.0, "decision": "followed", "asset_class_code": "A", "status": "closed"},
        {"pnl": -50.0, "decision": "followed", "asset_class_code": "A", "status": "closed"},
        {"pnl": 20.0, "decision": "skipped", "asset_class_code": "B", "status": "closed"},
        {"pnl": None, "decision": "manual", "asset_class_code": "B", "status": "open"},
    ]
    result = summarize(rows)
    assert result["overall"]["sample"] == 3
    assert result["overall"]["win_rate"] == round(2 / 3, 4)
    assert result["by_decision"]["followed"]["sample"] == 2
    assert result["by_decision"]["followed"]["payoff_ratio"] == 2.0
    assert result["open_positions"] == 1
    assert result["overall"]["reliable"] is False


def test_holding_days_uses_dates(client, db):
    _cleanup(db)
    opened = datetime(2026, 10, 1, 10, 0)
    created = client.post(
        "/api/journal", json=_payload("TJ006", opened_at=opened.isoformat())
    ).json()
    closed = client.put(
        f"/api/journal/{created['id']}/close",
        json={"exit_price": 1.05, "exit_reason": "target", "closed_at": "2026-10-31T10:00:00"},
    ).json()
    assert closed["holding_days"] == 30
    _cleanup(db)
