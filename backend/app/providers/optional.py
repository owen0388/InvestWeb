"""可选 / 需密钥的适配器。

这些适配器不会让系统启动失败：缺少密钥或依赖时，状态明确显示为
unconfigured，并在数据源管理页给出原因与所需权限。
"""
from __future__ import annotations

import importlib.util

from app.core.config import settings
from app.providers.base import BaseProvider, HttpClient, NewsItem, ProviderResult, to_float
from app.core.timeutil import parse_dt


class GdeltProvider(BaseProvider):
    """GDELT DOC 2.0：全球新闻发现。官方要求 <= 1 次/5 秒。"""

    code = "gdelt_news"
    name = "GDELT 全球事件监测"
    category = "news"

    def available(self) -> tuple[bool, str]:
        if not settings.GDELT_ENABLED:
            return False, "默认关闭（GDELT_ENABLED=0），启用后受每 5 秒 1 次频率限制"
        return True, ""

    async def fetch_news(self, query: str = "sourcelang:english (market OR economy)", limit: int = 25) -> ProviderResult:
        ok, reason = self.available()
        if not ok:
            return ProviderResult(provider=self.code, ok=False, skipped=1, message=reason)
        params = {
            "query": query,
            "mode": "artlist",
            "maxrecords": str(limit),
            "format": "json",
            "sort": "datedesc",
        }
        try:
            async with HttpClient(min_interval=5.0) as client:
                resp = await client.get("https://api.gdeltproject.org/api/v2/doc/doc", params=params)
                payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            return ProviderResult(provider=self.code, ok=False, message=f"GDELT 请求失败: {exc}"[:200])
        items: list[NewsItem] = []
        for row in payload.get("articles") or []:
            title = (row.get("title") or "").strip()
            url = (row.get("url") or "").strip()
            if not title or not url:
                continue
            items.append(
                NewsItem(
                    source_code=self.code,
                    external_id=url,
                    title=title,
                    summary=(row.get("domain") or ""),
                    url=url,
                    media=(row.get("domain") or "").strip(),
                    language="en",
                    published_at=parse_dt(row.get("seendate")),
                    reliability="low",
                    raw={"sourcecountry": row.get("sourcecountry")},
                )
            )
        return ProviderResult(
            provider=self.code,
            ok=bool(items),
            records=len(items),
            message=f"获取 {len(items)} 条 GDELT 新闻",
            detail={"items": items},
        )

    async def healthcheck(self) -> ProviderResult:
        return await self.fetch_news(limit=2)


class TushareProvider(BaseProvider):
    code = "tushare_pro"
    name = "Tushare Pro"
    category = "market"

    def available(self) -> tuple[bool, str]:
        if not settings.TUSHARE_TOKEN:
            return False, "未配置 TUSHARE_TOKEN；需在 tushare.pro 注册并满足积分要求"
        return True, "已配置 Token（首期未启用该通道，避免未经许可的字段假设）"

    async def healthcheck(self) -> ProviderResult:
        ok, reason = self.available()
        return ProviderResult(provider=self.code, ok=ok, skipped=0 if ok else 1, message=reason)


class AkshareProvider(BaseProvider):
    code = "akshare"
    name = "AKShare 公开数据适配器"
    category = "market"

    def available(self) -> tuple[bool, str]:
        if importlib.util.find_spec("akshare") is None:
            return False, "未安装 akshare 包；可选依赖，不影响系统运行"
        return True, "已安装 akshare（首期未启用该通道，避免未经许可的字段假设）"

    async def healthcheck(self) -> ProviderResult:
        ok, reason = self.available()
        return ProviderResult(provider=self.code, ok=ok, skipped=0 if ok else 1, message=reason)
