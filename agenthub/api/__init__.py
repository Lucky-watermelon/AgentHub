"""API 路由聚合。"""
from fastapi import APIRouter

from . import access, catalog, knowledge, models_api, runs

router = APIRouter(prefix="/api/v1")
router.include_router(catalog.router, tags=["agents/skills/tools"])
router.include_router(knowledge.router, tags=["knowledge"])
router.include_router(models_api.router, tags=["models"])
router.include_router(runs.router, tags=["runs/monitor"])
router.include_router(access.router, tags=["access/system"])
