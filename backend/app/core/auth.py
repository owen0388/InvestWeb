"""HTTP Basic 认证中间件。

为什么必须有：这个系统的 /api/portfolio、/api/journal、/api/settings 是裸接口，
一旦监听公网地址，任何知道 IP 的人都能读到真实持仓与交易记录，甚至改配置。
本地开发不设 AUTH_PASSWORD 自动跳过，公网部署必须设置。

选择 Basic Auth 而不是自建登录页，是因为：
- 零前端改动，浏览器原生弹窗
- 静态资源、API、文档一起保护，不会漏
代价是每次请求带 Authorization 头（HTTPS 下安全）。
"""
from __future__ import annotations

import base64
import binascii
import secrets

from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse

# 健康检查放行：便于探活与反向代理健康探测，不泄露业务数据
PUBLIC_PATHS = {"/api/health"}


class BasicAuthMiddleware:
    def __init__(self, app, username: str, password: str, realm: str = "InvestWeb"):
        self.app = app
        self.username = username
        self.password = password
        self.realm = realm

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 棰勬璇锋眰鏀捐锛屽惁鍒欒法鍩熷満鏅笅鍓嶇鎷夸笉鍒板搷搴
        if scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if path in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        auth = headers.get("authorization", "")
        if auth.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth[6:]).decode("utf-8")
                user, _, pwd = decoded.partition(":")
            except (binascii.Error, UnicodeDecodeError):
                user = pwd = ""
            # 使用常量时间比较，避免通过响应时间猜测密码
            if secrets.compare_digest(user, self.username) and secrets.compare_digest(
                pwd, self.password
            ):
                await self.app(scope, receive, send)
                return

        response = PlainTextResponse(
            "需要登录", status_code=401, headers={"WWW-Authenticate": f'Basic realm="{self.realm}"'}
        )
        await response(scope, receive, send)
