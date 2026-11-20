"""可观测性接口：Langfuse 旁路配置与连通性测试。"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..observability import config_view, send_test_event, update_config
from ..schemas import LangfuseUpdate

router = APIRouter()


@router.get("/observability/langfuse")
def get_langfuse(db: Session = Depends(get_db)):
    return config_view(db)


@router.post("/observability/langfuse")
def set_langfuse(body: LangfuseUpdate, db: Session = Depends(get_db)):
    return update_config(db, enabled=body.enabled, sampling=body.sampling, host=body.host)


@router.post("/observability/langfuse/test")
def test_langfuse():
    return send_test_event()
