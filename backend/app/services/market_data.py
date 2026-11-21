"""行情读取与通用指标计算（纯函数，便于测试）。"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import MacroObservation, MarketObservation


def load_close_series(db: Session, symbol: str, limit: int = 300) -> pd.Series:
    """优先使用东方财富日线（收盘、前复权），不足时回退到新浪快照。"""
    rows = db.execute(
        select(MarketObservation.trade_date, MarketObservation.close, MarketObservation.source_code)
        .where(
            MarketObservation.symbol == symbol,
            MarketObservation.close.is_not(None),
        )
        .order_by(MarketObservation.trade_date.desc())
        .limit(limit * 3)
    ).all()
    if not rows:
        return pd.Series(dtype="float64")
    # 同一品种可能来自多个来源（东方财富/腾讯/新浪/快照），选数据最完整的一组
    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(row[2], []).append(row)
    best_source = max(grouped, key=lambda code: len(grouped[code])) if grouped else None
    chosen = grouped.get(best_source, rows)
    data: dict[date, float] = {}
    for trade_date, close, _src in reversed(chosen):
        data[trade_date] = float(close)
    series = pd.Series(data).sort_index()
    return series.tail(limit)


def latest_quote(db: Session, symbol: str) -> Optional[MarketObservation]:
    return db.scalar(
        select(MarketObservation)
        .where(MarketObservation.symbol == symbol)
        .order_by(MarketObservation.trade_date.desc(), MarketObservation.observed_at.desc())
    )


def quote_with_change(db, symbol: str, obs: Optional[MarketObservation] = None) -> tuple[Optional[MarketObservation], Optional[float]]:
    """返回最新行情与涨跌幅；日线来源不带涨跌幅时，用前一日收盘实时计算。"""
    obs = obs or latest_quote(db, symbol)
    if obs is None:
        return None, None
    if obs.change_pct is not None:
        return obs, obs.change_pct
    series = load_close_series(db, symbol, 8)
    if len(series) >= 2:
        prev, last = float(series.iloc[-2]), float(series.iloc[-1])
        if prev > 0:
            return obs, round((last / prev - 1) * 100, 4)
    return obs, None


def returns(series: pd.Series) -> pd.Series:
    return series.pct_change().dropna()


def annualized_vol(series: pd.Series, window: int = 20) -> float | None:
    rets = returns(series.tail(window + 1))
    if len(rets) < 5:
        return None
    return float(rets.std() * np.sqrt(252))


def total_return(series: pd.Series, window: int) -> float | None:
    if len(series) < window + 1:
        return None
    start = series.iloc[-(window + 1)]
    end = series.iloc[-1]
    if start <= 0:
        return None
    return float(end / start - 1)


def max_drawdown(series: pd.Series) -> float | None:
    if len(series) < 10:
        return None
    running_max = series.cummax()
    dd = series / running_max - 1
    return float(dd.min())


def moving_average(series: pd.Series, window: int) -> float | None:
    if len(series) < window:
        return None
    return float(series.tail(window).mean())


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return float(max(low, min(high, value)))


def score_from_z(value: float | None, scale: float = 1.0, center: float = 50.0) -> float | None:
    if value is None:
        return None
    return clamp(center + 50.0 * np.tanh(value / scale), 0, 100)


def series_quality(series: pd.Series, expected_days: int = 120) -> float:
    """数据质量：长度 + 最近数据新鲜度。"""
    if series.empty:
        return 0.0
    length_score = min(1.0, len(series) / expected_days)
    last_date = series.index[-1]
    if hasattr(last_date, "date"):
        last_date = last_date.date()
    if not isinstance(last_date, date):
        return length_score * 0.7
    gap = (date.today() - last_date).days
    freshness = 1.0 if gap <= 5 else max(0.2, 1.0 - (gap - 5) / 30)
    return round(length_score * freshness, 3)


def staleness_factor(period_date: date | None, half_life_days: float = 180.0) -> float:
    if period_date is None:
        return 0.4
    age = max(0, (date.today() - period_date).days)
    return float(0.15 + 0.85 * (0.5 ** (age / half_life_days)))


def latest_macro(db: Session, indicator_code: str) -> MacroObservation | None:
    return db.scalar(
        select(MacroObservation)
        .where(
            MacroObservation.indicator_code == indicator_code,
            MacroObservation.value.is_not(None),
        )
        .order_by(MacroObservation.period.desc())
    )
