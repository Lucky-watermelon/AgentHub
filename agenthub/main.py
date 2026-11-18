"""FastAPI 应用装配：路由、异常、静态资源、初始化。"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import router as api_router
from .config import settings
from .db import SessionLocal, init_db
from .errors import ApiError
from .seed import seed_all

logger = logging.getLogger("agenthub")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings.seed_demo:
        db = SessionLocal()
        try:
            seed_all(db)
        finally:
            db.close()
    logger.info("AgentHub 启动完成 profile=%s data=%s", settings.profile, settings.data_path)
    yield


app = FastAPI(
    title="AgentHub",
    version=__version__,
    description="企业级智能体开发与运行支撑平台（裁剪版实现）：模型接入 / 智能体编排 / 工具调用 / 知识库集成 / 运行监控",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")] or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    payload = {"code": exc.code, "message": exc.message, "retryable": exc.retryable}
    if getattr(exc, "errors", None):
        payload["errors"] = exc.errors
    return JSONResponse(status_code=exc.status_code, content=payload)


@app.get("/health")
def health():
    return {"status": "ok", "version": __version__, "profile": settings.profile}


settings.reports_path.mkdir(parents=True, exist_ok=True)
app.mount("/reports", StaticFiles(directory=str(settings.reports_path)), name="reports")
app.mount("/", StaticFiles(directory=str(settings.web_path), html=True), name="web")
