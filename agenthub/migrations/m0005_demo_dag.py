"""升级演示智能体 ag_001：把技能节点与知识库检索加入默认流程。

仅当该智能体仍是旧版示例流程（DAG 中没有「知识库检索」）时更新，
避免覆盖用户自定义的流程。
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import text

VERSION = 5
DESCRIPTION = "demo: ag_001 加入 skill 节点与知识库检索"


def up(conn) -> None:
    row = conn.execute(text("SELECT dag_json, version FROM agents WHERE id = 'ag_001'")).first()
    if not row:
        return
    current = row[0] or ""
    if "知识库检索" in current:
        return

    from ..seed import SAMPLE_DAG

    version = int(row[1] or 1) + 1
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        text("UPDATE agents SET dag_json = :dag, version = :v, status = 'published', "
             "updated_at = :t WHERE id = 'ag_001'"),
        {"dag": json.dumps(SAMPLE_DAG, ensure_ascii=False), "v": version, "t": now},
    )
    conn.execute(
        text("INSERT INTO agent_versions (id, agent_id, version, trigger, gate, note, created_at) "
             "SELECT :id, :aid, :v, :trigger, 'PASS', :note, :t "
             "WHERE NOT EXISTS (SELECT 1 FROM agent_versions WHERE id = :id)"),
        {"id": "av_demo_skill_kb", "aid": "ag_001", "v": version,
         "trigger": "演示升级", "note": f"{len(SAMPLE_DAG['nodes'])} 节点：技能 + 知识库",
         "t": now},
    )
