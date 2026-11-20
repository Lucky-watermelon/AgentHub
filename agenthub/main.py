"""FastAPI 应用装配：路由、异常、静态资源、初始化。"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .api import compat_router, router as api_router
from .auth import authenticate, extract_token
from .config import settings
from .db import SessionLocal, init_db
from .errors import ApiError
from .seed import seed_all

logger = logging.getLogger("agenthub")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .executor import ensure_started

    init_db()
    ensure_started()
    if settings.seed_demo:
        db = SessionLocal()
        try:
            seed_all(db)
        finally:
            db.close()
    logger.info("AgentHub 启动完成 profile=%s data=%s", settings.profile, settings.data_path)
    yield
    # 优雅停机：等待在途运行结束，避免任务被硬切断
    from .executor import shutdown

    await asyncio.to_thread(shutdown)


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
app.include_router(compat_router, prefix="/v1")


@app.middleware("http")
async def auth_guard(request: Request, call_next):
    """AH_AUTH_REQUIRED=true 时校验对外接口凭证（SSE 可用 ?access_token= 传递）。"""
    if settings.auth_required and request.method != "OPTIONS":
        path = request.url.path
        if path.startswith("/api/v1") or path.startswith("/v1/"):
            db = SessionLocal()
            try:
                authenticate(db, extract_token(request))
            except ApiError as exc:
                return JSONResponse(status_code=exc.status_code,
                                    content={"code": exc.code, "message": exc.message,
                                             "retryable": exc.retryable})
            finally:
                db.close()
    return await call_next(request)


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    payload = {"code": exc.code, "message": exc.message, "retryable": exc.retryable}
    if getattr(exc, "errors", None):
        payload["errors"] = exc.errors
    headers = getattr(exc, "headers", None) or None
    return JSONResponse(status_code=exc.status_code, content=payload, headers=headers)


@app.get("/health")
def health():
    return {"status": "ok", "version": __version__, "profile": settings.profile}


@app.get("/reports/", include_in_schema=False)
def reports_index():
    """报告目录索引：StaticFiles 没有 index.html，这里补一个简单列表页。"""
    from datetime import datetime

    from fastapi.responses import HTMLResponse

    files = sorted(settings.reports_path.glob("*.html"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:200]
    rows = "".join(
        f'<li><a href="/reports/{f.name}">{f.name}</a>'
        f'<span>{datetime.fromtimestamp(f.stat().st_mtime):%Y-%m-%d %H:%M} · {f.stat().st_size // 1024} KB</span></li>'
        for f in files
    ) or "<li>暂无报告，先发起一次运行</li>"
    return HTMLResponse(
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<title>报告目录 · AgentHub</title><style>"
        "body{font:14px/1.7 -apple-system,'Microsoft YaHei',sans-serif;background:#f5f7fb;color:#1c2433;padding:32px}"
        ".box{max-width:900px;margin:0 auto;background:#fff;border:1px solid #e3e8f0;border-radius:12px;padding:24px 28px}"
        "h1{font-size:19px;margin:0 0 16px}ul{list-style:none;margin:0;padding:0}"
        "li{display:flex;justify-content:space-between;padding:10px 0;border-bottom:1px solid #f0f2f7}"
        "li:last-child{border:none}a{color:#2f6bff;text-decoration:none}span{color:#6b7689;font-size:12px}"
        "</style></head><body><div class='box'><h1>HTML 全景分析报告（最近 200 份）</h1>"
        f"<ul>{rows}</ul></div></body></html>"
    )


settings.reports_path.mkdir(parents=True, exist_ok=True)
app.mount("/reports", StaticFiles(directory=str(settings.reports_path)), name="reports")
app.mount("/", StaticFiles(directory=str(settings.web_path), html=True), name="web")
