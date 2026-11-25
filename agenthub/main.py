"""FastAPI 应用装配：路由、异常、静态资源、初始化。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError, TimeoutError as SATimeoutError

from . import __version__
from .api import compat_router, router as api_router
from .auth import authenticate, extract_token
from .config import settings
from .db import SessionLocal, init_db
from .docs_meta import APP_DESCRIPTION, TAGS, install as install_openapi_docs
from .errors import ApiError
from .seed import seed_once, sync_builtin_tools

logger = logging.getLogger("agenthub")


class JsonFormatter(logging.Formatter):
    """结构化日志：单行 JSON，便于采集与检索（含 request_id）。"""

    def format(self, record: logging.LogRecord) -> str:
        from .tracing import current_request_id

        payload = {
            "ts": datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3],
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": current_request_id(),
        }
        extra = getattr(record, "extra_fields", None)
        if isinstance(extra, dict):
            payload.update(extra)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)[:1200]
        return json.dumps(payload, ensure_ascii=False)


def _configure_logging() -> None:
    """给应用 logger 单独挂 JSON handler。

    uvicorn 的 dictConfig 会接管 root logger（disable_existing_loggers=True），
    因此这里显式配置 agenthub 命名空间并关闭向 root 传播，保证结构化日志稳定输出。
    """
    access = logging.getLogger("agenthub.access")
    if any(isinstance(handler.formatter, JsonFormatter) for handler in access.handlers):
        return
    from logging.handlers import RotatingFileHandler

    # 落 UTF-8 文件（Windows 控制台重定向时默认编码会破坏中文），按 10MB 轮转
    handler = RotatingFileHandler(settings.data_path / "agenthub.log", maxBytes=10 * 1024 * 1024,
                                  backupCount=5, encoding="utf-8")
    handler.setFormatter(JsonFormatter())
    for name in ("agenthub", "agenthub.access"):
        target = logging.getLogger(name)
        target.handlers = [handler]
        target.setLevel(logging.INFO)
        target.propagate = False
        target.disabled = False


@asynccontextmanager
async def lifespan(app: FastAPI):
    from .executor import ensure_started
    from .runner import recover_stale_runs

    _configure_logging()
    init_db()
    ensure_started()
    db = SessionLocal()
    try:
        if settings.seed_demo:
            seed_once(db)
        # 内置工具随版本增加时对旧库做幂等补齐（依赖校验需要 tools 表登记）
        sync_builtin_tools(db)
    finally:
        db.close()
    recovered = recover_stale_runs()
    logger.info("AgentHub 启动完成 profile=%s data=%s recovered_runs=%s",
                settings.profile, settings.data_path, recovered,
                extra={"extra_fields": {"recovered_runs": recovered}})
    yield
    # 优雅停机：等待在途运行结束，避免任务被硬切断
    from .executor import shutdown

    await asyncio.to_thread(shutdown)


app = FastAPI(
    title="AgentHub",
    version=__version__,
    description=APP_DESCRIPTION,
    openapi_tags=TAGS,
    docs_url=None,
    swagger_ui_parameters={
        "persistAuthorization": True,
        "displayRequestDuration": True,
        "filter": True,
        "tryItOutEnabled": True,
        "docExpansion": "list",
    },
    lifespan=lifespan,
)
install_openapi_docs(app)


@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui():
    """自定义 Swagger 页面：注入样式，收紧默认的 50px 上下留白。"""
    from fastapi.openapi.docs import get_swagger_ui_html
    from fastapi.responses import HTMLResponse

    html = get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " · API 文档",
        swagger_ui_parameters=app.swagger_ui_parameters,
    )
    body = html.body.decode("utf-8") if isinstance(html.body, bytes) else str(html.body)
    return HTMLResponse(body.replace(
        "</head>", '<link rel="stylesheet" href="/assets/swagger-docs.css"></head>'
    ))

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
async def request_context(request: Request, call_next):
    """为每个请求生成 request_id，写入响应头并记录结构化访问日志。"""
    from .tracing import clear, new_request_id, set_request_id

    rid = request.headers.get("x-request-id") or new_request_id()
    set_request_id(rid)
    started = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers["X-Request-ID"] = rid
        return response
    finally:
        logging.getLogger("agenthub.access").info("request", extra={"extra_fields": {
            "method": request.method,
            "path": request.url.path,
            "status": status,
            "dur_ms": round((time.perf_counter() - started) * 1000, 1),
            "client": request.client.host if request.client else "",
        }})
        clear()


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


@app.exception_handler(SQLAlchemyError)
async def db_error_handler(request: Request, exc: SQLAlchemyError):
    """数据库锁等待/连接池等异常：统一映射为可重试的 503，而不是裸 500。

    注意：SQLite 锁超时会被 SQLAlchemy 包装成 PendingRollbackError，
    因此这里按基类 SQLAlchemyError 捕获，再做关键字判定。
    """
    from .tracing import current_request_id

    detail = str(getattr(exc, "orig", None) or exc)
    lowered = detail.lower()
    busy = any(word in lowered for word in ("locked", "busy", "pendingrollback", "connection timed out"))
    return JSONResponse(
        status_code=503,
        content={
            "code": "AH-DB-503" if busy else "AH-DB-500",
            "message": "数据库繁忙，请稍后重试" if busy else "数据库操作失败",
            "retryable": busy,
            "request_id": current_request_id(),
            "detail": detail[:200],
        },
        headers={"Retry-After": "2"} if busy else None,
    )


@app.exception_handler(SATimeoutError)
async def pool_timeout_handler(request: Request, exc):
    """连接池取连接超时：同样是可重试错误。"""
    from .tracing import current_request_id

    return JSONResponse(
        status_code=503,
        content={"code": "AH-DB-503", "message": "数据库连接池繁忙，请稍后重试",
                 "retryable": True, "request_id": current_request_id()},
        headers={"Retry-After": "2"},
    )


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError):
    from .tracing import current_request_id

    payload = {"code": exc.code, "message": exc.message, "retryable": exc.retryable,
               "request_id": current_request_id()}
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
