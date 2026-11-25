"""tools 表扩展：HTTP 工具调用地址、方法与请求头。

在此之前，控制台可以注册 HTTP / Jinja2 工具，但运行时没有实现，
调用统一返回 AH-TOOL-501。补上这三列后，注册的 HTTP 工具可以直接被
DAG 节点与控制台「调用测试」使用。
"""
from __future__ import annotations

from sqlalchemy import inspect, text

VERSION = 3
DESCRIPTION = "tools: endpoint / method / headers_json"

COLUMNS: dict[str, str] = {
    "endpoint": "VARCHAR(500) NOT NULL DEFAULT ''",
    "method": "VARCHAR(10) NOT NULL DEFAULT 'POST'",
    "headers_json": "TEXT NOT NULL DEFAULT '{}'",
}


def up(conn) -> None:
    existing = {column["name"] for column in inspect(conn).get_columns("tools")}
    for name, ddl in COLUMNS.items():
        if name not in existing:
            conn.execute(text(f"ALTER TABLE tools ADD COLUMN {name} {ddl}"))
