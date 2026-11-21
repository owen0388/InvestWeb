"""统一行情访问层。

上层（指标计算、评分、排名、页面）只应通过这里取数据，不允许各自 new 一个
InvestDataClient，也不允许自己拼 HTTP 请求。

当前后端是 InvestData HTTP API。之所以不直接读它的 SQLite：
- 库里只存 raw，qfq/hfq 是 API 层按复权因子实时派生的，直读会拿到未复权价
- API 实测全市场约 2.3 分钟（同机），满足每日批量计算
若未来 API 成为瓶颈，替换本文件内部实现即可，上层不用改。
"""
from __future__ import annotations

from typing import Iterable, Optional

from app.core.config import settings
from app.datasource.investdata import BarSeries, InvestDataClient

# 本地库用 sh/sz/bj 前缀区分市场，InvestData 用纯代码 + market 字段
PREFIX_TO_MARKET = {"sh": "SH", "sz": "SZ", "bj": "BJ"}


class MarketRepository:
    """行情读取的统一入口。"""

    def __init__(self, client: Optional[InvestDataClient] = None):
        self._client = client or InvestDataClient(
            base_url=settings.INVESTDATA_BASE_URL,
            timeout=settings.INVESTDATA_TIMEOUT,
        )
        self._cache: dict[str, str] = {}

    # ---------- 标的 ----------
    def resolve(self, symbol: str, instrument_type: Optional[str] = None) -> str:
        """代码 → instrument_id。带缓存，减少重复查询。"""
        key = f"{symbol}:{instrument_type or ''}"
        if key in self._cache:
            return self._cache[key]
        inst = self._client.resolve(symbol, instrument_type=instrument_type)
        self._cache[key] = inst["instrument_id"]
        return inst["instrument_id"]

    def resolve_local_symbol(self, local_symbol: str) -> tuple[str, str]:
        """把本地库的 sh600519 形式转成 (纯代码, instrument_type 提示)。

        注意：本地 sh000001 是上证指数、sz000001 是平安银行，
        前缀决定了 instrument_type，不能只按代码猜。
        """
        prefix = local_symbol[:2].lower()
        code = local_symbol[2:]
        market = PREFIX_TO_MARKET.get(prefix)
        if not market:
            return local_symbol, "stock"
        # 指数代码段：000xxx / 399xxx / 899xxx 在不同市场含义不同，交给调用方指定类型
        return code, market

    def list_by_type(self, instrument_type: str, limit: Optional[int] = None) -> list[dict]:
        return self._client.list_instruments(instrument_type=instrument_type, limit=limit)

    def industries(self, keyword: Optional[str] = None, limit: int = 100) -> list[dict]:
        return self._client.industries(keyword=keyword, limit=limit)

    def industry_members(self, industry_id: str, limit: int = 2000) -> list[dict]:
        return self._client.industry_members(industry_id, limit=limit)

    # ---------- 行情 ----------
    def bars(self, instrument_id: str, days: int = 260, adjustment: str = "qfq") -> BarSeries:
        return self._client.bars(instrument_id, days=days, adjustment=adjustment)

    def close_series(self, instrument_id: str, days: int = 260, adjustment: str = "qfq") -> list[float]:
        """只要收盘价序列，供技术指标使用（默认前复权）。"""
        return self._client.bars(instrument_id, days=days, adjustment=adjustment).closes()

    def bars_batch(self, instrument_ids: Iterable[str], days: int = 260,
                   adjustment: str = "qfq") -> dict[str, BarSeries]:
        return self._client.bars_batch(instrument_ids, days=days, adjustment=adjustment)

    # ---------- 元信息 ----------
    def latest_trade_date(self) -> Optional[str]:
        return self._client.latest_trade_date()

    def is_ready(self) -> bool:
        try:
            return (self._client.ready() or {}).get("database") == "ok"
        except Exception:  # noqa: BLE001
            return False

    def close(self) -> None:
        self._client.close()


_repo: Optional[MarketRepository] = None


def get_repository() -> MarketRepository:
    """进程内单例，复用 HTTP 连接。"""
    global _repo
    if _repo is None:
        _repo = MarketRepository()
    return _repo
