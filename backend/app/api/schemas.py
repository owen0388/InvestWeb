"""API 请求模型（仅用于写入类接口的字段校验）。"""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class SourceUpdate(BaseModel):
    enabled: Optional[bool] = None
    interval_minutes: Optional[int] = Field(default=None, ge=0, le=10080)


class InstrumentUpdate(BaseModel):
    enabled: Optional[bool] = None
    favorite: Optional[bool] = None
    notes: Optional[str] = None
    manual_value: Optional[float] = None
    manual_note: Optional[str] = None


class HoldingIn(BaseModel):
    symbol: str
    quantity: float = 0.0
    cost_price: float = 0.0
    market_value: Optional[float] = None


class HoldingUpdate(BaseModel):
    quantity: Optional[float] = None
    cost_price: Optional[float] = None
    market_value: Optional[float] = None


class ProfileIn(BaseModel):
    risk_tolerance: Optional[str] = None
    horizon_years: Optional[float] = Field(default=None, ge=0, le=60)
    max_drawdown: Optional[float] = Field(default=None, ge=0, le=1)
    liquidity_need_ratio: Optional[float] = Field(default=None, ge=0, le=1)
    emergency_cash_months: Optional[float] = Field(default=None, ge=0, le=120)
    monthly_income: Optional[float] = Field(default=None, ge=0)
    monthly_expense: Optional[float] = Field(default=None, ge=0)
    allowed_classes: Optional[list[str]] = None
    excluded_symbols: Optional[list[str]] = None
    qualifications: Optional[list[str]] = None
    notes: Optional[str] = None
    confirmed: Optional[bool] = None


class ConstraintIn(BaseModel):
    asset_class_code: str
    min_weight: float = Field(ge=0, le=1)
    max_weight: float = Field(ge=0, le=1)
    max_tactical_tilt: Optional[float] = Field(default=None, ge=0, le=0.5)


class PortfolioIn(BaseModel):
    name: Optional[str] = None
    total_value: Optional[float] = Field(default=None, ge=0)
    cash_value: Optional[float] = Field(default=None, ge=0)
    liabilities: Optional[float] = Field(default=None, ge=0)
    notes: Optional[str] = None


class BaseWeightsIn(BaseModel):
    weights: dict[str, float]


class BacktestIn(BaseModel):
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    max_tilt: Optional[float] = Field(default=None, ge=0, le=0.3)
    transaction_cost_bps: Optional[float] = Field(default=None, ge=0, le=200)
    rebalance_days: Optional[int] = Field(default=None, ge=5, le=250)


class SettingsIn(BaseModel):
    notifications: Optional[dict] = None
    data_retention: Optional[dict] = None
    ai_budget: Optional[dict] = None
    scheduler: Optional[dict] = None
    notification_threshold: Optional[float] = Field(default=None, ge=0, le=100)
