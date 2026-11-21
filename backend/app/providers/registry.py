"""Provider 注册表：代码 -> 实例，供采集服务按数据源配置调用。"""
from __future__ import annotations

from app.providers.base import BaseProvider
from app.providers.eastmoney import EastmoneyKlineProvider, EastmoneyNewsProvider
from app.providers.macro import FredProvider, WorldBankProvider
from app.providers.optional import AkshareProvider, GdeltProvider, TushareProvider
from app.providers.sina_quote import SinaQuoteProvider

_PROVIDERS: dict[str, BaseProvider] = {
    "sina_quote": SinaQuoteProvider(),
    "eastmoney_kline": EastmoneyKlineProvider(),
    "eastmoney_news": EastmoneyNewsProvider(),
    "worldbank_macro": WorldBankProvider(),
    "fred_macro": FredProvider(),
    "gdelt_news": GdeltProvider(),
    "tushare_pro": TushareProvider(),
    "akshare": AkshareProvider(),
}


def get_provider(code: str) -> BaseProvider | None:
    return _PROVIDERS.get(code)


def all_providers() -> dict[str, BaseProvider]:
    return dict(_PROVIDERS)
