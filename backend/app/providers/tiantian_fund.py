"""天天基金（fund.eastmoney.com）净值与规模。

为什么需要它：InvestData 只有行情，没有 ETF 净值（NAV）与规模，
而 ETF 质量分的「折溢价稳定性(20%)」和「规模(30%)」必须用到这两项。

为什么不用 push2his：那是东财的行情接口，高频会被封 IP；
fund.eastmoney.com 是基金频道，独立风控，且返回结构稳定。

数据来源：`https://fund.eastmoney.com/pingzhongdata/{code}.js`
一次返回该基金全部历史，包含：
- fS_name              基金名称
- Data_netWorthTrend   单位净值序列 [{x: 毫秒时间戳, y: 净值, equityReturn}]
- Data_fluctuationScale 规模（季度）{categories: [日期], series: [{y: 亿元}]}
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Optional

import httpx

URL_TMPL = "https://fund.eastmoney.com/pingzhongdata/{code}.js"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def _extract(txt: str, var: str) -> Optional[str]:
    m = re.search(rf"var {var}\s*=\s*(.+?);\s*(?:/\*|$)", txt, re.S)
    return m.group(1).strip() if m else None


class TiantianFundProvider:
    """ETF 净值与规模。只读，不做推荐判断。"""

    code = "tiantian_fund"
    name = "天天基金（净值/规模）"

    def __init__(self, timeout: float = 20.0):
        self._client = httpx.Client(
            timeout=timeout, headers={"User-Agent": UA}, follow_redirects=True
        )

    def fetch(self, code: str) -> dict:
        """取一只 ETF 的净值序列与规模。

        返回 {ok, name, navs: [(date, nav)], scale: 亿元|None, scale_date, error}
        失败不抛异常——批量任务里单只失败不应中断整体。
        """
        try:
            resp = self._client.get(URL_TMPL.format(code=code))
            if resp.status_code != 200:
                return {"ok": False, "error": f"HTTP {resp.status_code}"}
            txt = resp.text
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": str(exc)[:100]}

        name_raw = _extract(txt, "fS_name")
        try:
            name = json.loads(name_raw) if name_raw else None
        except Exception:  # noqa: BLE001
            name = None

        navs: list[tuple[date, float]] = []
        nav_raw = _extract(txt, "Data_netWorthTrend")
        if nav_raw:
            try:
                for item in json.loads(nav_raw):
                    ts = item.get("x")
                    val = item.get("y")
                    if ts is None or val is None:
                        continue
                    d = datetime.fromtimestamp(ts / 1000).date()
                    navs.append((d, float(val)))
            except Exception:  # noqa: BLE001
                pass

        scale = None
        scale_date = None
        scale_raw = _extract(txt, "Data_fluctuationScale")
        if scale_raw:
            try:
                obj = json.loads(scale_raw)
                cats = obj.get("categories") or []
                series = obj.get("series") or []
                if cats and series:
                    last = series[-1]
                    val = last.get("y")
                    if val is not None:
                        scale = float(val)  # 单位：亿元
                        scale_date = cats[-1]
            except Exception:  # noqa: BLE001
                pass

        if not navs:
            return {"ok": False, "name": name, "error": "未解析到净值序列"}

        return {
            "ok": True,
            "name": name,
            "navs": navs,
            "scale": scale,
            "scale_date": scale_date,
            "error": None,
        }

    def close(self) -> None:
        self._client.close()
