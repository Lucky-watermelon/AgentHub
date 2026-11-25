"""skills 表扩展：SKILL.md 内容、脚本包与入口脚本。

对齐 Agent Skills 的文件组织方式：
- ``content``       SKILL.md 全文（frontmatter + 说明书）
- ``scripts_json``  随技能分发的脚本 [{name, language, code}]
- ``entrypoint``    默认执行的脚本名
"""
from __future__ import annotations

from sqlalchemy import inspect, text

VERSION = 4
DESCRIPTION = "skills: content / scripts_json / entrypoint + demo content"

COLUMNS: dict[str, str] = {
    "content": "TEXT NOT NULL DEFAULT ''",
    "scripts_json": "TEXT NOT NULL DEFAULT '[]'",
    "entrypoint": "VARCHAR(120) NOT NULL DEFAULT ''",
}


def up(conn) -> None:
    existing = {column["name"] for column in inspect(conn).get_columns("skills")}
    for name, ddl in COLUMNS.items():
        if name not in existing:
            conn.execute(text(f"ALTER TABLE skills ADD COLUMN {name} {ddl}"))

    # 旧库补示例内容（仅填空，不覆盖用户已编辑的技能）
    from .. import skill_templates

    for skill_id, content in skill_templates.SKILL_CONTENTS.items():
        conn.execute(text(
            "UPDATE skills SET content = :c WHERE id = :id AND (content IS NULL OR content = '')"
        ), {"c": content, "id": skill_id})
    for skill_id, scripts in skill_templates.SKILL_SCRIPTS.items():
        conn.execute(text(
            "UPDATE skills SET scripts_json = :s WHERE id = :id "
            "AND (scripts_json IS NULL OR scripts_json = '' OR scripts_json = '[]')"
        ), {"s": skill_templates.scripts_json(skill_id), "id": skill_id})
    for skill_id, entrypoint in skill_templates.SKILL_ENTRYPOINTS.items():
        conn.execute(text(
            "UPDATE skills SET entrypoint = :e WHERE id = :id "
            "AND (entrypoint IS NULL OR entrypoint = '')"
        ), {"e": entrypoint, "id": skill_id})
