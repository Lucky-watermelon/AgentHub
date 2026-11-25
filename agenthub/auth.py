"""对外调用鉴权：AppKey / PAT。

- 凭证只存 sha256 摘要，日志与响应体不出现明文。
- ``AH_AUTH_REQUIRED=true`` 时强制校验 ``/api/v1`` 与 ``/v1``；
  关闭时仍会尝试解析凭证，用于把调用归因到具体应用。
- 支持三种携带方式：``Authorization: Bearer <key>``、``X-API-Key``、``?access_token=``
  （最后一种为 SSE / EventSource 等无法自定义请求头的场景准备）。
"""
from __future__ import annotations

import hashlib
from datetime import date

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .errors import AuthRequired
from .models import AppKey

DEMO_KEY = "ah_pat_demo_local_dev"


def digest(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def extract_token(request: Request) -> str:
    header = request.headers.get("authorization") or ""
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return (request.headers.get("x-api-key")
            or request.query_params.get("access_token")
            or "").strip()


def authenticate(db: Session, token: str) -> AppKey:
    if not token:
        raise AuthRequired("缺少凭证：请求头需带 Authorization: Bearer <AppKey>")
    key = db.query(AppKey).filter(AppKey.digest == digest(token)).first()
    if not key:
        raise AuthRequired("凭证无效")
    if key.status != "启用":
        raise AuthRequired(f"凭证已{key.status}")
    if key.expires_at:
        try:
            expired = date.fromisoformat(key.expires_at[:10]) < date.today()
        except ValueError:
            expired = False
        if expired:
            raise AuthRequired(f"凭证已于 {key.expires_at} 过期")
    return key


def optional_key(request: Request, db: Session = Depends(get_db)) -> AppKey | None:
    """对外接口的调用方归因：未开启强制鉴权时允许匿名，但能识别出凭证。"""
    token = extract_token(request)
    if not token:
        if settings.auth_required:
            raise AuthRequired("缺少凭证：请求头需带 Authorization: Bearer <AppKey>")
        return None
    return authenticate(db, token)


def key_label(key: AppKey | None) -> str:
    return f"{key.app}/{key.prefix}" if key else "anonymous"
