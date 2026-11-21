"""东方财富 / 腾讯 / 新浪 日线适配器，带自动回退。

东方财富公开接口在短时间高频访问后可能直接断开连接；为保证「数据缺失不
等于系统停摆」，日线采集按 东方财富 → 腾讯 → 新浪 的顺序回退，并在返回
结果中记录实际来源，页面与数据库都保留真实来源标记。
"""
from __future__ import annotations

from datetime import date, datetime

from app.core.timeutil import now_cn, parse_dt
from app.providers.base import BaseProvider, Bar, HttpClient, NewsItem, ProviderResult, to_float

KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
NEWS_URL = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"
TENCENT_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
SINA_KLINE_URL = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"


# 内部代码 → 腾讯日线代码（仅港股/美股指数，用于补齐跨境类历史序列）
TENCENT_MAP = {
    "hkHSI": "hkHSI",
    "hk00700": "hk00700",
    "gb_$dji": "us.DJI",
    "gb_$ixic": "us.IXIC",
    "gb_$inx": "us.INX",
}

# 内部代码 → 新浪外盘期货代码（COMEX/LME/NYMEX）
HF_MAP = {
    "hf_GC": "GC",
    "hf_SI": "SI",
    "hf_CL": "CL",
    "hf_NG": "NG",
    "hf_CAD": "CAD",
    "hf_AHD": "AHD",
}

TENCENT_HISTORY_SYMBOLS = list(TENCENT_MAP.keys()) + list(HF_MAP.keys())


def to_secid(symbol: str) -> str | None:
    """把内部代码转换为东方财富 secid。仅支持沪深北。"""
    if symbol.startswith("sh"):
        return f"1.{symbol[2:]}"
    if symbol.startswith(("sz", "bj")):
        return f"0.{symbol[2:]}"
    return None


class EastmoneyKlineProvider(BaseProvider):
    code = "eastmoney_kline"
    name = "A 股日线（东方财富/腾讯/新浪回退）"
    category = "market"

    def __init__(self) -> None:
        # 单次运行内的熔断计数：某源连续失败 2 次后不再尝试，避免整轮采集被拖慢
        self._failures: dict[str, int] = {}

    async def _eastmoney(self, symbol: str, limit: int) -> list[Bar]:
        secid = to_secid(symbol)
        if not secid:
            return []
        params = {
            "secid": secid,
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": "1",
            "end": "20500101",
            "lmt": str(limit),
        }
        async with HttpClient(min_interval=0.35) as client:
            resp = await client.get(KLINE_URL, params=params)
            payload = resp.json()
        data = payload.get("data") or {}
        bars: list[Bar] = []
        for line in data.get("klines") or []:
            parts = line.split(",")
            if len(parts) < 7:
                continue
            try:
                trade_date = datetime.strptime(parts[0], "%Y-%m-%d").date()
            except ValueError:
                continue
            bars.append(
                Bar(
                    symbol=symbol,
                    trade_date=trade_date,
                    open=to_float(parts[1]),
                    close=to_float(parts[2]),
                    high=to_float(parts[3]),
                    low=to_float(parts[4]),
                    volume=to_float(parts[5]),
                    amount=to_float(parts[6]),
                    change_pct=to_float(parts[8]) if len(parts) > 8 else None,
                )
            )
        return bars

    async def _tencent(self, symbol: str, limit: int) -> list[Bar]:
        code = TENCENT_MAP.get(symbol, symbol)
        params = {"param": f"{code},day,,,{limit},qfq"}
        async with HttpClient(min_interval=0.2) as client:
            resp = await client.get(TENCENT_URL, params=params)
            payload = resp.json()
        node = (payload.get("data") or {}).get(code) or {}
        rows = node.get("qfqday") or node.get("day") or []
        bars: list[Bar] = []
        for row in rows:
            if len(row) < 6:
                continue
            try:
                trade_date = datetime.strptime(row[0], "%Y-%m-%d").date()
            except ValueError:
                continue
            bars.append(
                Bar(
                    symbol=symbol,
                    trade_date=trade_date,
                    open=to_float(row[1]),
                    close=to_float(row[2]),
                    high=to_float(row[3]),
                    low=to_float(row[4]),
                    volume=to_float(row[5]),
                )
            )
        return bars

    async def _sina(self, symbol: str, limit: int) -> list[Bar]:
        params = {"symbol": symbol, "scale": "240", "ma": "no", "datalen": str(limit)}
        async with HttpClient(min_interval=0.2) as client:
            resp = await client.get(SINA_KLINE_URL, params=params)
            rows = resp.json() or []
        bars: list[Bar] = []
        for row in rows:
            try:
                trade_date = datetime.strptime(row["day"], "%Y-%m-%d").date()
            except (KeyError, ValueError):
                continue
            bars.append(
                Bar(
                    symbol=symbol,
                    trade_date=trade_date,
                    open=to_float(row.get("open")),
                    close=to_float(row.get("close")),
                    high=to_float(row.get("high")),
                    low=to_float(row.get("low")),
                    volume=to_float(row.get("volume")),
                )
            )
        return bars

    async def _sina_global(self, symbol: str, limit: int) -> list[Bar]:
        code = HF_MAP[symbol]
        url = (
            "https://stock2.finance.sina.com.cn/futures/api/jsonp.php/var%20t=/"
            f"GlobalFuturesService.getGlobalFuturesDailyKLine?symbol={code}"
        )
        async with HttpClient(min_interval=0.3) as client:
            resp = await client.get(url, headers={"Referer": "https://finance.sina.com.cn/"})
            text = resp.text
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            return []
        import json

        rows = json.loads(text[start : end + 1])
        bars: list[Bar] = []
        for row in rows[-limit:]:
            try:
                trade_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
            except (KeyError, ValueError):
                continue
            bars.append(
                Bar(
                    symbol=symbol,
                    trade_date=trade_date,
                    open=to_float(row.get("open")),
                    close=to_float(row.get("close")),
                    high=to_float(row.get("high")),
                    low=to_float(row.get("low")),
                    volume=to_float(row.get("volume")),
                )
            )
        return bars

    async def fetch_history(self, symbol: str, limit: int = 250) -> ProviderResult:
        if symbol in HF_MAP:
            try:
                bars = await self._sina_global(symbol, limit)
            except Exception as exc:  # noqa: BLE001
                return ProviderResult(
                    provider=self.code, ok=False, message=f"{symbol} 外盘历史获取失败: {exc}"[:200]
                )
            return ProviderResult(
                provider=self.code,
                ok=bool(bars),
                records=len(bars),
                message=f"{symbol} 获取 {len(bars)} 根日线（来源 sina_global）" if bars else f"{symbol} 无外盘历史数据",
                detail={"bars": bars, "source_code": "sina_kline"},
            )
        if not to_secid(symbol) and symbol not in TENCENT_MAP:
            return ProviderResult(provider=self.code, ok=False, message="该代码暂无可用历史序列，跳过")
        errors: list[str] = []
        sources = [("tencent_kline", self._tencent), ("sina_kline", self._sina)]
        if to_secid(symbol):
            sources.insert(0, ("eastmoney_kline", self._eastmoney))
        for source_code, fetch in sources:
            if self._failures.get(source_code, 0) >= 2:
                continue
            try:
                bars = await fetch(symbol, limit)
            except Exception as exc:  # noqa: BLE001
                self._failures[source_code] = self._failures.get(source_code, 0) + 1
                errors.append(f"{source_code}: {str(exc)[:120]}")
                continue
            if bars:
                self._failures[source_code] = 0
                return ProviderResult(
                    provider=self.code,
                    ok=True,
                    records=len(bars),
                    message=f"{symbol} 获取 {len(bars)} 根日线（来源 {source_code}）",
                    detail={"bars": bars, "source_code": source_code},
                    errors=errors[:3],
                )
            errors.append(f"{source_code}: 返回空数据")
        return ProviderResult(
            provider=self.code,
            ok=False,
            records=0,
            message=f"{symbol} 所有日线来源均不可用",
            errors=errors[:5],
        )

    async def healthcheck(self) -> ProviderResult:
        return await self.fetch_history("sh000300", limit=5)


class EastmoneyNewsProvider(BaseProvider):
    code = "eastmoney_news"
    name = "东方财富财经新闻"
    category = "news"
    columns = [
        ("350", "要闻"),
    ]

    async def fetch_news(self, page_size: int = 60) -> ProviderResult:
        items: list[NewsItem] = []
        errors: list[str] = []
        async with HttpClient(min_interval=0.5) as client:
            for column, label in self.columns:
                params = {
                    "client": "web",
                    "biz": "web_news_col",
                    "column": column,
                    "order": "1",
                    "needInteractData": "0",
                    "page_index": "1",
                    "page_size": str(page_size),
                    "req_trace": "1",
                    "fields": "code,showTime,title,mediaName,summary,url,uniqueUrl",
                }
                try:
                    resp = await client.get(NEWS_URL, params=params)
                    data = resp.json().get("data") or {}
                    rows = data.get("list") or []
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{label}: {exc}"[:200])
                    continue
                for row in rows:
                    news_id = str(row.get("code") or "").strip()
                    title = (row.get("title") or "").strip()
                    if not news_id or not title:
                        continue
                    items.append(
                        NewsItem(
                            source_code=self.code,
                            external_id=news_id,
                            title=title,
                            summary=(row.get("summary") or "").strip()[:1200],
                            url=(row.get("uniqueUrl") or row.get("url") or "").strip(),
                            media=(row.get("mediaName") or label).strip(),
                            language="zh",
                            published_at=parse_dt(row.get("showTime")),
                            reliability="medium",
                            raw={"column": column, "showTime": row.get("showTime")},
                        )
                    )
        seen: set[str] = set()
        unique: list[NewsItem] = []
        for item in items:
            if item.external_id in seen:
                continue
            seen.add(item.external_id)
            unique.append(item)
        return ProviderResult(
            provider=self.code,
            ok=bool(unique),
            records=len(unique),
            message=f"获取 {len(unique)} 条新闻",
            detail={"items": unique},
            errors=errors[:5],
        )

    async def healthcheck(self) -> ProviderResult:
        return await self.fetch_news(page_size=3)
