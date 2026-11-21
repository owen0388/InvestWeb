"""公网部署的访问认证：不放行就等同于把持仓公开。"""
from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from app.core.auth import BasicAuthMiddleware


async def _ok(request):
    return PlainTextResponse("ok")


def _client() -> TestClient:
    app = Starlette(routes=[Route("/", _ok), Route("/api/health", _ok), Route("/api/portfolio", _ok)])
    wrapped = BasicAuthMiddleware(app, username="admin", password="s3cret")
    return TestClient(wrapped)


def test_rejects_without_credentials():
    assert _client().get("/api/portfolio").status_code == 401


def test_accepts_correct_password():
    resp = _client().get("/api/portfolio", auth=("admin", "s3cret"))
    assert resp.status_code == 200
    assert resp.text == "ok"


def test_rejects_wrong_password():
    assert _client().get("/api/portfolio", auth=("admin", "bad")).status_code == 401
    assert _client().get("/api/portfolio", auth=("other", "s3cret")).status_code == 401


def test_health_stays_public():
    """健康检查放行，便于探活；它不含业务数据。"""
    resp = _client().get("/api/health")
    assert resp.status_code == 200


def test_sends_www_authenticate_header():
    resp = _client().get("/")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"].startswith("Basic realm=")


def test_malformed_header_is_rejected_not_crashed():
    resp = _client().get("/", headers={"Authorization": "Basic !!!not-base64!!!"})
    assert resp.status_code == 401
