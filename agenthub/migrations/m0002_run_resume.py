"""runs 表扩展：幂等键、中断状态快照、节点进度。

用于支撑两件事：
1. Idempotency-Key 命中时直接返回原 Run，避免重复执行与重复计费；
2. HITL 审批后从断点继续，跳过已完成的节点。
"""
from __future__ import annotations

from sqlalchemy import inspect, text

VERSION = 2
DESCRIPTION = "runs: idempotency_key / state_json / node_progress"

COLUMNS: dict[str, str] = {
    "idempotency_key": "VARCHAR(120) NOT NULL DEFAULT ''",
    "state_json": "TEXT NOT NULL DEFAULT ''",
    "node_progress": "TEXT NOT NULL DEFAULT ''",
}


def up(conn) -> None:
    existing = {column["name"] for column in inspect(conn).get_columns("runs")}
    for name, ddl in COLUMNS.items():
        if name not in existing:
            conn.execute(text(f"ALTER TABLE runs ADD COLUMN {name} {ddl}"))
    conn.execute(text("CREATE INDEX IF NOT EXISTS ix_runs_idempotency_key ON runs (idempotency_key)"))
