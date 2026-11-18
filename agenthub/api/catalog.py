"""能力资产接口：智能体、技能、工具。"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import builtin_tools
from ..dag import normalize, structure, validate
from ..db import get_db
from ..errors import ApiError, DagInvalid, NotFound
from ..models import Agent, AgentVersion, Run, Skill, SkillVersion, Tool, ToolCall, now
from ..runner import record_tool_call, uid
from ..schemas import (
    AgentCreate,
    AgentUpdate,
    DagImport,
    SkillCreate,
    SkillUpdate,
    ToolCreate,
    ToolInvoke,
    agent_out,
    agent_version_out,
    skill_out,
    skill_version_out,
    tool_call_out,
    tool_out,
)
from ..seed import SAMPLE_DAG

router = APIRouter()


def _get_agent(db: Session, agent_id: str) -> Agent:
    agent = db.get(Agent, agent_id)
    if not agent:
        raise NotFound(f"智能体不存在：{agent_id}")
    return agent


def _get_skill(db: Session, skill_id: str) -> Skill:
    skill = db.get(Skill, skill_id)
    if not skill:
        raise NotFound(f"技能不存在：{skill_id}")
    return skill


# ---------------- 智能体 ----------------
@router.get("/agents")
def list_agents(db: Session = Depends(get_db)):
    rows = db.query(Agent).order_by(Agent.created_at.desc()).all()
    out = []
    for a in rows:
        item = agent_out(a)
        dag, errors, warnings = validate(json.loads(a.dag_json or "{}"))
        item["node_count"] = len(dag["nodes"])
        item["dag_ok"] = not errors
        item["dag_errors"] = errors
        item["run_count"] = db.query(Run).filter(Run.agent_id == a.id).count()
        out.append(item)
    return out


@router.post("/agents", status_code=201)
def create_agent(body: AgentCreate, db: Session = Depends(get_db)):
    dag = body.dag or SAMPLE_DAG
    _, errors, _ = validate(dag)
    if errors:
        raise DagInvalid(errors)
    agent = Agent(id=uid("ag"), name=body.name, description=body.description,
                  version=1, status="draft", dag_json=json.dumps(normalize(dag), ensure_ascii=False))
    db.add(agent)
    db.add(AgentVersion(id=uid("av"), agent_id=agent.id, version=1, trigger="新建", gate="PASS"))
    db.commit()
    return agent_out(agent)


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    raw = json.loads(agent.dag_json or "{}")
    dag, errors, warnings = validate(raw)
    return {"agent": agent_out(agent), "dag": dag, "structure": structure(dag),
            "errors": errors, "warnings": warnings}


@router.patch("/agents/{agent_id}")
def update_agent(agent_id: str, body: AgentUpdate, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    for field in ("name", "description", "status"):
        value = getattr(body, field)
        if value is not None:
            setattr(agent, field, value)
    agent.updated_at = now()
    db.commit()
    return agent_out(agent)


@router.post("/agents/{agent_id}/dag")
def import_dag(agent_id: str, body: DagImport, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    dag, errors, warnings = validate(body.dag)
    if errors:
        raise DagInvalid(errors)
    agent.dag_json = json.dumps(dag, ensure_ascii=False)
    agent.updated_at = now()
    if body.publish:
        agent.version += 1
        agent.status = "published"
        db.add(AgentVersion(id=uid("av"), agent_id=agent.id, version=agent.version,
                            trigger="DAG 变更", gate="PASS", note=f"{len(dag['nodes'])} 个节点"))
    db.commit()
    return {"ok": True, "version": agent.version, "status": agent.status,
            "structure": structure(dag), "warnings": warnings}


@router.post("/agents/{agent_id}/dag/validate")
def validate_dag(agent_id: str, body: DagImport, db: Session = Depends(get_db)):
    """只校验不落库：返回错误、警告与布局预览。"""
    _get_agent(db, agent_id)
    dag, errors, warnings = validate(body.dag)
    return {"ok": not errors, "errors": errors, "warnings": warnings,
            "structure": structure(dag) if not errors else None}


@router.get("/agents/{agent_id}/dag")
def get_dag(agent_id: str, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    dag, errors, warnings = validate(json.loads(agent.dag_json or "{}"))
    return {"dag": dag, "structure": structure(dag), "errors": errors, "warnings": warnings}


@router.post("/agents/{agent_id}/publish")
def publish_agent(agent_id: str, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    dag, errors, _ = validate(json.loads(agent.dag_json or "{}"))
    if errors:
        raise DagInvalid(errors)
    agent.version += 1
    agent.status = "published"
    agent.updated_at = now()
    db.add(AgentVersion(id=uid("av"), agent_id=agent.id, version=agent.version,
                        trigger="手动发布", gate="PASS", note="门禁通过（本轮为最小实现）"))
    db.commit()
    return {"ok": True, "version": agent.version, "status": agent.status}


@router.get("/agents/{agent_id}/versions")
def list_agent_versions(agent_id: str, db: Session = Depends(get_db)):
    _get_agent(db, agent_id)
    rows = (db.query(AgentVersion).filter(AgentVersion.agent_id == agent_id)
              .order_by(AgentVersion.created_at.desc()).all())
    return [agent_version_out(v) for v in rows]


@router.get("/agents/{agent_id}/runs")
def list_agent_runs(agent_id: str, limit: int = 20, db: Session = Depends(get_db)):
    _get_agent(db, agent_id)
    rows = (db.query(Run).filter(Run.agent_id == agent_id)
              .order_by(Run.created_at.desc()).limit(limit).all())
    from ..schemas import run_out

    return [run_out(r) for r in rows]


# ---------------- 技能 ----------------
@router.get("/skills")
def list_skills(db: Session = Depends(get_db)):
    rows = db.query(Skill).order_by(Skill.created_at.desc()).all()
    return [skill_out(s) for s in rows]


@router.post("/skills", status_code=201)
def create_skill(body: SkillCreate, db: Session = Depends(get_db)):
    skill = Skill(id=uid("sk"), name=body.name, category=body.category, version=1, status="draft",
                  prompt=body.prompt or "custom@v1", model=body.model,
                  tools_json=json.dumps(body.tools, ensure_ascii=False),
                  timeout_s=body.timeout_s, retry=body.retry, description=body.description)
    db.add(skill)
    db.add(SkillVersion(id=uid("sv"), skill_id=skill.id, version=1, trigger="新建",
                        gate="PENDING", changelog="初始草稿"))
    db.commit()
    return skill_out(skill)


@router.get("/skills/{skill_id}")
def get_skill(skill_id: str, db: Session = Depends(get_db)):
    skill = _get_skill(db, skill_id)
    return skill_out(skill)


@router.patch("/skills/{skill_id}")
def update_skill(skill_id: str, body: SkillUpdate, db: Session = Depends(get_db)):
    skill = _get_skill(db, skill_id)
    if body.prompt is not None:
        skill.prompt = body.prompt
    if body.model is not None:
        skill.model = body.model
    if body.tools is not None:
        skill.tools_json = json.dumps(body.tools, ensure_ascii=False)
    if body.timeout_s is not None:
        skill.timeout_s = body.timeout_s
    if body.retry is not None:
        skill.retry = body.retry
    if body.description is not None:
        skill.description = body.description
    skill.updated_at = now()
    db.commit()
    return skill_out(skill)


@router.post("/skills/{skill_id}/publish")
def publish_skill(skill_id: str, db: Session = Depends(get_db)):
    skill = _get_skill(db, skill_id)
    tools = json.loads(skill.tools_json or "[]")
    missing = [t for t in tools if not db.query(Tool).filter(Tool.name == t).first()]
    if missing:
        raise ApiError("工具未注册：" + "、".join(missing), code="AH-SKILL-422", status_code=422)
    skill.version += 1
    skill.status = "published"
    skill.updated_at = now()
    db.add(SkillVersion(id=uid("sv"), skill_id=skill.id, version=skill.version,
                        trigger="手动发布", gate="PASS", changelog="工具与模型绑定校验通过"))
    db.commit()
    return {"ok": True, "version": skill.version, "status": skill.status}


@router.get("/skills/{skill_id}/versions")
def list_skill_versions(skill_id: str, db: Session = Depends(get_db)):
    _get_skill(db, skill_id)
    rows = (db.query(SkillVersion).filter(SkillVersion.skill_id == skill_id)
              .order_by(SkillVersion.created_at.desc()).all())
    return [skill_version_out(v) for v in rows]


# ---------------- 工具 ----------------
@router.get("/tools")
def list_tools(source: str | None = None, db: Session = Depends(get_db)):
    query = db.query(Tool)
    if source and source != "全部":
        query = query.filter(Tool.source == source)
    return [tool_out(t) for t in query.order_by(Tool.created_at.asc()).all()]


@router.post("/tools", status_code=201)
def create_tool(body: ToolCreate, db: Session = Depends(get_db)):
    if db.query(Tool).filter(Tool.name == body.name).first():
        raise ApiError(f"工具已存在：{body.name}", code="AH-TOOL-409", status_code=409)
    try:
        json.loads(body.params_schema or "{}")
    except json.JSONDecodeError as exc:
        raise ApiError(f"参数 Schema 不是合法 JSON：{exc}", code="AH-TOOL-422", status_code=422)
    tool = Tool(id=uid("tool"), name=body.name, source=body.source, impl=body.impl,
                permission=body.permission, timeout_s=body.timeout_s,
                idempotency=body.idempotency, status="启用", schema_json=body.params_schema)
    db.add(tool)
    db.commit()
    return tool_out(tool)


@router.patch("/tools/{tool_id}")
def update_tool(tool_id: str, payload: dict, db: Session = Depends(get_db)):
    tool = db.get(Tool, tool_id)
    if not tool:
        raise NotFound("工具不存在")
    if "status" in payload:
        tool.status = payload["status"]
    db.commit()
    return tool_out(tool)


@router.post("/tools/{tool_id}/invoke")
def invoke_tool(tool_id: str, body: ToolInvoke, db: Session = Depends(get_db)):
    tool = db.get(Tool, tool_id)
    if not tool:
        raise NotFound("工具不存在")
    if tool.status != "启用":
        raise ApiError(f"工具已停用：{tool.name}", code="AH-TOOL-409", status_code=409)
    started = time.perf_counter()
    try:
        out = builtin_tools.invoke(tool.name, body.params, {"customer_no": body.params.get("customer_no", ""),
                                                            "features": {}})
        status, error = "成功", ""
    except KeyError as exc:
        dur = int((time.perf_counter() - started) * 1000)
        db.add(ToolCall(id=uid("tc"), tool_name=tool.name, params=json.dumps(body.params, ensure_ascii=False),
                        status="失败", dur_ms=dur, error=str(exc)))
        db.commit()
        raise ApiError(f"该工具没有内置实现：{exc}", code="AH-TOOL-501", status_code=501)
    dur = int((time.perf_counter() - started) * 1000)
    db.add(ToolCall(id=uid("tc"), tool_name=tool.name, params=json.dumps(body.params, ensure_ascii=False),
                    status=status, dur_ms=dur, error=error))
    db.commit()
    return {"tool": tool.name, "status": status, "dur_ms": dur, "result": out}


@router.get("/tools/calls")
def list_tool_calls(limit: int = 100, db: Session = Depends(get_db)):
    rows = db.query(ToolCall).order_by(ToolCall.created_at.desc()).limit(limit).all()
    return [tool_call_out(c) for c in rows]
