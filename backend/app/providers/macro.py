"""宏观数据适配器：世界银行（免密钥，年度）+ FRED（需 API Key，日频）。"""
from __future__ import annotations

import asyncio
from datetime import date

from app.core.config import settings
from app.providers.base import BaseProvider, HttpClient, MacroPoint, ProviderResult, to_float

WORLDBANK_INDICATORS = [
    ("NY.GDP.MKTP.KD.ZG", "实际 GDP 增速", "%"),
    ("FP.CPI.TOTL.ZG", "CPI 通胀率", "%"),
    ("SL.UEM.TOTL.ZS", "失业率", "%"),
    ("GC.DOD.TOTL.GD.ZS", "政府债务占 GDP", "%"),
    ("NE.EXP.GNFS.ZS", "出口占 GDP", "%"),
]
WORLDBANK_COUNTRIES = {"CN": "中国", "US": "美国", "WLD": "全球"}


class WorldBankProvider(BaseProvider):
    code = "worldbank_macro"
    name = "世界银行公开数据"
    category = "macro"

    async def fetch_macro(self, years: int = 6) -> ProviderResult:
        points: list[MacroPoint] = []
        errors: list[str] = []
        end_year = date.today().year - 1
        start_year = end_year - years + 1

        async with HttpClient(min_interval=0.0) as client:
            semaphore = asyncio.Semaphore(4)

            async def fetch_one(country: str, indicator: str, label: str, unit: str) -> None:
                url = f"https://api.worldbank.org/v2/country/{country}/indicator/{indicator}"
                params = {"format": "json", "per_page": "50", "date": f"{start_year}:{end_year}"}
                async with semaphore:
                    try:
                        resp = await client.get(url, params=params)
                        payload = resp.json()
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"{country}/{indicator}: {exc}"[:160])
                        return
                if not isinstance(payload, list) or len(payload) < 2 or not payload[1]:
                    return
                rows = sorted(payload[1], key=lambda r: r.get("date") or "")
                prev_value = None
                for row in rows:
                    value = to_float(row.get("value"))
                    period = str(row.get("date"))
                    points.append(
                        MacroPoint(
                            indicator_code=f"WB_{indicator}_{country}",
                            name=f"{WORLDBANK_COUNTRIES[country]}{label}",
                            country=country,
                            unit=unit,
                            period=period,
                            period_date=date(int(period), 12, 31),
                            release_at=None,
                            value=value,
                            previous_value=prev_value,
                            forecast_value=None,
                            revised=False,
                            source_url=url,
                        )
                    )
                    prev_value = value

            await asyncio.gather(
                *[
                    fetch_one(country, indicator, label, unit)
                    for country in WORLDBANK_COUNTRIES
                    for indicator, label, unit in WORLDBANK_INDICATORS
                ]
            )

        return ProviderResult(
            provider=self.code,
            ok=bool(points),
            records=len(points),
            message=f"获取 {len(points)} 条年度宏观观测（官方未提供逐条发布时间，release_at 留空）",
            detail={"points": points},
            errors=errors[:5],
        )

    async def healthcheck(self) -> ProviderResult:
        return await self.fetch_macro(years=1)


FRED_SERIES = [
    ("DGS10", "美国 10 年期国债收益率", "%"),
    ("DGS2", "美国 2 年期国债收益率", "%"),
    ("T10Y2Y", "美债 10Y-2Y 利差", "%"),
    ("FEDFUNDS", "联邦基金利率", "%"),
    ("CPIAUCSL", "美国 CPI 指数", "index"),
    ("UNRATE", "美国失业率", "%"),
    ("DTWEXBGS", "美元贸易加权指数", "index"),
    ("DCOILWTICO", "WTI 原油现货", "USD"),
]


class FredProvider(BaseProvider):
    code = "fred_macro"
    name = "FRED / ALFRED 宏观数据"
    category = "macro"

    def available(self) -> tuple[bool, str]:
        if not settings.FRED_API_KEY:
            return False, "未配置 FRED_API_KEY，需在 .env 中填写免费申请的 Key"
        return True, ""

    async def fetch_macro(self, limit: int = 24) -> ProviderResult:
        ok, reason = self.available()
        if not ok:
            return ProviderResult(provider=self.code, ok=False, skipped=1, message=reason)
        points: list[MacroPoint] = []
        errors: list[str] = []

        async with HttpClient(min_interval=0.0) as client:
            semaphore = asyncio.Semaphore(4)

            async def fetch_one(series_id: str, label: str, unit: str) -> None:
                params = {
                    "series_id": series_id,
                    "api_key": settings.FRED_API_KEY,
                    "file_type": "json",
                    "sort_order": "desc",
                    "limit": str(limit),
                }
                async with semaphore:
                    try:
                        resp = await client.get(
                            "https://api.stlouisfed.org/fred/series/observations", params=params
                        )
                        rows = (resp.json().get("observations") or [])[::-1]
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"{series_id}: {exc}"[:160])
                        return
                prev_value = None
                for row in rows:
                    value = to_float(row.get("value"))
                    period = row.get("date") or ""
                    if not period:
                        continue
                    try:
                        period_date = date.fromisoformat(period)
                    except ValueError:
                        period_date = None
                    points.append(
                        MacroPoint(
                            indicator_code=f"FRED_{series_id}",
                            name=label,
                            country="US",
                            unit=unit,
                            period=period,
                            period_date=period_date,
                            release_at=None,
                            value=value,
                            previous_value=prev_value,
                            revised=False,
                            source_url=f"https://fred.stlouisfed.org/series/{series_id}",
                        )
                    )
                    prev_value = value

            await asyncio.gather(*[fetch_one(*item) for item in FRED_SERIES])

        return ProviderResult(
            provider=self.code,
            ok=bool(points),
            records=len(points),
            message=f"获取 {len(points)} 条 FRED 观测",
            detail={"points": points},
            errors=errors[:5],
        )

    async def healthcheck(self) -> ProviderResult:
        return await self.fetch_macro(limit=2)
