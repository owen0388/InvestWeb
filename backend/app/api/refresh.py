"""数据刷新接口：统一手动入口 + 数据新鲜度。

长任务放后台执行，前端轮询 /status 看进度，避免请求超时。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db, session_scope
from app.services import refresh_service

logger = logging.getLogger("invest.refresh")

router = APIRouter(prefix="/api/refresh", tags=["refresh"])


def _run_steps(steps: list[str]) -> None:
    """顺序执行刷新步骤。

    单步失败不阻断后续：行情采集挂了，仍然应该按现有数据重算雷达，
    否则用户什么都拿不到。
    """
    for step in steps:
        refresh_service.mark_start(step)
        try:
            with session_scope() as db:
                result = refresh_service.run_scope(db, step)
                logger.info("refresh %s 完成: %s", step, result.get("message"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("refresh %s 失败: %s", step, exc)
        finally:
            refresh_service.mark_done(step)


@router.get("/status")
def status(db: Session = Depends(get_db)):
    """每层数据的最后更新时间、是否过期、是否有刷新在跑。"""
    return refresh_service.status(db)


@router.post("")
def trigger(
    payload: dict | None = None,
    background: BackgroundTasks = None,
    db: Session = Depends(get_db),
):
    scope = (payload or {}).get("scope", "all")
    steps = refresh_service.plan(scope)
    if not steps:
        raise HTTPException(
            status_code=400,
            detail=f"未知 scope：{scope}，可选 {sorted(refresh_service.SCOPES)} 或 all",
        )
    # 采集源有频率限制，并发刷新会把它们打挂，因此全局串行
    if refresh_service.running_scopes():
        running = [item["label"] for item in refresh_service.running_scopes()]
        raise HTTPException(
            status_code=409,
            detail=f"已有刷新任务在执行：{'、'.join(running)}，请等待完成",
        )

    estimate = sum(refresh_service.SCOPES.get(step, ("", 0, ""))[1] for step in steps)
    # BackgroundTasks 由 FastAPI 注入，响应返回后执行，不阻塞请求
    background.add_task(_run_steps, steps)
    return {
        "accepted": True,
        "scope": scope,
        "steps": steps,
        "estimate_seconds": estimate,
        "message": f"已提交 {len(steps)} 个步骤，预计 {estimate} 秒，可轮询 /api/refresh/status",
    }
