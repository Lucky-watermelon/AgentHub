"""API 路由聚合。

- ``router``       原生契约，前缀 /api/v1
- ``compat_router`` OpenAI 兼容契约，前缀 /v1（在 main 中挂载）
"""
from fastapi import APIRouter

from . import access, catalog, knowledge, models_api, public, runs

router = APIRouter(prefix="/api/v1")
router.include_router(catalog.router, tags=["agents/skills/tools"])
router.include_router(knowledge.router, tags=["knowledge"])
router.include_router(models_api.router, tags=["models"])
router.include_router(runs.router, tags=["runs/monitor"])
router.include_router(public.router, tags=["invoke"])
router.include_router(access.router, tags=["access/system"])

compat_router = public.compat
