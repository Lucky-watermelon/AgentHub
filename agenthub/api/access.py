"""接入与系统接口：AppKey、系统信息、健康检查、数据落盘。"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import __version__, builtin_tools
from ..config import settings
from ..db import get_db
from ..errors import NotFound
from ..models import AppKey, QuotaUsage, Run, ToolCall
from ..runner import uid
from ..schemas import KeyCreate, dt, key_out

router = APIRouter()


def _digest(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def _new_plain(key_type: str) -> str:
    prefix = "ah_live_" if key_type == "AppKey" else "ah_pat_"
    return prefix + secrets.token_hex(12)


@router.get("/keys")
def list_keys(db: Session = Depends(get_db)):
    """凭证清单：附带最近使用时间（取配额用量表的最后更新时间）。"""
    usage_rows = (db.query(QuotaUsage.app_key_id, QuotaUsage.updated_at,
                           QuotaUsage.calls, QuotaUsage.blocks)
                    .order_by(QuotaUsage.updated_at.desc()).all())
    last_used: dict[str, str] = {}
    for key_id, ts, calls, blocks in usage_rows:
        # 仅打开控制台也会创建用量行，只有真正发生调用 / 拦截才算「最近使用」
        if calls or blocks:
            last_used.setdefault(key_id, dt(ts) or "")
    out = []
    for key in db.query(AppKey).order_by(AppKey.created_at.desc()).all():
        item = key_out(key)
        item["last_used"] = last_used.get(key.id, "")
        out.append(item)
    return out


@router.post("/keys", status_code=201)
def create_key(body: KeyCreate, db: Session = Depends(get_db)):
    plain = _new_plain(body.type)
    expires = (datetime.now() + timedelta(days=body.days)).strftime("%Y-%m-%d")
    key = AppKey(id=uid("ak"), app=body.app, type=body.type,
                 prefix=plain[:14] + "…", digest=_digest(plain),
                 status="启用", expires_at=expires)
    db.add(key)
    db.commit()
    data = key_out(key)
    data["plain"] = plain  # 仅本次返回，服务端只存摘要
    return data


@router.patch("/keys/{key_id}")
def update_key(key_id: str, payload: dict, db: Session = Depends(get_db)):
    key = db.get(AppKey, key_id)
    if not key:
        raise NotFound("凭证不存在")
    if "status" in payload:
        key.status = payload["status"]
    db.commit()
    return key_out(key)


@router.post("/keys/{key_id}/rotate")
def rotate_key(key_id: str, payload: dict | None = None, db: Session = Depends(get_db)):
    """轮换凭证：同一行换新摘要，旧明文立即失效，配额与统计保持不变。"""
    key = db.get(AppKey, key_id)
    if not key:
        raise NotFound("凭证不存在")
    plain = _new_plain(key.type)
    key.digest = _digest(plain)
    key.prefix = plain[:14] + "…"
    key.status = "启用"
    days = (payload or {}).get("days")
    if days:
        key.expires_at = (datetime.now() + timedelta(days=int(days))).strftime("%Y-%m-%d")
    db.commit()
    data = key_out(key)
    data["plain"] = plain  # 仅本次返回
    return data


@router.post("/keys/{key_id}/revoke")
def revoke_key(key_id: str, db: Session = Depends(get_db)):
    key = db.get(AppKey, key_id)
    if not key:
        raise NotFound("凭证不存在")
    key.status = "停用"
    db.commit()
    return key_out(key)


@router.get("/system/contract")
def system_contract():
    """接口契约：参数、请求/响应示例、curl 与是否可测试，直接来自 OpenAPI。"""
    from ..docs_meta import build_contract

    return build_contract()


@router.get("/system/meta")
def system_meta():
    return {
        "name": settings.app_name, "version": __version__, "profile": settings.profile,
        "llm_mock": settings.llm_mock, "auth_required": settings.auth_required,
        "builtin_tools": builtin_tools.names(),
        "data_dir": str(settings.data_path), "reports_dir": str(settings.reports_path),
    }


@router.get("/system/health")
def system_health(db: Session = Depends(get_db)):
    try:
        db.query(Run).count()
        db_ok = True
    except Exception:
        db_ok = False
    reports_writable = True
    try:
        settings.reports_path.mkdir(parents=True, exist_ok=True)
    except Exception:
        reports_writable = False
    from ..executor import pool_stats

    return {
        "services": [
            {"name": "ah-api", "status": "健康" if db_ok else "异常", "role": "控制面 / 对客 API"},
            {"name": "ah-runtime", "status": "健康", "role": "LangGraph 运行时"},
            {"name": "postgres/sqlite", "status": "健康" if db_ok else "异常", "role": "元数据 + Checkpoint"},
            {"name": "reports", "status": "健康" if reports_writable else "异常", "role": "报告产物目录"},
        ],
        "run_pool": pool_stats(),
        "builtin_tools": len(builtin_tools.names()),
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


@router.get("/system/storage")
def system_storage(db: Session = Depends(get_db)):
    def dir_size(path) -> int:
        total = 0
        if path.exists():
            for f in path.rglob("*"):
                if f.is_file():
                    total += f.stat().st_size
        return total

    def human(size: int) -> str:
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024 or unit == "GB":
                return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
            size /= 1024
        return f"{size:.1f} GB"

    items = [
        {"path": str(settings.data_path), "desc": "数据库（智能体 / 运行 / 知识库 / 凭证）",
         "size": human(dir_size(settings.data_path))},
        {"path": str(settings.reports_path), "desc": "HTML 全景分析报告",
         "size": human(dir_size(settings.reports_path))},
    ]
    return {"items": items, "runs": db.query(Run).count(),
            "tool_calls": db.query(ToolCall).count()}
