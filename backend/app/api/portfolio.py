"""组合、持仓、投资者配置、风险约束与目标权重接口。"""
from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import (
    BaseWeightsIn,
    ConstraintIn,
    HoldingIn,
    HoldingUpdate,
    PortfolioIn,
    ProfileIn,
)
from app.core.db import get_db
from app.core.timeutil import now_cn
from app.models.entities import (
    AllocationTarget,
    AssetClass,
    Instrument,
    InvestorProfile,
    MarketObservation,
    Portfolio,
    PortfolioHolding,
    RiskConstraint,
    StrategyConfig,
    SystemSetting,
)
from app.services import audit
from app.services.allocation_service import (
    top_class_map,
    _set_json_setting,
    compute_allocation,
    get_base_weights,
)

router = APIRouter(prefix="/api/portfolios", tags=["portfolio"])


def _latest_close(db: Session, symbol: str) -> Optional[float]:
    obs = db.scalar(
        select(MarketObservation)
        .where(MarketObservation.symbol == symbol, MarketObservation.close.is_not(None))
        .order_by(MarketObservation.trade_date.desc(), MarketObservation.observed_at.desc())
    )
    return float(obs.close) if obs and obs.close else None


def _recalc_weights(db: Session, portfolio: Portfolio) -> None:
    holdings = db.scalars(select(PortfolioHolding).where(PortfolioHolding.portfolio_id == portfolio.id)).all()
    for holding in holdings:
        if holding.market_value <= 0 and holding.quantity > 0:
            price = _latest_close(db, holding.symbol) or holding.cost_price
            holding.market_value = round(holding.quantity * price, 2)
    total = sum(h.market_value for h in holdings) + (portfolio.cash_value or 0)
    if total <= 0:
        return
    portfolio.total_value = round(total, 2)
    for holding in holdings:
        holding.weight = round(holding.market_value / total, 6)
    db.commit()


def _portfolio_payload(db: Session, portfolio: Portfolio) -> dict:
    profile = db.scalar(select(InvestorProfile).where(InvestorProfile.portfolio_id == portfolio.id))
    holdings = db.scalars(
        select(PortfolioHolding).where(PortfolioHolding.portfolio_id == portfolio.id)
    ).all()
    class_weights: dict[str, float] = {}
    class_map = top_class_map(db)
    holding_payload = []
    for holding in holdings:
        inst = db.get(Instrument, holding.instrument_id)
        cls = db.scalar(select(AssetClass).where(AssetClass.code == inst.asset_class_code)) if inst else None
        price = _latest_close(db, holding.symbol)
        if inst:
            top = class_map.get(inst.asset_class_code, inst.asset_class_code)
            class_weights[top] = class_weights.get(top, 0.0) + holding.weight
        holding_payload.append(
            {
                "id": holding.id,
                "symbol": holding.symbol,
                "name": inst.name if inst else holding.symbol,
                "class": cls.name if cls else None,
                "class_code": inst.asset_class_code if inst else None,
                "quantity": holding.quantity,
                "cost_price": holding.cost_price,
                "market_value": holding.market_value,
                "weight": holding.weight,
                "latest_price": price,
                "data_status": inst.data_status if inst else None,
                "source": holding.source,
                "updated_at": holding.updated_at,
            }
        )
    constraints = db.scalars(
        select(RiskConstraint).where(RiskConstraint.portfolio_id == portfolio.id)
    ).all()
    return {
        "portfolio": {
            "id": portfolio.id,
            "name": portfolio.name,
            "owner": portfolio.owner,
            "base_currency": portfolio.base_currency,
            "total_value": portfolio.total_value,
            "cash_value": portfolio.cash_value,
            "liabilities": portfolio.liabilities,
            "is_example": portfolio.is_example,
            "is_default": portfolio.is_default,
            "notes": portfolio.notes,
            "updated_at": portfolio.updated_at,
        },
        "holdings": holding_payload,
        "class_weights": [{"code": k, "weight": round(v, 4)} for k, v in sorted(class_weights.items())],
        "profile": _profile_payload(profile),
        "constraints": [
            {
                "asset_class_code": c.asset_class_code,
                "min_weight": c.min_weight,
                "max_weight": c.max_weight,
                "max_single_instrument": c.max_single_instrument,
                "max_turnover_per_rebalance": c.max_turnover_per_rebalance,
                "max_tactical_tilt": c.max_tactical_tilt,
                "min_confidence": c.min_confidence,
                "enabled": c.enabled,
            }
            for c in constraints
        ],
        "base_source": get_base_weights(db, portfolio, profile)[1],
    }


def _profile_payload(profile: InvestorProfile | None) -> dict | None:
    if not profile:
        return None
    return {
        "risk_tolerance": profile.risk_tolerance,
        "horizon_years": profile.horizon_years,
        "max_drawdown": profile.max_drawdown,
        "liquidity_need_ratio": profile.liquidity_need_ratio,
        "emergency_cash_months": profile.emergency_cash_months,
        "monthly_income": profile.monthly_income,
        "monthly_expense": profile.monthly_expense,
        "allowed_classes": profile.allowed_classes,
        "excluded_symbols": profile.excluded_symbols,
        "qualifications": profile.qualifications,
        "notes": profile.notes,
        "confirmed": profile.confirmed,
        "confirmed_at": profile.confirmed_at,
    }


@router.get("")
def list_portfolios(db: Session = Depends(get_db)):
    portfolios = db.scalars(select(Portfolio).order_by(Portfolio.is_default.desc(), Portfolio.id)).all()
    return {
        "items": [
            {
                "id": p.id,
                "name": p.name,
                "total_value": p.total_value,
                "is_example": p.is_example,
                "is_default": p.is_default,
                "updated_at": p.updated_at,
            }
            for p in portfolios
        ]
    }


@router.get("/{portfolio_id}")
def get_portfolio(portfolio_id: int, db: Session = Depends(get_db)):
    portfolio = db.get(Portfolio, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="组合不存在")
    return _portfolio_payload(db, portfolio)


@router.put("/{portfolio_id}")
def update_portfolio(portfolio_id: int, payload: PortfolioIn, db: Session = Depends(get_db)):
    portfolio = db.get(Portfolio, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="组合不存在")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(portfolio, field, value)
    db.commit()
    _recalc_weights(db, portfolio)
    return _portfolio_payload(db, portfolio)


@router.post("/{portfolio_id}/holdings")
def add_holding(portfolio_id: int, payload: HoldingIn, db: Session = Depends(get_db)):
    portfolio = db.get(Portfolio, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="组合不存在")
    inst = db.scalar(select(Instrument).where(Instrument.symbol == payload.symbol))
    if not inst:
        raise HTTPException(status_code=404, detail=f"品种 {payload.symbol} 不在资产目录中，请先登记")
    holding = db.scalar(
        select(PortfolioHolding).where(
            PortfolioHolding.portfolio_id == portfolio_id,
            PortfolioHolding.instrument_id == inst.id,
        )
    )
    if holding is None:
        holding = PortfolioHolding(
            portfolio_id=portfolio_id,
            instrument_id=inst.id,
            symbol=inst.symbol,
            source="manual",
        )
        db.add(holding)
    holding.quantity = payload.quantity
    holding.cost_price = payload.cost_price
    if payload.market_value is not None:
        holding.market_value = payload.market_value
    elif payload.quantity > 0:
        holding.market_value = round(payload.quantity * (_latest_close(db, inst.symbol) or payload.cost_price), 2)
    audit.log(db, "holding.add", payload.symbol, {"quantity": payload.quantity, "cost_price": payload.cost_price})
    db.commit()
    _recalc_weights(db, portfolio)
    return _portfolio_payload(db, portfolio)


@router.put("/{portfolio_id}/holdings/{holding_id}")
def update_holding(portfolio_id: int, holding_id: int, payload: HoldingUpdate, db: Session = Depends(get_db)):
    holding = db.get(PortfolioHolding, holding_id)
    if not holding or holding.portfolio_id != portfolio_id:
        raise HTTPException(status_code=404, detail="持仓不存在")
    if payload.quantity is not None:
        holding.quantity = payload.quantity
        price = _latest_close(db, holding.symbol) or holding.cost_price
        holding.market_value = round(payload.quantity * price, 2)
    if payload.cost_price is not None:
        holding.cost_price = payload.cost_price
    if payload.market_value is not None:
        holding.market_value = payload.market_value
    audit.log(db, "holding.update", holding.symbol, payload.model_dump(exclude_none=True))
    db.commit()
    portfolio = db.get(Portfolio, portfolio_id)
    _recalc_weights(db, portfolio)
    return _portfolio_payload(db, portfolio)


@router.delete("/{portfolio_id}/holdings/{holding_id}")
def delete_holding(portfolio_id: int, holding_id: int, db: Session = Depends(get_db)):
    holding = db.get(PortfolioHolding, holding_id)
    if not holding or holding.portfolio_id != portfolio_id:
        raise HTTPException(status_code=404, detail="持仓不存在")
    audit.log(db, "holding.delete", holding.symbol, {})
    db.delete(holding)
    db.commit()
    portfolio = db.get(Portfolio, portfolio_id)
    _recalc_weights(db, portfolio)
    return _portfolio_payload(db, portfolio)


@router.post("/{portfolio_id}/holdings/import")
async def import_holdings(
    portfolio_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    portfolio = db.get(Portfolio, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="组合不存在")
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="文件为空")
    frame = None
    for encoding in ("utf-8-sig", "gbk", "utf-8"):
        try:
            import io

            frame = pd.read_csv(io.BytesIO(raw), encoding=encoding)
            break
        except Exception:  # noqa: BLE001
            continue
    if frame is None or frame.empty:
        raise HTTPException(status_code=400, detail="CSV 解析失败，请确认包含 symbol/quantity 列")

    rename = {
        "代码": "symbol", "名称": "name", "数量": "quantity", "持仓数量": "quantity",
        "成本": "cost_price", "成本价": "cost_price", "市值": "market_value",
    }
    frame = frame.rename(columns={k: v for k, v in rename.items() if k in frame.columns})
    if "symbol" not in frame.columns:
        raise HTTPException(status_code=400, detail="缺少 symbol（代码）列")
    imported, failed = 0, []
    for _, row in frame.iterrows():
        symbol = str(row["symbol"]).strip()
        inst = db.scalar(select(Instrument).where(Instrument.symbol == symbol))
        if not inst:
            failed.append(symbol)
            continue
        quantity = float(row.get("quantity") or 0) if pd.notna(row.get("quantity")) else 0.0
        cost = float(row.get("cost_price") or 0) if pd.notna(row.get("cost_price")) else 0.0
        holding = db.scalar(
            select(PortfolioHolding).where(
                PortfolioHolding.portfolio_id == portfolio_id,
                PortfolioHolding.instrument_id == inst.id,
            )
        )
        if holding is None:
            holding = PortfolioHolding(
                portfolio_id=portfolio_id, instrument_id=inst.id, symbol=inst.symbol, source="csv"
            )
            db.add(holding)
        holding.quantity = quantity
        holding.cost_price = cost
        holding.market_value = round(quantity * (_latest_close(db, symbol) or cost), 2)
        holding.source = "csv"
        imported += 1
    audit.log(db, "holding.import", str(portfolio_id), {"imported": imported, "failed": failed})
    db.commit()
    _recalc_weights(db, portfolio)
    return {
        "imported": imported,
        "failed": failed,
        "note": "未在资产目录中的代码被跳过，需先登记后再导入。",
        "portfolio": _portfolio_payload(db, portfolio),
    }


@router.get("/{portfolio_id}/investor-profile")
def get_profile(portfolio_id: int, db: Session = Depends(get_db)):
    profile = db.scalar(select(InvestorProfile).where(InvestorProfile.portfolio_id == portfolio_id))
    return {"profile": _profile_payload(profile), "confirmed": bool(profile and profile.confirmed)}


@router.put("/{portfolio_id}/investor-profile")
def update_profile(portfolio_id: int, payload: ProfileIn, db: Session = Depends(get_db)):
    portfolio = db.get(Portfolio, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="组合不存在")
    profile = db.scalar(select(InvestorProfile).where(InvestorProfile.portfolio_id == portfolio_id))
    if profile is None:
        profile = InvestorProfile(portfolio_id=portfolio_id)
        db.add(profile)
    data = payload.model_dump(exclude_none=True)
    confirmed = data.pop("confirmed", None)
    for field, value in data.items():
        setattr(profile, field, value)
    audit.log(db, "investor_profile.update", str(portfolio_id), {"confirmed": confirmed})
    if confirmed is not None:
        profile.confirmed = confirmed
        profile.confirmed_at = now_cn() if confirmed else None
        if confirmed:
            portfolio.is_example = False
    db.commit()
    return {"profile": _profile_payload(profile), "confirmed": profile.confirmed}


@router.get("/{portfolio_id}/constraints")
def get_constraints(portfolio_id: int, db: Session = Depends(get_db)):
    rows = db.scalars(select(RiskConstraint).where(RiskConstraint.portfolio_id == portfolio_id)).all()
    return {
        "items": [
            {
                "asset_class_code": r.asset_class_code,
                "min_weight": r.min_weight,
                "max_weight": r.max_weight,
                "max_tactical_tilt": r.max_tactical_tilt,
                "max_turnover_per_rebalance": r.max_turnover_per_rebalance,
                "min_confidence": r.min_confidence,
            }
            for r in rows
        ]
    }


@router.put("/{portfolio_id}/constraints")
def update_constraints(portfolio_id: int, items: list[ConstraintIn], db: Session = Depends(get_db)):
    for item in items:
        if item.min_weight > item.max_weight:
            raise HTTPException(status_code=400, detail=f"{item.asset_class_code} 的最小权重不能大于最大权重")
        row = db.scalar(
            select(RiskConstraint).where(
                RiskConstraint.portfolio_id == portfolio_id,
                RiskConstraint.asset_class_code == item.asset_class_code,
            )
        )
        if row is None:
            row = RiskConstraint(portfolio_id=portfolio_id, asset_class_code=item.asset_class_code)
            db.add(row)
        row.min_weight = item.min_weight
        row.max_weight = item.max_weight
        if item.max_tactical_tilt is not None:
            row.max_tactical_tilt = item.max_tactical_tilt
    total_min = sum(i.min_weight for i in items)
    if total_min > 1.0001:
        raise HTTPException(status_code=400, detail=f"各类别最小权重合计 {total_min:.2%} 超过 100%，约束不可行")
    db.commit()
    return get_constraints(portfolio_id, db)


@router.get("/{portfolio_id}/base-weights")
def read_base_weights(portfolio_id: int, db: Session = Depends(get_db)):
    portfolio = db.get(Portfolio, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="组合不存在")
    profile = db.scalar(select(InvestorProfile).where(InvestorProfile.portfolio_id == portfolio_id))
    weights, source = get_base_weights(db, portfolio, profile)
    return {"weights": weights, "source": source}


@router.put("/{portfolio_id}/base-weights")
def write_base_weights(portfolio_id: int, payload: BaseWeightsIn, db: Session = Depends(get_db)):
    portfolio = db.get(Portfolio, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="组合不存在")
    total = sum(payload.weights.values())
    if abs(total - 1.0) > 0.001:
        raise HTTPException(status_code=400, detail=f"基础权重合计必须为 100%，当前 {total:.2%}")
    if any(v < 0 for v in payload.weights.values()):
        raise HTTPException(status_code=400, detail="权重不能为负")
    _set_json_setting(db, f"portfolio:{portfolio_id}:base_weights", payload.weights, "用户自定义基础权重")
    audit.log(db, "base_weights.update", str(portfolio_id), payload.weights)
    db.add(StrategyConfig(code="manual_base_weights", name="用户基础权重", version=1, params=payload.weights, note="用户手动设置"))
    db.commit()
    return {"ok": True, "weights": payload.weights}


@router.get("/{portfolio_id}/allocation")
def get_allocation(portfolio_id: int, db: Session = Depends(get_db)):
    portfolio = db.get(Portfolio, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="组合不存在")
    return compute_allocation(db, portfolio, persist=False)


@router.post("/{portfolio_id}/allocation/recompute")
def recompute_allocation(portfolio_id: int, db: Session = Depends(get_db)):
    portfolio = db.get(Portfolio, portfolio_id)
    if not portfolio:
        raise HTTPException(status_code=404, detail="组合不存在")
    audit.log(db, "allocation.recompute", str(portfolio_id), {})
    db.commit()
    return compute_allocation(db, portfolio, persist=True)


@router.get("/{portfolio_id}/allocation/history")
def allocation_history(portfolio_id: int, db: Session = Depends(get_db), limit: int = 20):
    versions = db.execute(
        select(AllocationTarget.version, AllocationTarget.as_of)
        .where(AllocationTarget.portfolio_id == portfolio_id)
        .group_by(AllocationTarget.version, AllocationTarget.as_of)
        .order_by(AllocationTarget.version.desc())
        .limit(limit)
    ).all()
    result = []
    for version, as_of in versions:
        rows = db.scalars(
            select(AllocationTarget).where(
                AllocationTarget.portfolio_id == portfolio_id,
                AllocationTarget.version == version,
            )
        ).all()
        result.append(
            {
                "version": version,
                "as_of": as_of.isoformat(),
                "targets": [
                    {
                        "asset_class_code": r.asset_class_code,
                        "base_weight": r.base_weight,
                        "tactical_weight": r.tactical_weight,
                        "target_weight": r.target_weight,
                        "prev_weight": r.prev_weight,
                        "delta": r.delta,
                        "score": r.score,
                        "confidence": r.confidence,
                        "reason": r.reason,
                    }
                    for r in rows
                ],
            }
        )
    return {"items": result, "total": len(result)}
