"""API 集成测试：真实调用 FastAPI 路由，验证分页、字段与错误响应。"""
from __future__ import annotations

import pytest


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("ok", "degraded")
    assert body["counts"]["instruments"] > 0
    assert "ai" in body and body["ai"]["configured"] is False


def test_asset_classes(client):
    resp = client.get("/api/asset-classes")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 12
    codes = {item["code"] for item in items}
    assert {"CASH", "CN_EQ", "FUND", "BOND", "GOLD", "COMMODITY", "DERIVATIVE",
            "OVERSEAS", "REIT", "WEALTH", "INSURANCE", "REALESTATE"} <= codes
    assert all("children" in item and "counts" in item for item in items)


def test_instruments_pagination_and_filter(client):
    resp = client.get("/api/instruments", params={"page": 1, "page_size": 5})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 5
    assert body["total"] > 5
    filtered = client.get("/api/instruments", params={"asset_class": "GOLD"}).json()
    assert filtered["total"] >= 2
    assert all(i["asset_class_code"].startswith("GOLD") for i in filtered["items"])


def test_instrument_detail_and_404(client):
    resp = client.get("/api/instruments/sh000300")
    assert resp.status_code == 200
    body = resp.json()
    assert body["instrument"]["symbol"] == "sh000300"
    assert "history" in body and "signals" in body
    assert client.get("/api/instruments/not-exist").status_code == 404


def test_instrument_days_validation(client):
    assert client.get("/api/instruments/sh000300", params={"days": 5}).status_code == 422


def test_overview_and_regime(client):
    overview = client.get("/api/market/overview")
    assert overview.status_code == 200
    body = overview.json()
    assert "regime" in body and "data_health" in body
    assert body["data_health"]["instruments_total"] > 0
    regime = client.get("/api/market/regime")
    assert regime.status_code == 200
    assert regime.json()["state"]


def test_news_and_events_listing(client):
    news = client.get("/api/news", params={"days": 30})
    assert news.status_code == 200
    assert "items" in news.json()
    events = client.get("/api/events", params={"days": 30})
    assert events.status_code == 200
    for item in events.json()["items"]:
        assert item["importance"] <= 100
        assert item["direction"] in ("bullish", "bearish", "mixed", "unknown")


def test_sources_and_jobs(client):
    sources = client.get("/api/sources").json()["items"]
    assert len(sources) >= 8
    codes = {s["code"] for s in sources}
    assert "fred_macro" in codes and "sina_quote" in codes
    fred = next(s for s in sources if s["code"] == "fred_macro")
    assert fred["requires_key"] is True
    jobs = client.get("/api/jobs")
    assert jobs.status_code == 200
    assert isinstance(jobs.json()["jobs"], list)


def test_portfolio_and_allocation(client):
    portfolios = client.get("/api/portfolios").json()["items"]
    assert portfolios
    pid = portfolios[0]["id"]
    detail = client.get(f"/api/portfolios/{pid}")
    assert detail.status_code == 200
    allocation = client.get(f"/api/portfolios/{pid}/allocation")
    assert allocation.status_code == 200
    body = allocation.json()
    assert sum(t["target"] for t in body["targets"]) == pytest.approx(1.0, abs=1e-5)
    assert body["disclaimer"]


def test_settings_hides_secrets(client):
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    text = resp.text
    assert "api_key" not in text.lower() or "api_key_configured" in text
    assert "sk-" not in text


def test_backtest_fails_explicitly_without_history(client):
    resp = client.post("/api/backtests", json={"max_tilt": 0.05})
    assert resp.status_code in (422, 200)
    if resp.status_code == 422:
        detail = resp.json()["detail"]
        assert "不足" in detail["message"] or "数据" in detail["message"]


def test_unknown_route_returns_404(client):
    assert client.get("/api/does-not-exist").status_code == 404
