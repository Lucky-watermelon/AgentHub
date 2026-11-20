"""配额与限流：按调用凭证（AppKey / PAT）维度。

三层限额：
1. RPS      滑动窗口（1 秒）限制每秒请求数
2. 并发     同时在途请求数上限
3. 日配额   当天累计调用次数上限

限流状态在进程内存中维护（单机 lite 形态）；多副本部署时应换成 Redis。
超限统一返回 ``AH-QUOTA-429`` 并带 ``Retry-After``。
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import date

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from .auth import optional_key
from .db import SessionLocal, get_db
from .errors import ApiError
from .models import AppKey, QuotaPolicy, QuotaUsage, now
from .runner import uid

DEFAULT_RPS = 10
DEFAULT_CONCURRENCY = 4
DEFAULT_DAILY_CALLS = 1000


class _Limiter:
    """进程内滑动窗口 + 并发计数。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, deque] = defaultdict(deque)
        self._inflight: dict[str, int] = defaultdict(int)

    def try_acquire(self, key_id: str, rps: int, concurrency: int) -> tuple[bool, str, int]:
        ts = time.monotonic()
        with self._lock:
            window = self._windows[key_id]
            while window and ts - window[0] >= 1.0:
                window.popleft()
            if rps and len(window) >= rps:
                retry_ms = max(50, int(1000 * (1.0 - (ts - window[0]))))
                return False, "rps", retry_ms
            if concurrency and self._inflight[key_id] >= concurrency:
                return False, "concurrency", 200
            window.append(ts)
            self._inflight[key_id] += 1
            return True, "", 0

    def release(self, key_id: str) -> None:
        with self._lock:
            if self._inflight[key_id] > 0:
                self._inflight[key_id] -= 1

    def snapshot(self, key_id: str, rps: int) -> dict:
        ts = time.monotonic()
        with self._lock:
            window = self._windows[key_id]
            while window and ts - window[0] >= 1.0:
                window.popleft()
            return {"window": len(window), "rps_limit": rps, "inflight": self._inflight[key_id]}


limiter = _Limiter()


def today() -> str:
    return date.today().isoformat()


# ---------------- 策略与用量 ----------------
def get_policy(db: Session, key_id: str) -> QuotaPolicy:
    policy = db.query(QuotaPolicy).filter(QuotaPolicy.app_key_id == key_id).first()
    if not policy:
        policy = QuotaPolicy(id=uid("qp"), app_key_id=key_id, rps=DEFAULT_RPS,
                             concurrency=DEFAULT_CONCURRENCY, daily_calls=DEFAULT_DAILY_CALLS,
                             enabled=True)
        db.add(policy)
        db.commit()
    return policy


def get_usage(db: Session, key_id: str, day: str | None = None) -> QuotaUsage:
    d = day or today()
    row = (db.query(QuotaUsage)
             .filter(QuotaUsage.app_key_id == key_id, QuotaUsage.day == d).first())
    if not row:
        row = QuotaUsage(id=uid("qu"), app_key_id=key_id, day=d, calls=0, blocks=0)
        db.add(row)
        db.commit()
    return row


def _record(key_id: str, blocked: bool = False) -> None:
    """用量记账使用独立短事务，失败不影响业务响应。"""
    session = SessionLocal()
    try:
        d = today()
        row = (session.query(QuotaUsage)
                      .filter(QuotaUsage.app_key_id == key_id, QuotaUsage.day == d).first())
        if not row:
            row = QuotaUsage(id=uid("qu"), app_key_id=key_id, day=d, calls=0, blocks=0)
            session.add(row)
        if blocked:
            row.blocks += 1
        else:
            row.calls += 1
        row.updated_at = now()
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


# ---------------- 依赖 ----------------
def quota_guard(request: Request,
                key: AppKey | None = Depends(optional_key),
                db: Session = Depends(get_db)):
    """对外调用入口的限额闸门：先校验日配额，再占用并发与 RPS 名额。"""
    if key is None:  # 匿名调用不计配额（强制鉴权时不会走到这里）
        yield None
        return

    policy = get_policy(db, key.id)
    if not policy.enabled:
        yield key
        return

    usage = get_usage(db, key.id)
    if policy.daily_calls and usage.calls >= policy.daily_calls:
        _record(key.id, blocked=True)
        raise ApiError(
            f"日调用配额已用尽（{usage.calls}/{policy.daily_calls}），请次日重试或调整配额",
            code="AH-QUOTA-429", status_code=429,
            headers={"Retry-After": "60", "X-RateLimit-Scope": "daily"},
        )

    ok, reason, retry_ms = limiter.try_acquire(key.id, policy.rps, policy.concurrency)
    if not ok:
        _record(key.id, blocked=True)
        label = "每秒请求数超限" if reason == "rps" else "并发请求数超限"
        raise ApiError(
            f"{label}（上限 {policy.rps} rps / {policy.concurrency} 并发）",
            code="AH-QUOTA-429", status_code=429,
            headers={"Retry-After": str(max(1, retry_ms // 1000 or 1)),
                     "X-RateLimit-Scope": reason},
        )

    try:
        yield key
    finally:
        limiter.release(key.id)
        _record(key.id)


# ---------------- 展示 ----------------
def policy_view(db: Session, key: AppKey) -> dict:
    policy = get_policy(db, key.id)
    usage = get_usage(db, key.id)
    return {
        "key_id": key.id,
        "app": key.app,
        "prefix": key.prefix,
        "status": key.status,
        "enabled": policy.enabled,
        "rps": policy.rps,
        "concurrency": policy.concurrency,
        "daily_calls": policy.daily_calls,
        "used_calls": usage.calls,
        "blocked": usage.blocks,
        "remaining": max(0, policy.daily_calls - usage.calls) if policy.daily_calls else None,
        "live": limiter.snapshot(key.id, policy.rps),
        "day": usage.day,
    }
