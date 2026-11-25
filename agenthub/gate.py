"""DAG 依赖门禁：在结构校验之外，校验节点引用的资产是否真实可用。

结构校验（``dag.validate``）只看图本身；这里补上对数据库资产的检查：

- ``tool`` 节点：工具必须已注册且启用；
- ``llm`` 节点：模型别名必须存在，且其 Provider 未停用；
- ``skill`` 节点：技能必须已发布，其模型别名与绑定工具同样可用；
- ``知识库检索`` 工具：知识库为空时给 warning（不阻断运行）。

返回 ``(errors, warnings)``。errors 非空时，保存 / 发布 / 运行都应拒绝。
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from . import builtin_tools
from .config import settings
from .models import Alias, KbDoc, Provider, Skill, Tool

KNOWLEDGE_TOOL = "知识库检索"


def _label(node: dict) -> str:
    return str(node.get("id") or node.get("name") or "(未命名)")


def _check_tool(db: Session, name: str, node_id: str,
                errors: list[str], warnings: list[str]) -> None:
    name = str(name or "").strip()
    if not name:
        errors.append(f"工具节点缺少 tool 名称（节点 {node_id}）")
        return
    row = db.query(Tool).filter(Tool.name == name).first()
    if row is None:
        if name in builtin_tools.TOOL_REGISTRY:
            # 旧库尚未完成启动补齐时仍然允许，避免误伤
            warnings.append(f"内置工具未登记（重启后自动补齐）：{name}（节点 {node_id}）")
        else:
            errors.append(f"工具不存在：{name}（节点 {node_id}）")
            return
    elif row.status != "启用":
        errors.append(f"工具已停用：{name}（节点 {node_id}）")
        return

    if name == KNOWLEDGE_TOOL and db.query(KbDoc).count() == 0:
        warnings.append("知识库暂无文档：知识库检索会返回空结果")


def _check_alias(db: Session, name: str, node_id: str,
                 errors: list[str], warnings: list[str]) -> None:
    name = str(name or "").strip()
    if not name:
        errors.append(f"LLM / 技能节点缺少模型别名（节点 {node_id}）")
        return
    alias = db.query(Alias).filter(Alias.alias == name).first()
    if not alias:
        errors.append(f"模型别名不存在：{name}（节点 {node_id}）")
        return
    provider = db.query(Provider).filter(Provider.name == alias.provider_name).first()
    if not provider:
        errors.append(f"别名的 Provider 不存在：{name} -> {alias.provider_name}")
    elif provider.status == "stopped":
        errors.append(f"Provider 已停用：{provider.name}（别名 {name}，节点 {node_id}）")


def _skill_tools(skill: Skill) -> list[str]:
    try:
        value = json.loads(skill.tools_json or "[]")
    except json.JSONDecodeError:
        return []
    return [str(t) for t in value] if isinstance(value, list) else []


def _check_skill(db: Session, name: str, node: dict,
                 errors: list[str], warnings: list[str]) -> None:
    node_id = _label(node)
    name = str(name or "").strip()
    if not name:
        errors.append(f"技能节点缺少 skill 名称（节点 {node_id}）")
        return
    skill = db.query(Skill).filter(Skill.name == name).first()
    if not skill:
        errors.append(f"技能不存在：{name}（节点 {node_id}）")
        return
    if skill.status != "published":
        errors.append(f"技能未发布：{name}（当前 {skill.status}，节点 {node_id}）")
    _check_alias(db, node.get("model") or skill.model, node_id, errors, warnings)
    for tool_name in _skill_tools(skill):
        _check_tool(db, tool_name, node_id, errors, warnings)


def validate_dependencies(db: Session, dag: dict) -> tuple[list[str], list[str]]:
    """校验 DAG 引用的工具、模型别名、技能与知识库是否可用。"""
    errors: list[str] = []
    warnings: list[str] = []
    for node in (dag or {}).get("nodes") or []:
        if not isinstance(node, dict):
            continue
        ntype = node.get("type") or "tool"
        node_id = _label(node)
        if ntype == "tool":
            _check_tool(db, node.get("tool") or node.get("name"), node_id, errors, warnings)
        elif ntype == "llm":
            _check_alias(db, node.get("model") or settings.default_model,
                         node_id, errors, warnings)
        elif ntype == "skill":
            _check_skill(db, node.get("skill") or node.get("name"), node, errors, warnings)
    # 去重，保持首次出现的顺序
    return list(dict.fromkeys(errors)), list(dict.fromkeys(warnings))
