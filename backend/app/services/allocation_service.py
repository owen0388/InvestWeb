"""跨资产配置引擎。

流程：基础权重 → 评分倾斜 → 约束投影 → 交易阈值 → 风险分析。
约束求解使用“有上下界的单纯形投影”（对偶变量二分），能同时满足
单项/类别上下限与总和为 1，而不是简单归一化后破坏约束。
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
from app.models.entities import (
    AllocationTarget,
    AssetClass,
    AssetSignal,
    Instrument,
    InvestorProfile,
    Portfolio,
    PortfolioHolding,
    RiskConstraint,
    SystemSetting,
)
from app.services import config_service
from app.services.market_data import annualized_vol, load_close_series, max_drawdown

MIN_TRADE = 0.005


def bounded_simplex_projection(
    desired: np.ndarray, lower: np.ndarray, upper: np.ndarray, total: float = 1.0
) -> tuple[np.ndarray, float]:
    """把 desired 投影到 {l <= w <= u, sum(w)=total}，返回 (权重, 最大残差)。

    对偶变量 λ 的单调性保证二分收敛；若约束不可行，退化为按上下界裁剪后归一化。
    """
    desired = np.asarray(desired, dtype=float)
    lower = np.asarray(lower, dtype=float)
    upper = np.asarray(upper, dtype=float)
    if lower.sum() > total + 1e-9 or upper.sum() < total - 1e-9:
        # 不可行：放宽到可行区间
        return _fallback(desired, lower, upper, total), 1.0

    lo, hi = -10.0, 10.0
    for _ in range(200):
        lam = (lo + hi) / 2
        weights = np.clip(desired + lam, lower, upper)
        if weights.sum() > total:
            hi = lam
        else:
            lo = lam
    weights = np.clip(desired + (lo + hi) / 2, lower, upper)
    # 消除浮点误差：把残差分配给仍有空间的项
    diff = total - weights.sum()
    if abs(diff) > 1e-9:
        for idx in np.argsort(-weights):
            room = (upper[idx] - weights[idx]) if diff > 0 else (weights[idx] - lower[idx])
            if room > 1e-12:
                step = np.sign(diff) * min(abs(diff), room)
                weights[idx] += step
                diff -= step
            if abs(diff) < 1e-12:
                break
    return weights, float(abs(weights.sum() - total))


def _fallback(desired, lower, upper, total) -> np.ndarray:
    clipped = np.clip(desired, lower, upper)
    if clipped.sum() <= 0:
        return np.full(len(desired), total / len(desired))
    return clipped / clipped.sum() * total


def top_class_map(db: Session) -> dict[str, str]:
    """二级类别 → 一级类别（基于目录层级，不能用字符串截断猜）。"""
    classes = db.scalars(select(AssetClass)).all()
    by_code = {c.code: c for c in classes}
    mapping: dict[str, str] = {}
    for cls in classes:
        node = cls
        depth = 0
        while node.parent_code and node.parent_code in by_code and depth < 5:
            node = by_code[node.parent_code]
            depth += 1
        mapping[cls.code] = node.code
    return mapping


def _get_json_setting(db: Session, key: str, default):
    row = db.get(SystemSetting, key)
    return row.value.get("value") if row and isinstance(row.value, dict) else default


def _set_json_setting(db: Session, key: str, value, description: str = "") -> None:
    row = db.get(SystemSetting, key)
    if row is None:
        row = SystemSetting(key=key, description=description, value={"value": value})
        db.add(row)
    else:
        row.value = {"value": value}


def get_base_weights(db: Session, portfolio: Portfolio, profile: InvestorProfile | None) -> tuple[dict, str]:
    key = f"portfolio:{portfolio.id}:base_weights"
    stored = _get_json_setting(db, key, None)
    if stored:
        return {k: float(v) for k, v in stored.items()}, "用户自定义基础权重"
    limits = config_service.risk_limits()
    if profile and profile.confirmed:
        preset = limits.get("profiles", {}).get(profile.risk_tolerance, {})
        if preset:
            return {k: float(v) for k, v in preset.get("base_weights", {}).items()}, f"已确认风险偏好「{preset.get('name')}」"
    example = limits.get("example_portfolio", {}).get("base_weights", {})
    return {k: float(v) for k, v in example.items()}, "示例基准（用户尚未确认个性化配置）"


def _class_signals(db: Session) -> dict[str, dict]:
    as_of = db.scalar(select(AssetSignal.as_of).order_by(AssetSignal.as_of.desc()))
    if as_of is None:
        return {}
    rows = db.scalars(select(AssetSignal).where(AssetSignal.as_of == as_of)).all()
    mapping = top_class_map(db)
    grouped: dict[str, list[AssetSignal]] = {}
    for row in rows:
        top = mapping.get(row.asset_class_code, row.asset_class_code)
        grouped.setdefault(top, []).append(row)
    result = {}
    for top, items in grouped.items():
        scores = [i.total_score for i in items if i.total_score is not None]
        confs = [i.confidence for i in items if i.confidence is not None]
        qualities = [i.data_quality for i in items if i.data_quality is not None]
        result[top] = {
            "score": round(float(np.mean(scores)), 1) if scores else None,
            "confidence": round(float(np.mean(confs)), 3) if confs else 0.0,
            "data_quality": round(float(np.mean(qualities)), 3) if qualities else 0.0,
            "count": len(items),
            "as_of": as_of.isoformat(),
            "leaders": sorted(
                [{"symbol": i.symbol, "score": i.total_score, "action": i.action} for i in items if i.total_score],
                key=lambda x: x["score"],
                reverse=True,
            )[:3],
        }
    return result


def _current_weights(db: Session, portfolio: Portfolio) -> dict[str, float]:
    holdings = db.scalars(select(PortfolioHolding).where(PortfolioHolding.portfolio_id == portfolio.id)).all()
    total = sum(h.market_value for h in holdings) or portfolio.total_value or 1.0
    weights: dict[str, float] = {}
    mapping = top_class_map(db)
    for holding in holdings:
        instrument = db.get(Instrument, holding.instrument_id)
        if not instrument:
            continue
        top = mapping.get(instrument.asset_class_code, instrument.asset_class_code)
        weights[top] = weights.get(top, 0.0) + (holding.market_value / total)
    if not weights:
        # 没有持仓时，以现金为起点
        weights["CASH"] = 1.0
    return {k: round(v, 6) for k, v in weights.items()}


def _constraints(db: Session, portfolio: Portfolio) -> tuple[dict[str, RiskConstraint], dict[str, AssetClass]]:
    rows = db.scalars(select(RiskConstraint).where(RiskConstraint.portfolio_id == portfolio.id)).all()
    limits = {r.asset_class_code: r for r in rows}
    classes = {c.code: c for c in db.scalars(select(AssetClass).where(AssetClass.level == 1)).all()}
    return limits, classes


def compute_allocation(db: Session, portfolio: Portfolio, persist: bool = True) -> dict:
    profile = db.scalar(select(InvestorProfile).where(InvestorProfile.portfolio_id == portfolio.id))
    limits, classes = _constraints(db, portfolio)
    base_weights, base_source = get_base_weights(db, portfolio, profile)
    current = _current_weights(db, portfolio)
    signals = _class_signals(db)
    risk_cfg = config_service.risk_limits().get("portfolio", {})
    min_confidence = float(risk_cfg.get("min_confidence", 0.35))
    min_quality = 0.4

    codes = sorted(set(base_weights) | set(current) | set(limits) | set(classes))
    # 只把真正参与配置的类别纳入求解：有基础权重、有持仓、或有强制最低权重
    codes = [
        c
        for c in codes
        if c in classes
        and (
            float(base_weights.get(c, 0.0)) > 0
            or float(current.get(c, 0.0)) > 0
            or float((limits.get(c).min_weight if limits.get(c) else 0.0)) > 0
            or c == "CASH"
        )
    ]

    # 用户准入/禁止
    allowed = set(profile.allowed_classes) if profile and profile.allowed_classes else set(codes)
    excluded: list[str] = []

    desired, lower, upper, meta = [], [], [], {}
    for code in codes:
        cls = classes[code]
        constraint = limits.get(code)
        cmin = constraint.min_weight if constraint else cls.default_min_weight
        cmax = constraint.max_weight if constraint else cls.default_max_weight
        if code not in allowed:
            cmax = 0.0
            cmin = 0.0
            excluded.append(code)
        if code == "DERIVATIVE":
            cmax = 0.0
            cmin = 0.0
            excluded.append(code)
        base = float(base_weights.get(code, 0.0))
        signal = signals.get(code, {})
        score = signal.get("score")
        conf = signal.get("confidence", 0.0)
        quality = signal.get("data_quality", 0.0)
        reasons: list[str] = []
        tilt = 0.0
        if score is None:
            reasons.append("无可用评分，不进行战术调整")
        elif conf < min_confidence:
            reasons.append(f"评分置信度 {conf:.2f} 低于阈值 {min_confidence:.2f}，不调整")
        elif quality < min_quality:
            reasons.append(f"数据质量 {quality:.2f} 不足，不因缺失数据加仓")
        else:
            raw = (score - 50) / 50 * float(
                constraint.max_tactical_tilt if constraint else risk_cfg.get("max_tactical_tilt", 0.05)
            )
            tilt = raw * conf
            reasons.append(f"评分 {score:.1f}、置信度 {conf:.2f}，战术倾斜 {tilt*100:+.2f}%")
        target_desire = base + tilt
        desired.append(min(1.0, max(0.0, target_desire)))
        lower.append(max(0.0, cmin))
        upper.append(max(cmin, min(1.0, cmax)))
        meta[code] = {
            "name": cls.name,
            "base": round(base, 4),
            "score": score,
            "confidence": conf,
            "quality": quality,
            "tilt": round(tilt, 4),
            "min": round(cmin, 4),
            "max": round(cmax, 4),
            "reasons": reasons,
            "leaders": signal.get("leaders", []),
            "current": round(current.get(code, 0.0), 4),
            "as_of": signal.get("as_of"),
            "excluded": code in excluded,
        }

    if not codes:
        return {"error": "资产类别为空，请先初始化资产目录"}

    weights, residual = bounded_simplex_projection(
        np.array(desired), np.array(lower), np.array(upper), total=1.0
    )
    warnings: list[str] = []
    if residual > 1e-6:
        warnings.append("上下限约束不可行，已退化为裁剪归一化，请检查约束配置。")

    # 换手限制
    max_turnover = float(risk_cfg.get("max_turnover_per_rebalance", 0.12))
    turnover = float(np.abs(weights - np.array([meta[c]["current"] for c in codes])).sum() / 2)
    turnover_relaxed = False
    if turnover > max_turnover:
        lower_t = np.array([max(l, meta[c]["current"] - max_turnover) for c, l in zip(codes, lower)])
        upper_t = np.array([min(u, meta[c]["current"] + max_turnover) for c, u in zip(codes, upper)])
        if lower_t.sum() <= 1.0 <= upper_t.sum():
            weights, residual = bounded_simplex_projection(
                np.array(desired), lower_t, upper_t, total=1.0
            )
            turnover = float(np.abs(weights - np.array([meta[c]["current"] for c in codes])).sum() / 2)
            warnings.append(f"已按单次换手上限 {max_turnover:.0%} 压缩调整幅度")
        else:
            turnover_relaxed = True
            warnings.append("换手上限与上下限约束冲突，本次仅提示风险，不强制压缩权重。")
        lower, upper = lower_t, upper_t

    # 最小交易阈值：过小的调整视为不交易，并重新投影保持总和
    adjusted = []
    for idx, code in enumerate(codes):
        delta = weights[idx] - meta[code]["current"]
        if abs(delta) < MIN_TRADE:
            adjusted.append(meta[code]["current"])
        else:
            adjusted.append(weights[idx])
    weights, _ = bounded_simplex_projection(np.array(adjusted), lower, upper, total=1.0)

    targets = []
    for idx, code in enumerate(codes):
        w = float(weights[idx])
        delta = w - meta[code]["current"]
        meta[code]["target"] = round(w, 6)
        meta[code]["delta"] = round(delta, 6)
        targets.append(
            {
                "asset_class_code": code,
                **meta[code],
                "target": round(w, 6),
                "delta": round(delta, 6),
                "trade": abs(delta) >= MIN_TRADE,
            }
        )

    risk = analyze_risk(db, portfolio, {c: meta[c]["current"] for c in codes}, {t["asset_class_code"]: t["target"] for t in targets}, classes)
    regime_row = db.get(SystemSetting, "last_regime")

    result = {
        "portfolio": {
            "id": portfolio.id,
            "name": portfolio.name,
            "total_value": portfolio.total_value,
            "is_example": portfolio.is_example,
            "is_personalized": bool(profile and profile.confirmed),
            "base_source": base_source,
            "risk_tolerance": profile.risk_tolerance if profile else "balanced",
            "horizon_years": profile.horizon_years if profile else None,
            "max_drawdown": profile.max_drawdown if profile else None,
        },
        "as_of": date.today().isoformat(),
        "regime": (regime_row.value if regime_row else {}).get("state", "数据不足"),
        "targets": targets,
        "risk": risk,
        "turnover": round(turnover, 4),
        "max_turnover": max_turnover,
        "turnover_relaxed": turnover_relaxed,
        "warnings": warnings,
        "excluded_classes": excluded,
        "disclaimer": (
            "本配置为研究与模拟结果，不执行真实交易；"
            + ("已基于用户确认的投资者配置生成。" if profile and profile.confirmed
               else "用户尚未确认投资者配置，当前展示的是示例组合，不构成个性化建议。")
        ),
    }
    result = clean_json(result)
    if persist:
        _persist_targets(db, portfolio, targets, result)
    return result


def _persist_targets(db: Session, portfolio: Portfolio, targets: list[dict], result: dict) -> None:
    last_version = db.scalar(
        select(AllocationTarget.version)
        .where(AllocationTarget.portfolio_id == portfolio.id)
        .order_by(AllocationTarget.version.desc())
    ) or 0
    today = date.today()
    version = last_version + 1
    for target in targets:
        db.add(
            AllocationTarget(
                portfolio_id=portfolio.id,
                asset_class_code=target["asset_class_code"],
                base_weight=target["base"],
                tactical_weight=target["tilt"],
                target_weight=target["target"],
                prev_weight=target["current"],
                delta=target["delta"],
                score=target["score"],
                confidence=target["confidence"],
                reason={"reasons": target["reasons"], "leaders": target["leaders"], "regime": result["regime"]},
                constraints={"min": target["min"], "max": target["max"]},
                as_of=today,
                version=version,
                applied=False,
            )
        )
    db.commit()


def _returns_matrix(db: Session, symbols: list[str], days: int = 250) -> pd.DataFrame:
    series_map = {}
    for symbol in symbols:
        series = load_close_series(db, symbol, days + 30)
        if len(series) >= 60:
            series_map[symbol] = series.tail(days)
    if len(series_map) < 2:
        return pd.DataFrame()
    frame = pd.DataFrame(series_map).sort_index().ffill().dropna(how="all")
    return frame.pct_change().dropna(how="all")


REPRESENTATIVE = {
    "CN_EQ": "sh000300",
    "FUND": "sh510300",
    "BOND": "sh511010",
    "GOLD": "sh518880",
    "COMMODITY": "hf_CL",
    "OVERSEAS": "sh513100",
    "CASH": "sh511880",
}


def analyze_risk(
    db: Session,
    portfolio: Portfolio,
    current: dict[str, float],
    target: dict[str, float],
    classes: dict[str, AssetClass],
) -> dict:
    symbols = [s for c, s in REPRESENTATIVE.items() if (current.get(c, 0) + target.get(c, 0)) > 0]
    matrix = _returns_matrix(db, symbols, days=250)
    notes: list[str] = []
    comparison: dict = {"current": None, "target": None}

    if matrix.empty or len(matrix) < 60:
        notes.append("可对齐的历史收益序列不足，无法计算组合波动率与相关性（不使用假设计算）。")
    else:
        used = {}
        for code, symbol in REPRESENTATIVE.items():
            if symbol in matrix.columns and (current.get(code, 0) + target.get(code, 0)) > 0:
                used[code] = symbol
        coverage = sum(current.get(c, 0) for c in used) or sum(target.get(c, 0) for c in used)
        if coverage < 0.55:
            notes.append(f"可计算风险的权重覆盖仅 {coverage:.0%}，不输出组合波动率，避免误导。")
        else:
            cols = [used[c] for c in used]
            cov = matrix[cols].cov() * 252
            for label, weights in (("current", current), ("target", target)):
                vector = np.array([weights.get(c, 0.0) for c in used])
                norm = sum(weights.get(c, 0.0) for c in used)
                if norm <= 0:
                    continue
                vector = vector / norm
                vol = float(np.sqrt(vector @ cov.values @ vector))
                comparison[label] = {
                    "annual_vol": round(vol, 4),
                    "coverage": round(coverage, 3),
                    "note": "按可计算权重的条件波动率，未覆盖部分以现金近似。",
                }
            notes.append("波动率基于近 250 个交易日的历史收益与协方差，不代表未来风险。")
        corr = matrix[list(used.values())].corr().round(2)
        corr_matrix = {
            "labels": [classes[c].name if c in classes else c for c in used],
            "values": corr.values.tolist(),
        }
    if "corr_matrix" not in locals():
        corr_matrix = {"labels": [], "values": []}

    class_weights = [{"code": c, "name": classes[c].name if c in classes else c, "weight": round(w, 4)} for c, w in target.items() if w > 0]
    top_class = max(class_weights, key=lambda x: x["weight"], default=None)
    hhi = round(sum(w["weight"] ** 2 for w in class_weights), 4)
    low_liquidity = sum(
        w for c, w in target.items()
        if c in classes and classes[c].liquidity_tier in ("low", "very_low")
    )

    scenarios = [
        {"name": "权益快速下跌 20%", "shocks": {"CN_EQ": -0.20, "FUND": -0.18, "GOLD": 0.06, "BOND": 0.03}},
        {"name": "利率上行 100bp", "shocks": {"BOND": -0.035, "CN_EQ": -0.06, "CASH": 0.004}},
        {"name": "原油上涨 30%", "shocks": {"COMMODITY": 0.18, "CN_EQ": -0.02, "GOLD": 0.03}},
        {"name": "人民币贬值 5%", "shocks": {"OVERSEAS": 0.05, "GOLD": 0.04, "CN_EQ": -0.02}},
    ]
    stress = []
    for scenario in scenarios:
        impact = sum(target.get(code, 0.0) * shock for code, shock in scenario["shocks"].items())
        stress.append({"name": scenario["name"], "impact": round(impact, 4), "assumption": "线性近似，未考虑相关性与流动性折价"})

    max_dd = None
    if matrix is not None and not matrix.empty and len(matrix) >= 60:
        try:
            cols = [c for c in REPRESENTATIVE.values() if c in matrix.columns]
            vec = np.array([target.get(code, 0.0) for code, sym in REPRESENTATIVE.items() if sym in cols])
            norm = vec.sum()
            if norm > 0:
                port_ret = (matrix[cols].values @ (vec / norm))
                equity = np.cumprod(1 + port_ret)
                peak = np.maximum.accumulate(equity)
                max_dd = round(float((equity / peak - 1).min()), 4)
        except Exception:  # noqa: BLE001
            max_dd = None

    return {
        "volatility": comparison,
        "vol_change": (
            round(comparison["target"]["annual_vol"] - comparison["current"]["annual_vol"], 4)
            if comparison["current"] and comparison["target"]
            else None
        ),
        "max_drawdown_estimate": max_dd,
        "concentration": {"max_class": top_class, "hhi": hhi},
        "low_liquidity_weight": round(low_liquidity, 4),
        "correlation": corr_matrix,
        "stress_tests": stress,
        "notes": notes,
        "data_limitations": [
            "私募、保险、房产等非日频资产未参与波动率与相关性计算。",
            "风险指标为历史统计，不构成未来损失上限。",
        ],
    }
