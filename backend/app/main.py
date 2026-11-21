"""FastAPI 入口：初始化数据库、写入目录、启动调度器、挂载前端静态文件。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import (
    backtest,
    catalog,
    journal,
    market,
    news_events,
    ops,
    opportunity_radar,
    refresh,
    portfolio,
    universe,
)
from app.core.config import PROJECT_ROOT, settings
from app.core.db import SessionLocal, init_db
from app.core.timeutil import now_cn
from app.jobs.scheduler import start_scheduler, stop_scheduler
from app.services.seed_service import seed_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("invest")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as db:
        counts = seed_all(db)
        logger.info("目录初始化完成: %s", counts)
    if settings.APP_ENV != "test":
        start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(
    title=settings.APP_NAME,
    description="面向个人投资者的全品类投资情报、事件分析、资产评分与配置研究系统。仅研究与模拟，不执行真实交易。",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 公网部署保护：设置 AUTH_PASSWORD 才启用，本地开发不设则跳过
if settings.AUTH_PASSWORD:
    from app.core.auth import BasicAuthMiddleware

    app.add_middleware(
        BasicAuthMiddleware,
        username=settings.AUTH_USER,
        password=settings.AUTH_PASSWORD,
    )
    logger.info("已启用 Basic 认证（用户 %s）", settings.AUTH_USER)
else:
    logger.warning("未设置 AUTH_PASSWORD，接口无认证保护，仅适合本机使用")


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("未处理异常: %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "服务器内部错误",
            "path": request.url.path,
            "time": now_cn().isoformat(sep=" ", timespec="seconds"),
            "hint": "请查看后端日志；单一数据源失败不应导致全站不可用。",
        },
    )


for router in (ops.router, catalog.router, market.router, market.macro_router,
               news_events.router, portfolio.router, backtest.router, universe.router,
               opportunity_radar.router, journal.router, refresh.router):
    app.include_router(router)


@app.get("/api")
def api_root():
    return {
        "name": settings.APP_NAME,
        "docs": "/docs",
        "health": "/api/health",
        "disclaimer": "本系统仅提供研究与模拟建议，不执行真实券商交易。",
    }


# 单端口部署：存在构建产物时直接托管前端
DIST = PROJECT_ROOT / "frontend" / "dist"
if DIST.exists():

    @app.get("/{full_path:path}")
    def spa(full_path: str):
        """优先返回真实静态文件，其余交给 Vue Router（避免 /assets 路由被静态目录抢占）。"""
        from fastapi import HTTPException
        from fastapi.responses import FileResponse

        # 未命中的 /api 路径必须返回 404，不能回退到 index.html
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="接口不存在")
        target = DIST / full_path
        if full_path and target.is_file():
            return FileResponse(target)
        return FileResponse(DIST / "index.html")
