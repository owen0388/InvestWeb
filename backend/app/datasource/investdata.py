"""InvestData HTTP 客户端。

只做一件事：把 InvestData 的接口调用封装成稳定方法，不掺业务逻辑。

设计约束（来自 Phase 1 要求）：
- 所有上层模块通过这一层取行情，不允许各自请求 InvestData
- 保留 InvestData 的 quality_status 与 adjustment_type，不做静默过滤
- 代码歧义时明确报错，不猜测（000001 既是平安银行也是上证指数）
- 分页必须完整取回，不能只取第一页就当成全量
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Optional

import httpx


class InvestDataError(RuntimeError):
    """InvestData 调用失败的统一异常。"""


class AmbiguousSymbol(InvestDataError):
    """代码对应多个标的（如 000001）。必须指定 instrument_type。"""

    def __init__(self, symbol: str, candidates: list[dict]):
        self.symbol = symbol
        self.candidates = candidates
        types = ", ".join(f"{c.get('instrument_type')}:{c.get('instrument_id')[:8]}" for c in candidates[:5])
        super().__init__(f"代码 {symbol} 对应 {len(candidates)} 个标的（{types}），请指定 instrument_type")


@dataclass
class Bar:
    trade_date: str
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None
    amount: Optional[float] = None
    pre_close: Optional[float] = None
    turnover_rate: Optional[float] = None
    change_pct: Optional[float] = None
    quality_status: str = "PASS"
    quality_flags: Optional[str] = None

    @property
    def is_clean(self) -> bool:
        return (self.quality_status or "").upper() == "PASS"


@dataclass
class BarSeries:
    instrument_id: str
    symbol: str
    name: Optional[str]
    instrument_type: str
    adjustment: str
    bars: list[Bar] = field(default_factory=list)
    first_date: Optional[str] = None
    last_date: Optional[str] = None
    provider: Optional[str] = None

    def closes(self) -> list[float]:
        return [b.close for b in self.bars]

    def clean_bars(self) -> list[Bar]:
        """只返回质量通过的数据。异常数据不删除，由调用方决定怎么处理。"""
        return [b for b in self.bars if b.is_clean]

    @property
    def length(self) -> int:
        return len(self.bars)


class InvestDataClient:
    """InvestData 只读客户端。

    默认地址 127.0.0.1:8100（同机部署）；本地开发通过 SSH 隧道转发到同一地址。
    """

    def __init__(self, base_url: str = "http://127.0.0.1:8100", timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = httpx.Client(timeout=timeout, follow_redirects=True)

    # ---------- 基础 ----------
    def ready(self) -> dict:
        """就绪检查。返回 latest_trade_date，用于判断数据是否新鲜。"""
        return self._get("/api/v1/health/ready")

    def latest_trade_date(self) -> Optional[str]:
        return (self.ready() or {}).get("latest_trade_date")

    def coverage(self) -> dict:
        return self._get("/api/v1/quality/coverage")

    # ---------- 标的 ----------
    def list_instruments(
        self,
        instrument_type: Optional[str] = None,
        keyword: Optional[str] = None,
        page_size: int = 500,
        max_pages: int = 40,
        limit: Optional[int] = None,
    ) -> list[dict]:
        """分页取标的。

        page_size 是「每页条数」，limit 才是「总共要多少条」。
        传 limit 时取够就停，避免为了 3 条记录翻完整库。
        """
        out: list[dict] = []
        page = 1
        while page <= max_pages:
            params: dict[str, Any] = {"page": page, "page_size": page_size}
            if instrument_type:
                params["instrument_type"] = instrument_type
            if keyword:
                params["keyword"] = keyword
            data = self._get("/api/v1/instruments", **params)
            items = data.get("items") or []
            out.extend(items)
            if limit is not None and len(out) >= limit:
                break
            total = data.get("total") or 0
            if not items or len(out) >= total:
                break
            page += 1
        return out[:limit] if limit is not None else out

    def resolve(self, symbol: str, instrument_type: Optional[str] = None) -> dict:
        """按代码定位唯一标的。歧义直接抛 AmbiguousSymbol，不猜。"""
        params: dict[str, Any] = {"symbol": symbol, "page_size": 50}
        if instrument_type:
            params["instrument_type"] = instrument_type
        try:
            data = self._get("/api/v1/instruments", **params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 409:
                detail = (exc.response.json() or {}).get("detail") or {}
                raise AmbiguousSymbol(symbol, detail.get("candidates") or []) from exc
            raise
        items = data.get("items") or []
        if not items:
            raise InvestDataError(f"未找到代码 {symbol}")
        if len(items) > 1:
            raise AmbiguousSymbol(symbol, items)
        return items[0]

    def industries(self, keyword: Optional[str] = None, limit: int = 100) -> list[dict]:
        params: dict[str, Any] = {"limit": limit}
        if keyword:
            params["keyword"] = keyword
        return self._get("/api/v1/industries", **params)

    def industry_members(self, instrument_id: str, limit: int = 2000) -> list[dict]:
        data = self._get(f"/api/v1/industries/{instrument_id}/members", limit=limit)
        return data if isinstance(data, list) else (data.get("items") or [])

    # ---------- 行情 ----------
    def bars(
        self,
        instrument_id: str,
        days: int = 260,
        adjustment: str = "qfq",
    ) -> BarSeries:
        """取最近 N 根日线。

        接口按 trade_date **升序**返回（从最早开始），因此不能简单取第一页当最新。
        做法：先探明该标的的最后交易日，再用日期区间请求，最后按日期排序取尾部。

        返回长度可能小于 days（新股/停牌），不做补齐。
        """
        if adjustment not in ("raw", "qfq", "hfq"):
            raise ValueError(f"不支持的复权口径: {adjustment}")

        # 1) 探明覆盖范围（只取 1 条，代价很小）
        probe = self._get(
            "/api/v1/bars/daily",
            instrument_id=instrument_id,
            adjustment_type=adjustment,
            page_size=1,
            page=1,
        )
        cov = probe.get("coverage") or {}
        last = cov.get("last")
        total = int(probe.get("total") or 0)
        if not last or total == 0:
            return BarSeries(
                instrument_id=instrument_id, symbol=probe.get("symbol") or "",
                name=None, instrument_type=probe.get("instrument_type") or "",
                adjustment=adjustment, provider=probe.get("source_provider"),
            )

        # 2) 按自然日回推（交易日约占自然日 70%，多留余量覆盖长假）
        end_dt = date.fromisoformat(last)
        start_dt = end_dt - timedelta(days=int(days * 1.7) + 15)

        # 3) 区间请求，必要时翻页
        items: list[dict] = []
        page = 1
        while page <= 5:
            data = self._get(
                "/api/v1/bars/daily",
                instrument_id=instrument_id,
                adjustment_type=adjustment,
                start_date=start_dt.isoformat(),
                end_date=end_dt.isoformat(),
                page_size=1000,
                page=page,
            )
            chunk = data.get("items") or []
            items.extend(chunk)
            if len(chunk) < 1000:
                break
            page += 1

        if not items:
            # 区间内无数据（极端停牌），退回全量取尾部
            data = self._get(
                "/api/v1/bars/daily", instrument_id=instrument_id,
                adjustment_type=adjustment, page_size=1000, page=1,
            )
            items = data.get("items") or []

        items.sort(key=lambda x: x.get("trade_date") or "")
        selected = items[-days:] if len(items) > days else items
        bars = [self._to_bar(x) for x in selected]
        return BarSeries(
            instrument_id=instrument_id,
            symbol=probe.get("symbol") or "",
            name=None,
            instrument_type=probe.get("instrument_type") or "",
            adjustment=adjustment,
            bars=bars,
            first_date=bars[0].trade_date if bars else cov.get("first"),
            last_date=bars[-1].trade_date if bars else cov.get("last"),
            provider=probe.get("source_provider"),
        )

    def bars_batch(
        self,
        instrument_ids: Iterable[str],
        days: int = 260,
        adjustment: str = "qfq",
    ) -> dict[str, BarSeries]:
        """批量取。单只失败不影响其他标的（返回空序列并记录）。"""
        out: dict[str, BarSeries] = {}
        for iid in instrument_ids:
            try:
                out[iid] = self.bars(iid, days=days, adjustment=adjustment)
            except Exception as exc:  # noqa: BLE001
                out[iid] = BarSeries(
                    instrument_id=iid, symbol="", name=None,
                    instrument_type="", adjustment=adjustment, bars=[],
                )
                out[iid].error = str(exc)[:200]  # type: ignore[attr-defined]
        return out

    # ---------- 内部 ----------
    def _get(self, path: str, **params: Any) -> Any:
        url = f"{self.base_url}{path}"
        try:
            resp = self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise InvestDataError(f"InvestData 连接失败: {exc}") from exc
        if resp.status_code == 409:
            raise httpx.HTTPStatusError("ambiguous", request=resp.request, response=resp)
        if resp.status_code >= 400:
            raise InvestDataError(f"{path} 返回 {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    @staticmethod
    def _to_bar(row: dict) -> Bar:
        return Bar(
            trade_date=row["trade_date"],
            open=float(row.get("open") or 0),
            high=float(row.get("high") or 0),
            low=float(row.get("low") or 0),
            close=float(row.get("close") or 0),
            volume=row.get("volume"),
            amount=row.get("amount"),
            pre_close=row.get("pre_close"),
            turnover_rate=row.get("turnover_rate"),
            change_pct=row.get("change_pct"),
            quality_status=row.get("quality_status") or "PASS",
            quality_flags=row.get("quality_flags"),
        )

    def close(self) -> None:
        self._client.close()
