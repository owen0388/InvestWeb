"""历史回测（简化但严谨）。

为避免前视偏差：
- 每个调仓日只使用该日之前已经存在的收盘价计算评分；
- 不使用当前宏观数据回溯历史（宏观 vintage 数据尚未接入，直接排除）；
- 交易成本、换手、样本内外拆分与数据范围全部落库。
"""
from __future__ import annotations

from datetime import date
from typing import Optional

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.jsonutil import clean_json
from app.core.timeutil import now_cn
from app.models.entities import BacktestRun, Instrument
from app.services import config_service
from app.services.allocation_service import bounded_simplex_projection
from app.services.market_data import load_close_series, moving_average
from app.services.opportunity_service import decide_state, trend_features

REPRESENTATIVE = {
    "CN_EQ": "sh000300",
    "FUND": "sh510300",
    "BOND": "sh511010",
    "GOLD": "sh518880",
    "OVERSEAS": "sh513100",
    "CASH": "sh511880",
}
BENCHMARK = "sh000300"


def _score_from_history(series: pd.Series) -> float | None:
    if len(series) < 60:
        return None
    ma20 = moving_average(series, 20)
    ma60 = moving_average(series, 60)
    if not ma20 or not ma60:
        return None
    dev20 = series.iloc[-1] / ma20 - 1
    dev60 = series.iloc[-1] / ma60 - 1
    rets = series.pct_change().tail(20).dropna()
    vol = float(rets.std() * np.sqrt(252)) if len(rets) >= 5 else 0.25
    score = 50 + dev60 * 350 + dev20 * 150 - max(0.0, (vol - 0.12)) * 120
    return float(max(0, min(100, score)))


def _metrics(daily: pd.Series, rf: float = 0.015) -> dict:
    if daily.empty:
        return {}
    equity = (1 + daily).cumprod()
    total = float(equity.iloc[-1] - 1)
    days = max(1, (daily.index[-1] - daily.index[0]).days)
    annual = float((1 + total) ** (365 / days) - 1)
    vol = float(daily.std() * np.sqrt(252))
    peak = equity.cummax()
    dd = float((equity / peak - 1).min())
    sharpe = float((annual - rf) / vol) if vol > 0 else None
    return {
        "total_return": round(total, 4),
        "annual_return": round(annual, 4),
        "annual_vol": round(vol, 4),
        "max_drawdown": round(dd, 4),
        "sharpe": round(sharpe, 2) if sharpe is not None else None,
        "days": days,
        "start": daily.index[0].isoformat(),
        "end": daily.index[-1].isoformat(),
    }


def run_backtest(db: Session, params: Optional[dict] = None) -> BacktestRun:
    params = params or {}
    cfg = config_service.risk_limits()
    portfolio_cfg = cfg.get("portfolio", {})
    max_tilt = float(params.get("max_tilt", portfolio_cfg.get("max_tactical_tilt", 0.05)))
    cost_bps = float(params.get("transaction_cost_bps", portfolio_cfg.get("transaction_cost_bps", 8)))
    rebalance_days = int(params.get("rebalance_days", portfolio_cfg.get("rebalance_days", 20)))
    base_weights = {k: float(v) for k, v in cfg.get("example_portfolio", {}).get("base_weights", {}).items()}
    class_limits = cfg.get("class_limits", {})

    frames: dict[str, pd.Series] = {}
    missing: list[str] = []
    for code, symbol in REPRESENTATIVE.items():
        series = load_close_series(db, symbol, 1200)
        if len(series) >= 120:
            frames[code] = series
        else:
            missing.append(f"{code}（{symbol}）历史数据不足 {len(series)} 条")

    run = BacktestRun(
        strategy_code=params.get("strategy_code", "score_tilt"),
        status="running",
        start_date=date.today(),
        end_date=date.today(),
        params={
            "max_tilt": max_tilt,
            "transaction_cost_bps": cost_bps,
            "rebalance_days": rebalance_days,
            "base_weights": base_weights,
            "representatives": REPRESENTATIVE,
        },
    )
    db.add(run)
    db.commit()

    if len(frames) < 3:
        run.status = "failed"
        run.error = "可用于回测的资产类别不足 3 个：" + "；".join(missing[:4])
        run.finished_at = now_cn()
        db.commit()
        return run

    price = pd.DataFrame(frames).sort_index().ffill().dropna(how="all")
    bench = load_close_series(db, BENCHMARK, 1200)
    price = price.join(bench.rename("__bench__"), how="inner").dropna(subset=["__bench__"])
    if len(price) < 180:
        run.status = "failed"
        run.error = f"对齐后的交易日仅 {len(price)} 天，不足以进行 180 天以上的回测。"
        run.finished_at = now_cn()
        db.commit()
        return run

    codes = [c for c in frames if c in price.columns]
    dates = price.index.tolist()
    asset_returns = price[codes].pct_change().fillna(0.0)
    weights = np.array([base_weights.get(c, 0.0) for c in codes], dtype=float)
    weights = weights / weights.sum() if weights.sum() > 0 else np.full(len(codes), 1 / len(codes))

    equity = [1.0]
    bench_equity = [1.0]
    curve = [{"date": dates[0].isoformat(), "strategy": 1.0, "benchmark": 1.0}]
    rebalances = []
    total_cost = 0.0
    total_turnover = 0.0
    daily_returns = []
    last_rebalance_idx = 0

    for i in range(1, len(dates)):
        day_ret = float(np.nansum(weights * asset_returns.iloc[i].values))
        cost = 0.0
        if i - last_rebalance_idx >= rebalance_days:
            scores, lower, upper, desired = [], [], [], []
            for code in codes:
                series = price[code].iloc[: i + 1].dropna()
                score = _score_from_history(series)
                limit = class_limits.get(code, {})
                lo = float(limit.get("min", 0.0))
                hi = float(limit.get("max", 0.6))
                lower.append(lo)
                upper.append(max(lo, hi))
                if score is None:
                    scores.append(None)
                    desired.append(weights[codes.index(code)])
                else:
                    scores.append(round(score, 1))
                    desired.append(max(0.0, base_weights.get(code, 0.0) + (score - 50) / 50 * max_tilt))
            new_weights, _ = bounded_simplex_projection(np.array(desired), np.array(lower), np.array(upper), 1.0)
            turnover = float(np.abs(new_weights - weights).sum() / 2)
            cost = turnover * cost_bps / 10000
            total_cost += cost
            total_turnover += turnover
            weights = new_weights
            last_rebalance_idx = i
            rebalances.append(
                {
                    "date": dates[i].isoformat(),
                    "weights": {c: round(float(w), 4) for c, w in zip(codes, weights)},
                    "scores": dict(zip(codes, scores)),
                    "turnover": round(turnover, 4),
                    "cost": round(cost, 6),
                }
            )
        equity_value = equity[-1] * (1 + day_ret - cost)
        bench_value = bench_equity[-1] * (1 + float(price["__bench__"].iloc[i] / price["__bench__"].iloc[i - 1] - 1))
        equity.append(equity_value)
        bench_equity.append(bench_value)
        daily_returns.append(day_ret - cost)
        # 与对比引擎保持一致：权重随价格漂移，否则调仓日之间的换手被低估
        grown = weights * (1 + asset_returns.iloc[i].values)
        if grown.sum() > 0:
            weights = grown / grown.sum()
        curve.append(
            {
                "date": dates[i].isoformat(),
                "strategy": round(equity_value, 4),
                "benchmark": round(bench_value, 4),
            }
        )

    daily = pd.Series(daily_returns, index=dates[1:])
    if (daily.abs() > 0.4).any():
        run.status = "failed"
        run.error = "检测到单日收益超过 40%，疑似价格数据异常或单位不一致，已中止回测（不输出可疑结果）。"
        run.finished_at = now_cn()
        db.commit()
        return run
    bench_daily = price["__bench__"].pct_change().dropna()
    split = int(len(daily) * 0.7)
    metrics = _metrics(daily)
    metrics.update(
        {
            "benchmark": _metrics(bench_daily),
            "turnover_total": round(total_turnover, 3),
            "cost_total": round(total_cost, 5),
            "rebalances": len(rebalances),
            "excess_return": round(metrics.get("total_return", 0) - _metrics(bench_daily).get("total_return", 0), 4),
            "in_sample": _metrics(daily.iloc[:split]),
            "out_of_sample": _metrics(daily.iloc[split:]) if split < len(daily) else {},
        }
    )
    run.status = "success"
    run.start_date = dates[0]
    run.end_date = dates[-1]
    run.metrics = clean_json(metrics)
    run.equity_curve = clean_json(curve[-1500:])
    run.benchmark_curve = clean_json([{"date": dates[i].isoformat(), "value": round(bench_equity[i], 4)} for i in range(len(bench_equity))][-1500:])
    run.rebalances = clean_json(rebalances[-40:])
    run.assumptions = {
        "data_range": f"{dates[0]} ~ {dates[-1]}",
        "rebalance": f"每 {rebalance_days} 个交易日",
        "transaction_cost_bps": cost_bps,
        "price": "东方财富前复权日线收盘价",
        "dividends": "已在前复权价格中体现，未单独计入",
        "scores": "仅使用趋势/动量/波动，不含宏观与事件，避免前视偏差",
        "benchmark": "沪深300",
    }
    run.warnings = [
        "使用当前资产目录中的代表品种回测，存在幸存者偏差。",
        "不模拟停牌、涨跌停与流动性冲击，成交价按收盘价假设。",
        "样本外结果仅覆盖后 30% 区间，样本有限，不能据此判断策略优劣。",
    ]
    if missing:
        run.warnings.append("以下类别历史数据不足，未纳入回测：" + "；".join(missing))
    run.finished_at = now_cn()
    db.commit()
    return run


# ============================================================
# 策略对比：同区间、同成本、同基准，多个策略并排跑
# ============================================================

STRATEGY_LABELS = {
    "buy_hold": "买入持有",
    "rebalance": "定期再平衡",
    "score_tilt": "评分倾斜",
    "trend": "趋势跟随",
    "rotation": "相对强弱轮动",
}


def _base_vector(codes: list[str], base_weights: dict[str, float]) -> np.ndarray:
    vec = np.array([float(base_weights.get(c, 0.0)) for c in codes], dtype=float)
    if vec.sum() <= 0:
        vec = np.full(len(codes), 1.0 / len(codes))
    return vec / vec.sum()


def _weight_fn_buy_hold():
    return None  # 不调仓


def _weight_fn_rebalance(base_vec: np.ndarray):
    return lambda i, price, codes: base_vec.copy()


def _weight_fn_score_tilt(
    base_weights: dict[str, float], class_limits: dict, max_tilt: float
):
    def fn(i: int, price: pd.DataFrame, codes: list[str]) -> np.ndarray:
        desired, lower, upper = [], [], []
        for code in codes:
            series = price[code].iloc[: i + 1].dropna()
            score = _score_from_history(series)
            limit = class_limits.get(code, {})
            lo = float(limit.get("min", 0.0))
            hi = float(limit.get("max", 0.6))
            lower.append(lo)
            upper.append(max(lo, hi))
            base = float(base_weights.get(code, 0.0))
            desired.append(max(0.0, base + (score - 50) / 50 * max_tilt) if score is not None else base)
        projected, _ = bounded_simplex_projection(
            np.array(desired), np.array(lower), np.array(upper), 1.0
        )
        return projected

    return fn


def _weight_fn_trend(
    base_weights: dict[str, float], cash_code: str = "CASH"
) -> "callable":
    """趋势跟随：只在 CANDIDATE 持基准权重，其余转现金。

    这是「雷达信号能不能赚钱」最直接的检验——如果这个策略跑不过买入持有，
    说明筛选出的机会没有超额收益。
    """
    def fn(i: int, price: pd.DataFrame, codes: list[str]) -> np.ndarray:
        target = {c: 0.0 for c in codes}
        for code in codes:
            if code == cash_code:
                continue
            series = price[code].iloc[: i + 1].dropna()
            feats = trend_features(series)
            state, _reasons = decide_state(feats, holding=False)
            if state == "CANDIDATE":
                target[code] = float(base_weights.get(code, 0.0))
        invested = sum(v for k, v in target.items() if k != cash_code)
        target[cash_code] = max(1.0 - invested, 0.0)
        vec = np.array([target[c] for c in codes], dtype=float)
        if vec.sum() <= 0:
            return _base_vector(codes, {cash_code: 1.0})
        return vec / vec.sum()

    return fn


def _weight_fn_rotation(top_n: int = 3, cash_code: str = "CASH"):
    """相对强弱轮动：每期持有 20 日动量最强的 N 个，等权。"""
    def fn(i: int, price: pd.DataFrame, codes: list[str]) -> np.ndarray:
        scores = {}
        for code in codes:
            if code == cash_code:
                continue
            series = price[code].iloc[: i + 1].dropna()
            if len(series) < 21:
                continue
            scores[code] = float(series.iloc[-1] / series.iloc[-21] - 1)
        picked = sorted(scores, key=scores.get, reverse=True)[:top_n]
        target = {c: 0.0 for c in codes}
        if not picked:
            target[cash_code] = 1.0
        else:
            weight = 1.0 / len(picked)
            for code in picked:
                target[code] = weight
        vec = np.array([target[c] for c in codes], dtype=float)
        return vec / vec.sum() if vec.sum() > 0 else _base_vector(codes, {cash_code: 1.0})

    return fn


def _simulate(
    price: pd.DataFrame,
    codes: list[str],
    weight_fn,
    rebalance_days: int,
    cost_bps: float,
    base_weights: dict[str, float],
) -> dict:
    """通用回测循环。weight_fn 返回目标权重；None 表示永不调仓。"""
    dates = price.index.tolist()
    asset_returns = price[codes].pct_change().fillna(0.0)
    weights = _base_vector(codes, base_weights)

    equity = [1.0]
    daily: list[float] = []
    turnover_total = 0.0
    cost_total = 0.0
    rebalances = 0
    last = 0
    curve = [{"date": dates[0].isoformat(), "value": 1.0}]

    for i in range(1, len(dates)):
        day_returns = asset_returns.iloc[i].values
        day_ret = float(np.nansum(weights * day_returns))
        cost = 0.0
        if weight_fn is not None and i - last >= rebalance_days:
            target = weight_fn(i, price, codes)
            turnover = float(np.abs(target - weights).sum() / 2)
            cost = turnover * cost_bps / 10000
            weights = target
            last = i
            turnover_total += turnover
            cost_total += cost
            rebalances += 1
        value = equity[-1] * (1 + day_ret - cost)
        equity.append(value)
        daily.append(day_ret - cost)
        curve.append({"date": dates[i].isoformat(), "value": round(value, 4)})
        # 关键：权重必须随价格自然漂移，否则等于每天零成本再平衡，
        # 会让「买入持有」和「定期再平衡」跑出完全相同的结果。
        grown = weights * (1 + day_returns)
        total = grown.sum()
        if total > 0:
            weights = grown / total

    series = pd.Series(daily, index=dates[1:])
    return {
        "daily": series,
        "equity": equity,
        "curve": curve,
        "turnover_total": round(turnover_total, 3),
        "cost_total": round(cost_total, 5),
        "rebalances": rebalances,
    }


def _iso_date(value) -> str:
    return value.date().isoformat() if hasattr(value, "date") else str(value)


def _by_period(series: pd.Series) -> dict:
    """按自然年拆分，回答「是不是只在某一段行情里有效」。"""
    # dates 是 datetime.date 对象，index 是 object 类型，先转 DatetimeIndex 才能取 .year
    if not isinstance(series.index, pd.DatetimeIndex):
        series = pd.Series(series.values, index=pd.to_datetime(series.index))
    out = {}
    for year, chunk in series.groupby(series.index.year):
        if len(chunk) < 20:
            continue
        out[str(year)] = _metrics(chunk)
    return out


def run_comparison(db: Session, params: Optional[dict] = None) -> dict:
    """多策略对比。同区间、同成本、同基准，任一条件不同则不可比。"""
    params = params or {}
    cfg = config_service.risk_limits()
    portfolio_cfg = cfg.get("portfolio", {})
    cost_bps = float(params.get("transaction_cost_bps", portfolio_cfg.get("transaction_cost_bps", 8)))
    rebalance_days = int(params.get("rebalance_days", portfolio_cfg.get("rebalance_days", 20)))
    max_tilt = float(params.get("max_tilt", portfolio_cfg.get("max_tactical_tilt", 0.05)))
    base_weights = {
        k: float(v) for k, v in cfg.get("example_portfolio", {}).get("base_weights", {}).items()
    }
    class_limits = cfg.get("class_limits", {})

    frames: dict[str, pd.Series] = {}
    missing: list[str] = []
    for code, symbol in REPRESENTATIVE.items():
        series = load_close_series(db, symbol, 1200)
        if len(series) >= 120:
            frames[code] = series
        else:
            missing.append(f"{code}（{symbol}）历史数据不足 {len(series)} 条")

    if len(frames) < 3:
        return {
            "ok": False,
            "message": "可用于对比的资产类别不足 3 个：" + "；".join(missing[:4]),
            "strategies": [],
        }

    price = pd.DataFrame(frames).sort_index().ffill().dropna(how="all")
    bench = load_close_series(db, BENCHMARK, 1200)
    price = price.join(bench.rename("__bench__"), how="inner").dropna(subset=["__bench__"])
    if len(price) < 180:
        return {
            "ok": False,
            "message": f"对齐后仅 {len(price)} 个交易日，不足以做 180 天以上的对比",
            "strategies": [],
        }

    codes = [c for c in frames if c in price.columns]
    base_vec = _base_vector(codes, base_weights)

    plans = [
        ("buy_hold", _weight_fn_buy_hold()),
        ("rebalance", _weight_fn_rebalance(base_vec)),
        ("score_tilt", _weight_fn_score_tilt(base_weights, class_limits, max_tilt)),
        ("trend", _weight_fn_trend(base_weights)),
        ("rotation", _weight_fn_rotation(top_n=3)),
    ]

    strategies = []
    period_table: dict[str, dict] = {}
    for code, fn in plans:
        sim = _simulate(price, codes, fn, rebalance_days, cost_bps, base_weights)
        if (sim["daily"].abs() > 0.4).any():
            strategies.append(
                {
                    "code": code,
                    "name": STRATEGY_LABELS[code],
                    "failed": True,
                    "message": "检测到单日收益超过 40%，疑似价格异常，已跳过该策略",
                }
            )
            continue
        metrics = _metrics(sim["daily"])
        metrics.update(
            {
                "turnover_total": sim["turnover_total"],
                "cost_total": sim["cost_total"],
                "rebalances": sim["rebalances"],
            }
        )
        strategies.append(
            {
                "code": code,
                "name": STRATEGY_LABELS[code],
                "failed": False,
                "metrics": metrics,
                "curve": sim["curve"][-1500:],
            }
        )
        period_table[code] = _by_period(sim["daily"])

    bench_daily = price["__bench__"].pct_change().dropna()
    bench_metrics = _metrics(bench_daily)

    # 敏感性：单参数扫描，其余固定（不做全网格，样本太小会过拟合）
    sensitivity = {
        "max_tilt": [
            _sensitivity_point(price, codes, base_weights, class_limits, "max_tilt", value, cost_bps, rebalance_days)
            for value in (0.02, 0.05, 0.08)
        ],
        "rebalance_days": [
            _sensitivity_point(price, codes, base_weights, class_limits, "rebalance_days", value, cost_bps, rebalance_days)
            for value in (10, 20, 40)
        ],
        "cost_bps": [
            _sensitivity_point(price, codes, base_weights, class_limits, "cost_bps", value, cost_bps, rebalance_days)
            for value in (0, 8, 15)
        ],
    }

    valid = [s for s in strategies if not s.get("failed")]
    best = max(valid, key=lambda s: s["metrics"].get("total_return", -9)) if valid else None
    warnings = [
        "所有策略使用同一价格矩阵、同一成本假设、同一区间，否则不可比。",
        "不模拟停牌、涨跌停与流动性冲击，成交价按收盘价假设。",
        "代表品种固定为当前目录中的一只，存在幸存者偏差。",
        "参数敏感性为单参数扫描，不是全网格，不能据此宣称参数最优。",
    ]
    if missing:
        warnings.append("以下类别历史不足未纳入：" + "；".join(missing))

    return {
        "ok": True,
        "period": {
            # index 元素可能是 datetime.date 或 Timestamp，统一走 _iso_date
            "start": _iso_date(price.index[0]),
            "end": _iso_date(price.index[-1]),
            "days": int(len(price)),
        },
        "params": {
            "transaction_cost_bps": cost_bps,
            "rebalance_days": rebalance_days,
            "max_tilt": max_tilt,
            "base_weights": base_weights,
        },
        "benchmark": {"name": "沪深300", "metrics": bench_metrics, "total_return": bench_metrics.get("total_return")},
        "strategies": strategies,
        "best": {"code": best["code"], "name": best["name"], "total_return": best["metrics"].get("total_return")} if best else None,
        "by_period": period_table,
        "sensitivity": sensitivity,
        "warnings": warnings,
    }


def _sensitivity_point(
    price: pd.DataFrame,
    codes: list[str],
    base_weights: dict[str, float],
    class_limits: dict,
    key: str,
    value: float,
    cost_bps: float,
    rebalance_days: int,
) -> dict:
    """只动一个参数，其余保持默认，观察收益与换手如何变化。"""
    tilt = value if key == "max_tilt" else 0.05
    days = int(value) if key == "rebalance_days" else rebalance_days
    cost = value if key == "cost_bps" else cost_bps
    fn = _weight_fn_score_tilt(base_weights, class_limits, tilt)
    sim = _simulate(price, codes, fn, days, cost, base_weights)
    metrics = _metrics(sim["daily"])
    return {
        "value": value,
        "total_return": metrics.get("total_return"),
        "max_drawdown": metrics.get("max_drawdown"),
        "turnover_total": sim["turnover_total"],
        "rebalances": sim["rebalances"],
    }
