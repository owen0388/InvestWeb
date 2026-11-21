"""回测接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.schemas import BacktestIn
from app.core.db import get_db
from app.models.entities import BacktestRun
from app.services import audit
from app.services.backtest_service import run_backtest, run_comparison

router = APIRouter(prefix="/api/backtests", tags=["backtest"])


def _payload(run: BacktestRun) -> dict:
    return {
        "id": run.id,
        "strategy_code": run.strategy_code,
        "status": run.status,
        "start_date": run.start_date.isoformat(),
        "end_date": run.end_date.isoformat(),
        "params": run.params,
        "metrics": run.metrics,
        "assumptions": run.assumptions,
        "warnings": run.warnings,
        "error": run.error,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "has_curve": bool(run.equity_curve),
        "rebalances": len(run.rebalances or []),
    }


@router.post("/comparison")
def compare_strategies(payload: dict | None = None, db: Session = Depends(get_db)):
    """多策略对比：买入持有 / 定期再平衡 / 评分倾斜 / 趋势跟随 / 相对强弱轮动。

    同区间、同成本、同基准；不做这个对比，单策略收益没有参照系。
    """
    params = payload or {}
    result = run_comparison(db, params)
    if not result.get("ok"):
        return result
    audit.log(db, "backtest.comparison", "multi", {"params": params})
    db.commit()
    return result


@router.get("")
def list_backtests(db: Session = Depends(get_db), limit: int = 20):
    runs = db.scalars(select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(limit)).all()
    return {"items": [_payload(r) for r in runs], "total": len(runs)}


@router.post("")
def create_backtest(payload: BacktestIn, db: Session = Depends(get_db)):
    params = payload.model_dump(exclude_none=True)
    params.pop("start_date", None)
    params.pop("end_date", None)
    audit.log(db, "backtest.run", params.get("strategy_code", "score_tilt"), params)
    db.commit()
    run = run_backtest(db, params)
    if run.status == "failed":
        raise HTTPException(status_code=422, detail={"message": run.error, "backtest_id": run.id})
    return _detail(db, run.id)


@router.get("/{backtest_id}")
def get_backtest(backtest_id: int, db: Session = Depends(get_db)):
    return _detail(db, backtest_id)


def _detail(db: Session, backtest_id: int) -> dict:
    run = db.get(BacktestRun, backtest_id)
    if not run:
        raise HTTPException(status_code=404, detail="回测记录不存在")
    return {
        **_payload(run),
        "equity_curve": run.equity_curve,
        "benchmark_curve": run.benchmark_curve,
        "rebalances": run.rebalances,
        "data_note": "曲线与指标均来自真实历史收盘价；不使用未来数据，不保证未来收益。",
    }
