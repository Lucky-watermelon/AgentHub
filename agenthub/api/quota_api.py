"""配额管理接口。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..errors import NotFound
from ..models import AppKey, QuotaPolicy, now
from ..quota import get_policy, get_usage, policy_view
from ..schemas import QuotaUpdate

router = APIRouter()


@router.get("/quota")
def list_quota(db: Session = Depends(get_db)):
    keys = db.query(AppKey).order_by(AppKey.created_at.desc()).all()
    return [policy_view(db, k) for k in keys]


@router.get("/quota/usage")
def quota_usage(day: str | None = None, db: Session = Depends(get_db)):
    keys = {k.id: k for k in db.query(AppKey).all()}
    from ..quota import get_usage as usage_of

    out = []
    for key_id, key in keys.items():
        usage = usage_of(db, key_id, day)
        out.append({"key_id": key_id, "app": key.app, "prefix": key.prefix,
                    "day": usage.day, "calls": usage.calls, "blocked": usage.blocks})
    return out


@router.put("/quota/{key_id}")
def update_quota(key_id: str, body: QuotaUpdate, db: Session = Depends(get_db)):
    key = db.get(AppKey, key_id)
    if not key:
        raise NotFound("凭证不存在")
    policy: QuotaPolicy = get_policy(db, key_id)
    if body.rps is not None:
        policy.rps = max(0, body.rps)
    if body.concurrency is not None:
        policy.concurrency = max(0, body.concurrency)
    if body.daily_calls is not None:
        policy.daily_calls = max(0, body.daily_calls)
    if body.enabled is not None:
        policy.enabled = body.enabled
    policy.updated_at = now()
    db.commit()
    return policy_view(db, key)


@router.post("/quota/{key_id}/reset")
def reset_usage(key_id: str, db: Session = Depends(get_db)):
    key = db.get(AppKey, key_id)
    if not key:
        raise NotFound("凭证不存在")
    usage = get_usage(db, key_id)
    usage.calls = 0
    usage.blocks = 0
    usage.updated_at = now()
    db.commit()
    return policy_view(db, key)
