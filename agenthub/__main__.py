"""启动入口：python -m agenthub"""
from __future__ import annotations

import uvicorn

from .config import settings

if __name__ == "__main__":
    uvicorn.run(
        "agenthub.main:app",
        host=settings.host,
        port=settings.port,
        reload=False,
        log_level="info",
    )
