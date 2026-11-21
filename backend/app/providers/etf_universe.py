"""ETF 全量目录与基金详情适配器（东方财富公开接口）。

实测（2026-11-04）：
- 列表接口 17 页可拉全 1,657 只，约 16 秒，价格/成交额/规模/上市日覆盖 100%。
- 基金详情接口可拿单位净值、报告期净资产、成立日、跟踪指数。
- 详情接口连续请求会被风控返回空 Datas，因此串行 + 间隔 + 重试。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from app.core.timeutil import now_cn
from app.providers.base import BaseProvider, HttpClient, ProviderResult, to_float

CLIST = "https://push2.eastmoney.com/api/qt/clist/get"
FUND_BASIC = "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNNBasicInformation"
HEADERS = {"Referer": "https://quote.eastmoney.com/"}
FIELDS = "f2,f3,f5,f6,f8,f12,f13,f14,f20,f26"
FS = "b:MK0021,b:MK0022,b:MK0023,b:MK0024"


@dataclass
class EtfRow:
    code: str
    symbol: str
    name: str
    market: str
    price: Optional[float]
    change_pct: Optional[float]
    amount: Optional[float]
    mktcap: Optional[float]
    list_date: Optional[date]


class EtfUniverseProvider(BaseProvider):
    code = "eastmoney_etf"
    name = "ETF 全量目录"
    category = "market"

    async def fetch_list(self, interval: float = 0.25) -> tuple[list[EtfRow], int, list[str]]:
        rows: list[EtfRow] = []
        errors: list[str] = []
        total = 0
        async with HttpClient(min_interval=interval) as client:
            for page in range(1, 30):
                params = {
                    "pn": page,
                    "pz": 100,
                    "po": 1,
                    "np": 1,
                    "ut": "bd1d9ddb04089700cf9c27f6f7426281",
                    "fltt": 2,
                    "invt": 2,
                    "fid": "f12",
                    "fs": FS,
                    "fields": FIELDS,
                }
                try:
                    resp = await client.get(CLIST, params=params, headers=HEADERS)
                    payload = resp.json()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"page {page}: {str(exc)[:120]}")
                    break
                data = payload.get("data") or {}
                diff = data.get("diff") or []
                total = int(data.get("total") or 0)
                if not diff:
                    break
                for raw in diff:
                    code = str(raw.get("f12") or "").strip()
                    if len(code) != 6 or not code.isdigit():
                        continue
                    market = "SH" if raw.get("f13") == 1 else "SZ"
                    list_date = None
                    list_raw = raw.get("f26")
                    if isinstance(list_raw, int) and list_raw > 19000101:
                        try:
                            list_date = datetime.strptime(str(list_raw), "%Y%m%d").date()
                        except ValueError:
                            list_date = None
                    rows.append(
                        EtfRow(
                            code=code,
                            symbol=f"{market.lower()}{code}",
                            name=str(raw.get("f14") or "").strip(),
                            market=market,
                            price=to_float(raw.get("f2")),
                            change_pct=to_float(raw.get("f3")),
                            amount=to_float(raw.get("f6")),
                            mktcap=to_float(raw.get("f20")),
                            list_date=list_date,
                        )
                    )
                if len(rows) >= total:
                    break
        return rows, total, errors

    async def fetch_detail(self, code: str, interval: float = 0.4, retries: int = 2) -> dict:
        """基金详情：单位净值、报告期净资产、成立日、跟踪指数。失败返回带 _error 的字典。"""
        params = {
            "FCODE": code,
            "deviceid": "1",
            "plat": "Android",
            "product": "EFund",
            "version": "6.3.8",
        }
        last_error = ""
        async with HttpClient(min_interval=interval) as client:
            for attempt in range(retries):
                try:
                    resp = await client.get(FUND_BASIC, params=params, headers={"User-Agent": "Mozilla/5.0"})
                    payload = resp.json()
                    datas = payload.get("Datas") or {}
                    if datas:
                        return {
                            "nav": to_float(datas.get("DWJZ")),
                            "endnav": to_float(str(datas.get("ENDNAV") or "").replace(",", "")),
                            "estab_date": datas.get("ESTABDATE"),
                            "index_name": (datas.get("INDEXNAME") or "").strip(),
                            "fund_type": (datas.get("FTYPE") or "").strip(),
                            "name": (datas.get("SHORTNAME") or "").strip(),
                        }
                    last_error = f"empty Datas, ErrCode={payload.get('ErrCode')}"
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)[:160]
                if attempt < retries - 1:
                    await asyncio.sleep(0.8 * (attempt + 1))
        return {"_error": last_error}

    async def healthcheck(self) -> ProviderResult:
        rows, total, errors = await self.fetch_list()
        return ProviderResult(
            provider=self.code,
            ok=bool(rows),
            records=len(rows),
            message=f"ETF 列表：接口报告 {total}，解析 {len(rows)}",
            errors=errors[:3],
            detail={"total": total},
        )


def listed_days(list_date: Optional[date]) -> Optional[int]:
    if not list_date:
        return None
    return (now_cn().date() - list_date).days


ULIST = "https://push2.eastmoney.com/api/qt/ulist.np/get"


class EtfSupplementProvider(BaseProvider):
    """按代码段批量扫描，补齐列表接口不覆盖的品种（债券、REIT、货币）。

    东财 ETF 板块实测不含债券 ETF（511010/511260）与 REIT（508000），
    但批量行情接口支持一次 50 个 secid，且不存在的代码会自动省略。
    """

    code = "eastmoney_etf_supplement"
    name = "ETF 代码段补充扫描"
    category = "market"

    async def scan(self, segments: list[dict], batch_size: int = 50, interval: float = 0.25) -> tuple[list[EtfRow], list[str]]:
        rows: list[EtfRow] = []
        errors: list[str] = []
        async with HttpClient(min_interval=interval) as client:
            for seg in segments:
                market = str(seg.get("market", "1"))
                prefix = str(seg.get("prefix", ""))
                start = int(seg.get("start", 0))
                end = int(seg.get("end", 999))
                codes = [f"{market}.{prefix}{i:03d}" for i in range(start, end + 1)]
                for idx in range(0, len(codes), batch_size):
                    batch = codes[idx : idx + batch_size]
                    try:
                        resp = await client.get(
                            ULIST,
                            params={"secids": ",".join(batch), "fields": FIELDS, "fltt": "2", "invt": "2"},
                            headers=HEADERS,
                        )
                        diff = (resp.json().get("data") or {}).get("diff") or []
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"{prefix}{idx}: {str(exc)[:100]}")
                        continue
                    for raw in diff:
                        name = str(raw.get("f14") or "")
                        if "ETF" not in name and "REIT" not in name:
                            continue
                        raw_code = str(raw.get("f12") or "")
                        if len(raw_code) != 6:
                            continue
                        list_date = None
                        list_raw = raw.get("f26")
                        if isinstance(list_raw, int) and list_raw > 19000101:
                            try:
                                list_date = datetime.strptime(str(list_raw), "%Y%m%d").date()
                            except ValueError:
                                list_date = None
                        rows.append(
                            EtfRow(
                                code=raw_code,
                                symbol=f"{'sh' if market == '1' else 'sz'}{raw_code}",
                                name=name,
                                market="SH" if market == "1" else "SZ",
                                price=to_float(raw.get("f2")),
                                change_pct=to_float(raw.get("f3")),
                                amount=to_float(raw.get("f6")),
                                mktcap=to_float(raw.get("f20")),
                                list_date=list_date,
                            )
                        )
        return rows, errors

    async def healthcheck(self) -> ProviderResult:
        rows, errors = await self.scan([{"market": "1", "prefix": "511", "start": 0, "end": 49}])
        return ProviderResult(provider=self.code, ok=bool(rows), records=len(rows), message=f"扫描 {len(rows)} 只", errors=errors[:3])


NAV_HISTORY = "https://api.fund.eastmoney.com/f10/lsjz"


class FundNavProvider(BaseProvider):
    """历史单位净值（天天基金 f10/lsjz）。

    用途：计算折溢价的历史稳定性。**不能用于跟踪误差**——跟踪误差需要
    指数序列，当前无可靠免费源；用净值怼价格只会得到折溢价波动，两者含义不同。

    注意：货币 ETF 的 DWJZ 是"每万份收益"口径（如 100.93 / 0.274），
    调用方必须按 category 排除，不能直接参与折溢价计算。
    """

    code = "eastmoney_fund_nav"
    name = "基金历史净值"
    category = "market"

    async def fetch_nav_history(
        self, code: str, limit: int = 120, interval: float = 0.3
    ) -> list[tuple[date, float]]:
        async with HttpClient(min_interval=interval) as client:
            resp = await client.get(
                NAV_HISTORY,
                params={"fundCode": code, "pageIndex": 1, "pageSize": limit},
                headers={"Referer": "https://fundf10.eastmoney.com/"},
            )
            payload = resp.json()
        rows = ((payload.get("Data") or {}).get("LSJZList")) or []
        points: list[tuple[date, float]] = []
        for row in rows:
            try:
                trade_date = datetime.strptime(str(row.get("FSRQ")), "%Y-%m-%d").date()
                nav = float(str(row.get("DWJZ")))
            except (TypeError, ValueError):
                continue
            if nav > 0:
                points.append((trade_date, nav))
        points.sort(key=lambda item: item[0])
        return points

    async def healthcheck(self) -> ProviderResult:
        points = await self.fetch_nav_history("510300", limit=5)
        return ProviderResult(
            provider=self.code,
            ok=bool(points),
            records=len(points),
            message=f"510300 取到 {len(points)} 条净值",
        )
