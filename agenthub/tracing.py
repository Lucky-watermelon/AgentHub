"""请求级追踪上下文：request_id 贯穿日志、事件与错误响应。"""
from __future__ import annotations

import contextvars
import uuid

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("ah_request_id", default="")


def new_request_id() -> str:
    rid = uuid.uuid4().hex[:12]
    _request_id.set(rid)
    return rid


def set_request_id(rid: str) -> None:
    _request_id.set(rid or "")


def current_request_id() -> str:
    return _request_id.get()


def clear() -> None:
    _request_id.set("")
