"""迁移执行器：schema_migrations 版本表 + 顺序应用。"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger("agenthub.migrations")

SCHEMA_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    description VARCHAR(200) NOT NULL DEFAULT '',
    applied_at VARCHAR(30) NOT NULL DEFAULT ''
)
"""


def _migration_modules():
    from . import (m0001_baseline, m0002_run_resume, m0003_tool_http,
                   m0004_skill_content, m0005_demo_dag)

    return [m0001_baseline, m0002_run_resume, m0003_tool_http,
            m0004_skill_content, m0005_demo_dag]


MIGRATIONS = _migration_modules()


def applied_versions(engine: Engine) -> list[int]:
    with engine.connect() as conn:
        conn.execute(text(SCHEMA_TABLE))
        conn.commit()
        rows = conn.execute(text("SELECT version FROM schema_migrations ORDER BY version")).fetchall()
    return [row[0] for row in rows]


def run_migrations(engine: Engine) -> list[int]:
    """按版本顺序应用未执行的迁移，返回本次新应用的版本号。"""
    newly: list[int] = []
    with engine.begin() as conn:
        conn.execute(text(SCHEMA_TABLE))
        done = {row[0] for row in conn.execute(text("SELECT version FROM schema_migrations")).fetchall()}
        for module in _migration_modules():
            version = getattr(module, "VERSION", 0)
            if not version or version in done:
                continue
            module.up(conn)
            conn.execute(
                text("INSERT INTO schema_migrations (version, description, applied_at) "
                     "VALUES (:v, :d, :t)"),
                {"v": version,
                 "d": getattr(module, "DESCRIPTION", "")[:200],
                 "t": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
            )
            newly.append(version)
            logger.info("applied migration %s: %s", version, getattr(module, "DESCRIPTION", ""))
    return newly
