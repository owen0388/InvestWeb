"""全市场数据访问层（Phase 1）。

上层统一通过 MarketRepository 取数据，不要各自请求 InvestData。
"""
from app.datasource.investdata import (  # noqa: F401
    AmbiguousSymbol,
    Bar,
    BarSeries,
    InvestDataClient,
    InvestDataError,
)
from app.datasource.repository import (  # noqa: F401
    MarketRepository,
    get_repository,
)

__all__ = [
    "InvestDataClient", "MarketRepository", "get_repository",
    "Bar", "BarSeries", "InvestDataError", "AmbiguousSymbol",
]
