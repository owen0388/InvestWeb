"""新浪财经行情适配器（公开快照接口，个人研究用途）。

已实测可用：A 股指数/个股/ETF、港股、美股指数、COMEX 贵金属与原油、
伦铜/伦铝、在岸人民币。返回 GBK 编码文本，需显式解码。
"""
from __future__ import annotations

from datetime import date, datetime, time as dtime

from app.core.timeutil import CN_TZ, now_cn, parse_dt
from app.providers.base import BaseProvider, HttpClient, ProviderResult, Quote, to_float

SINA_URL = "https://hq.sinajs.cn/list="
BATCH_SIZE = 40


def _is_cn_trading_time(dt: datetime) -> bool:
    if dt.weekday() >= 5:
        return False
    t = dt.time()
    return dtime(9, 30) <= t <= dtime(11, 30) or dtime(13, 0) <= t <= dtime(15, 0)


def _data_type(symbol: str, quote_dt: datetime) -> str:
    now = now_cn()
    age_min = (now - quote_dt).total_seconds() / 60 if quote_dt else 1e9
    if symbol.startswith(("sh", "sz", "bj")):
        if quote_dt.date() == now.date() and _is_cn_trading_time(now) and age_min <= 10:
            return "realtime"
        return "close"
    if age_min <= 15:
        return "realtime"
    if age_min <= 24 * 60:
        return "delayed"
    return "close"


def _parse_cn(fields: list[str], symbol: str) -> Quote | None:
    if len(fields) < 32:
        return None
    price = to_float(fields[3])
    prev = to_float(fields[2])
    if price is None or price <= 0:
        return None
    quote_dt = parse_dt(f"{fields[30]} {fields[31]}") or now_cn()
    pct = (price / prev - 1) * 100 if prev else None
    return Quote(
        symbol=symbol,
        name=fields[0],
        price=price,
        prev_close=prev,
        open=to_float(fields[1]),
        high=to_float(fields[4]),
        low=to_float(fields[5]),
        volume=to_float(fields[8]),
        amount=to_float(fields[9]),
        change_pct=round(pct, 4) if pct is not None else None,
        quote_time=quote_dt,
        trade_date=quote_dt.date(),
        currency="CNY",
        data_type=_data_type(symbol, quote_dt),
    )


def _parse_hk(fields: list[str], symbol: str) -> Quote | None:
    if len(fields) < 19:
        return None
    price = to_float(fields[6])
    if price is None or price <= 0:
        return None
    quote_dt = parse_dt(f"{fields[17]} {fields[18]}") or now_cn()
    return Quote(
        symbol=symbol,
        name=fields[1],
        price=price,
        prev_close=to_float(fields[3]),
        open=to_float(fields[2]),
        high=to_float(fields[4]),
        low=to_float(fields[5]),
        change_pct=to_float(fields[8]),
        volume=to_float(fields[12]),
        amount=to_float(fields[11]),
        quote_time=quote_dt,
        trade_date=quote_dt.date(),
        currency="HKD",
        data_type=_data_type(symbol, quote_dt),
    )


def _parse_global_future(fields: list[str], symbol: str) -> Quote | None:
    if len(fields) < 14:
        return None
    price = to_float(fields[0])
    if price is None or price <= 0:
        return None
    prev = to_float(fields[7])
    quote_dt = parse_dt(f"{fields[12]} {fields[6]}") or now_cn()
    pct = (price / prev - 1) * 100 if prev else None
    return Quote(
        symbol=symbol,
        name=fields[13] or symbol,
        price=price,
        prev_close=prev,
        open=to_float(fields[8]),
        high=to_float(fields[4]),
        low=to_float(fields[5]),
        change_pct=round(pct, 4) if pct is not None else None,
        quote_time=quote_dt,
        trade_date=quote_dt.date(),
        currency="USD",
        data_type=_data_type(symbol, quote_dt),
    )


def _parse_us(fields: list[str], symbol: str) -> Quote | None:
    if len(fields) < 5:
        return None
    price = to_float(fields[1])
    if price is None or price <= 0:
        return None
    change = to_float(fields[4]) or 0.0
    quote_dt = parse_dt(fields[3]) or now_cn()
    return Quote(
        symbol=symbol,
        name=fields[0],
        price=price,
        prev_close=price - change,
        change_pct=to_float(fields[2]),
        open=to_float(fields[5]),
        high=to_float(fields[6]),
        low=to_float(fields[7]),
        quote_time=quote_dt,
        trade_date=quote_dt.date(),
        currency="USD",
        data_type=_data_type(symbol, quote_dt),
    )


def _parse_fx(fields: list[str], symbol: str) -> Quote | None:
    if len(fields) < 11:
        return None
    price = to_float(fields[8])
    if price is None or price <= 0:
        return None
    quote_dt = parse_dt(f"{now_cn().date()} {fields[0]}") or now_cn()
    return Quote(
        symbol=symbol,
        name=fields[9] or "USD/CNY",
        price=price,
        prev_close=to_float(fields[3]),
        change_pct=to_float(fields[10]),
        quote_time=quote_dt,
        trade_date=quote_dt.date(),
        currency="CNY",
        data_type=_data_type(symbol, quote_dt),
    )


def parse_sina_line(line: str) -> Quote | None:
    if "hq_str_" not in line or '="' not in line:
        return None
    head, _, rest = line.partition('="')
    symbol = head.split("hq_str_")[-1].strip()
    payload = rest.rsplit('";', 1)[0]
    if not payload:
        return None
    fields = payload.split(",")
    if symbol.startswith("sh") or symbol.startswith("sz"):
        return _parse_cn(fields, symbol)
    if symbol.startswith("hk"):
        return _parse_hk(fields, symbol)
    if symbol.startswith("hf_"):
        return _parse_global_future(fields, symbol)
    if symbol.startswith("gb_"):
        return _parse_us(fields, symbol)
    if symbol.startswith("fx_"):
        return _parse_fx(fields, symbol)
    return None


class SinaQuoteProvider(BaseProvider):
    code = "sina_quote"
    name = "新浪财经行情"
    category = "market"

    async def fetch_quotes(self, symbols: list[str]) -> ProviderResult:
        if not symbols:
            return ProviderResult(provider=self.code, ok=True, message="无待采集代码")
        quotes: list[Quote] = []
        errors: list[str] = []
        async with HttpClient(min_interval=0.4) as client:
            for i in range(0, len(symbols), BATCH_SIZE):
                batch = symbols[i : i + BATCH_SIZE]
                url = SINA_URL + ",".join(batch)
                try:
                    resp = await client.get(url, headers={"Referer": "https://finance.sina.com.cn/"})
                    text = resp.content.decode("gbk", errors="replace")
                except Exception as exc:  # noqa: BLE001
                    errors.append(str(exc)[:200])
                    continue
                for line in text.splitlines():
                    try:
                        quote = parse_sina_line(line)
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"解析失败: {exc}"[:200])
                        continue
                    if quote and quote.trade_date:
                        quotes.append(quote)
                    elif line.strip():
                        errors.append(f"未返回有效数据: {line[:40]}")
        return ProviderResult(
            provider=self.code,
            ok=bool(quotes),
            records=len(quotes),
            failed=max(0, len(symbols) - len(quotes)),
            message=f"成功 {len(quotes)}/{len(symbols)}",
            detail={"quotes": quotes},
            errors=errors[:10],
        )

    async def healthcheck(self) -> ProviderResult:
        return await self.fetch_quotes(["sh000300"])
