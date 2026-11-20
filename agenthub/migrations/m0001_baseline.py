"""基线迁移：标记初始结构，不改变任何表。

已有数据库在引入迁移机制时会先应用这一条，作为版本 1 的起点。
"""
from __future__ import annotations

VERSION = 1
DESCRIPTION = "baseline: initial schema (agents/skills/tools/runs/kb/providers/keys)"


def up(conn) -> None:  # noqa: ARG001  基线只做登记
    return None
