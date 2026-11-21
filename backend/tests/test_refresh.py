"""数据刷新：作用域展开、新鲜度判定、互斥。"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.services import refresh_service


def test_plan_expands_all_in_pipeline_order():
    """all 必须按「先取数、再计算、最后快照」的顺序，不能并行乱序。"""
    assert refresh_service.plan("all") == ["quotes", "universe", "quality", "radar"]


def test_plan_rejects_unknown_scope():
    assert refresh_service.plan("whatever") == []


def test_all_scopes_have_metadata():
    for key, (label, seconds, desc) in refresh_service.SCOPES.items():
        assert label and seconds > 0 and desc


def test_status_lists_four_layers(client):
    data = client.get("/api/refresh/status").json()
    keys = [layer["key"] for layer in data["layers"]]
    assert keys == ["quotes", "universe", "quality", "radar"]
    assert "session_state" in data
    assert isinstance(data["running"], list)


def test_unknown_scope_returns_400(client):
    resp = client.post("/api/refresh", json={"scope": "nope"})
    assert resp.status_code == 400
    assert "scope" in resp.json()["detail"]


def test_running_guard_blocks_duplicate():
    """同一时刻只允许一个刷新，避免并发打挂采集源。"""
    refresh_service.mark_start("universe")
    try:
        assert refresh_service.is_running("universe")
        assert refresh_service.running_scopes()
    finally:
        refresh_service.mark_done("universe")
    assert not refresh_service.is_running("universe")


def test_layer_marks_missing_as_stale():
    layer = refresh_service._layer("quotes", None, 10, "无数据")
    assert layer["stale"] is True
    assert layer["reason"] == "从未更新"


def test_layer_tolerates_slight_delay():
    """刚过预期时间不算过期，超过 3 倍才算（与 health_check 口径一致）。"""
    from app.core.timeutil import now_cn

    fresh = refresh_service._layer("quotes", now_cn() - timedelta(minutes=15), 10)
    assert fresh["stale"] is False
    stale = refresh_service._layer("quotes", now_cn() - timedelta(minutes=45), 10)
    assert stale["stale"] is True
