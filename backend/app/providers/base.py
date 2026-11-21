"""统一数据采集适配器接口与通用 HTTP 客户端。

所有 provider 都返回统一的内部数据结构，采集服务只关心结果，
不关心具体站点。缺少密钥/依赖的适配器通过 `available()` 明确暴露原因。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

import httpx

from app.core.config import settings

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@dataclass
class Quote:
    symbol: str
    name: str
    price: Optional[float]
    prev_close: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    volume: Optional[float] = None
    amount: Optional[float] = None
    change_pct: Optional[float] = None
    quote_time: Optional[datetime] = None
    trade_date: Optional[date] = None
    currency: str = "CNY"
    data_type: str = "close"


@dataclass
class Bar:
    symbol: str
    trade_date: date
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    volume: Optional[float] = None
    amount: Optional[float] = None
    change_pct: Optional[float] = None


@dataclass
class NewsItem:
    source_code: str
    external_id: str
    title: str
    summary: str = ""
    url: str = ""
    media: str = ""
    language: str = "zh"
    published_at: Optional[datetime] = None
    reliability: str = "medium"
    raw: dict = field(default_factory=dict)


@dataclass
class MacroPoint:
    indicator_code: str
    name: str
    country: str
    unit: str
    period: str
    value: Optional[float]
    period_date: Optional[date] = None
    release_at: Optional[datetime] = None
    previous_value: Optional[float] = None
    forecast_value: Optional[float] = None
    forecast_source: str = ""
    revised: bool = False
    source_url: str = ""


@dataclass
class ProviderResult:
    provider: str
    ok: bool
    records: int = 0
    failed: int = 0
    skipped: int = 0
    message: str = ""
    detail: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)


class HttpClient:
    """带超时、重试、退避与限频的轻量 HTTP 包装。"""

    def __init__(self, min_interval: float = 0.0):
        self.min_interval = min_interval
        self._last = 0.0
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> "HttpClient":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.HTTP_TIMEOUT),
            headers={"User-Agent": UA},
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client:
            await self._client.aclose()

    async def get(self, url: str, **kwargs) -> httpx.Response:
        assert self._client is not None
        last_error: Exception | None = None
        for attempt in range(settings.HTTP_MAX_RETRIES + 1):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()
            try:
                resp = await self._client.get(url, **kwargs)
                if resp.status_code in (429, 502, 503, 504) and attempt < settings.HTTP_MAX_RETRIES:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                resp.raise_for_status()
                return resp
            except Exception as exc:  # noqa: BLE001 - 记录后重试
                last_error = exc
                if attempt < settings.HTTP_MAX_RETRIES:
                    await asyncio.sleep(1.0 * (attempt + 1))
        raise RuntimeError(f"请求失败: {url[:80]} -> {last_error}")


class BaseProvider:
    code: str = "base"
    name: str = "base"
    category: str = "market"

    def available(self) -> tuple[bool, str]:
        return True, ""

    async def healthcheck(self) -> ProviderResult:  # pragma: no cover - 由子类实现
        raise NotImplementedError


def to_float(value: Any) -> Optional[float]:
    try:
        if value in (None, "", "-", "--", "None"):
            return None
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
