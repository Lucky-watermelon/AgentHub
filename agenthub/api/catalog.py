"""能力资产接口：智能体、技能、工具。"""
from __future__ import annotations

import json
import time
from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import builtin_tools, sandbox, tools_runtime
from ..dag import structure, validate
from ..db import get_db
from ..gate import validate_dependencies
from ..errors import ApiError, DagInvalid, NotFound, ToolFailed
from ..models import (
    Agent,
    AgentVersion,
    Alias,
    Run,
    Skill,
    SkillVersion,
    Tool,
    ToolCall,
    now,
)
from ..runner import record_tool_call, uid
from ..schemas import (
    AgentCreate,
    AgentUpdate,
    DagImport,
    SandboxRun,
    SkillCreate,
    SkillUpdate,
    ToolCreate,
    ToolInvoke,
    ToolUpdate,
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


def _dag_gate(db: Session, raw: dict) -> tuple[dict, list[str], list[str], list[str], list[str]]:
    """结构校验 + 依赖门禁。结构错误优先，避免在坏图上做无意义的资产查询。"""
    dag, errors, warnings = validate(raw)
    if errors:
        return dag, errors, warnings, [], []
    dep_errors, dep_warnings = validate_dependencies(db, dag)
    return dag, errors, warnings, dep_errors, dep_warnings


def _agent_usage(db: Session) -> list[tuple[set[str], set[str]]]:
    """每个智能体 DAG 引用到的 (工具名, 技能名) 集合。"""
    usage: list[tuple[set[str], set[str]]] = []
    for (dag_json,) in db.query(Agent.dag_json).all():
        try:
            dag = json.loads(dag_json or "{}")
        except json.JSONDecodeError:
            continue
        tools: set[str] = set()
        skills: set[str] = set()
        for n in dag.get("nodes") or []:
            if n.get("tool"):
                tools.add(str(n["tool"]))
            if n.get("type") == "skill":
                name = n.get("skill") or n.get("name")
                if name:
                    skills.add(str(name))
        usage.append((tools, skills))
    return usage


def _tool_refs(db: Session) -> dict[str, int]:
    """工具引用次数 = DAG 节点引用 + 技能绑定，替代写死的 refs 字段。"""
    counts: dict[str, int] = defaultdict(int)
    for tools, _skills in _agent_usage(db):
        for name in tools:
            counts[name] += 1
    for (tools_json,) in db.query(Skill.tools_json).all():
        try:
            tools = json.loads(tools_json or "[]")
        except json.JSONDecodeError:
            continue
        for name in tools:
            counts[str(name)] += 1
    return counts


def _skill_refs(db: Session) -> dict[str, int]:
    """技能被多少个智能体使用：skill 节点显式引用，或绑定工具与 DAG 工具集有交集。"""
    usage = _agent_usage(db)
    counts: dict[str, int] = {}
    for skill in db.query(Skill).all():
        try:
            tools = set(json.loads(skill.tools_json or "[]"))
        except json.JSONDecodeError:
            tools = set()
        counts[skill.id] = sum(
            1 for agent_tools, agent_skills in usage
            if skill.name in agent_skills or (tools and agent_tools & tools)
        )
    return counts


def _check_tool_config(impl: str, endpoint: str, method: str, headers: str,
                       params_schema: str) -> None:
    if params_schema:
        try:
            schema = json.loads(params_schema)
        except json.JSONDecodeError as exc:
            raise ApiError(f"参数 Schema 不是合法 JSON：{exc}", code="AH-TOOL-422",
                           status_code=422)
        if not isinstance(schema, dict):
            raise ApiError("参数 Schema 必须是 JSON 对象", code="AH-TOOL-422", status_code=422)
    if headers:
        try:
            parsed = json.loads(headers)
        except json.JSONDecodeError as exc:
            raise ApiError(f"请求头不是合法 JSON：{exc}", code="AH-TOOL-422", status_code=422)
        if not isinstance(parsed, dict):
            raise ApiError("请求头必须是 JSON 对象", code="AH-TOOL-422", status_code=422)
    impl_lower = (impl or "").lower()
    if impl_lower == "http":
        if not (endpoint or "").strip():
            raise ApiError("HTTP 工具必须填写 endpoint", code="AH-TOOL-422", status_code=422)
        if not endpoint.lower().startswith(("http://", "https://")):
            raise ApiError("endpoint 必须是 http(s) 地址", code="AH-TOOL-422", status_code=422)
        if (method or "POST").upper() not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            raise ApiError(f"不支持的 HTTP 方法：{method}", code="AH-TOOL-422", status_code=422)
    if impl_lower == "jinja2" and not (endpoint or "").strip():
        raise ApiError("Jinja2 工具必须填写模板内容", code="AH-TOOL-422", status_code=422)


# ---------------- 智能体 ----------------
@router.get("/agents")
def list_agents(db: Session = Depends(get_db)):
    rows = db.query(Agent).order_by(Agent.created_at.desc()).all()
    out = []
    for a in rows:
        item = agent_out(a)
        dag, errors, warnings, dep_errors, dep_warnings = _dag_gate(
            db, json.loads(a.dag_json or "{}"))
        item["node_count"] = len(dag["nodes"])
        item["dag_ok"] = not errors and not dep_errors
        item["dag_errors"] = errors + dep_errors
        item["dependency_errors"] = dep_errors
        item["run_count"] = db.query(Run).filter(Run.agent_id == a.id).count()
        out.append(item)
    return out


@router.post("/agents", status_code=201)
def create_agent(body: AgentCreate, db: Session = Depends(get_db)):
    dag, errors, warnings, dep_errors, dep_warnings = _dag_gate(db, body.dag or SAMPLE_DAG)
    if errors or dep_errors:
        raise DagInvalid(errors + dep_errors)
    agent = Agent(id=uid("ag"), name=body.name, description=body.description,
                  version=1, status="draft", dag_json=json.dumps(dag, ensure_ascii=False))
    db.add(agent)
    db.add(AgentVersion(id=uid("av"), agent_id=agent.id, version=1, trigger="新建", gate="PASS"))
    db.commit()
    return agent_out(agent)


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    raw = json.loads(agent.dag_json or "{}")
    dag, errors, warnings, dep_errors, dep_warnings = _dag_gate(db, raw)
    return {"agent": agent_out(agent), "dag": dag, "structure": structure(dag),
            "errors": errors + dep_errors, "warnings": warnings + dep_warnings,
            "dependency_errors": dep_errors}


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
    dag, errors, warnings, dep_errors, dep_warnings = _dag_gate(db, body.dag)
    if errors or dep_errors:
        raise DagInvalid(errors + dep_errors)
    agent.dag_json = json.dumps(dag, ensure_ascii=False)
    agent.updated_at = now()
    if body.publish:
        agent.version += 1
        agent.status = "published"
        db.add(AgentVersion(id=uid("av"), agent_id=agent.id, version=agent.version,
                            trigger="DAG 变更", gate="PASS", note=f"{len(dag['nodes'])} 个节点"))
    db.commit()
    return {"ok": True, "version": agent.version, "status": agent.status,
            "structure": structure(dag), "warnings": warnings + dep_warnings,
            "dependency_errors": dep_errors}


@router.post("/agents/{agent_id}/dag/validate")
def validate_dag(agent_id: str, body: DagImport, db: Session = Depends(get_db)):
    """只校验不落库：结构校验 + 依赖门禁，返回错误、警告与布局预览。"""
    _get_agent(db, agent_id)
    dag, errors, warnings, dep_errors, dep_warnings = _dag_gate(db, body.dag)
    return {"ok": not errors and not dep_errors, "errors": errors + dep_errors,
            "warnings": warnings + dep_warnings,
            "structure": structure(dag) if not errors else None,
            "dependency_errors": dep_errors}


@router.get("/agents/{agent_id}/dag")
def get_dag(agent_id: str, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    dag, errors, warnings, dep_errors, dep_warnings = _dag_gate(
        db, json.loads(agent.dag_json or "{}"))
    return {"dag": dag, "structure": structure(dag), "errors": errors + dep_errors,
            "warnings": warnings + dep_warnings, "dependency_errors": dep_errors}


@router.post("/agents/{agent_id}/publish")
def publish_agent(agent_id: str, db: Session = Depends(get_db)):
    agent = _get_agent(db, agent_id)
    dag, errors, warnings, dep_errors, dep_warnings = _dag_gate(
        db, json.loads(agent.dag_json or "{}"))
    if errors or dep_errors:
        raise DagInvalid(errors + dep_errors)
    agent.version += 1
    agent.status = "published"
    agent.updated_at = now()
    db.add(AgentVersion(id=uid("av"), agent_id=agent.id, version=agent.version,
                        trigger="手动发布", gate="PASS",
                        note="门禁通过（结构 + 依赖）"))
    db.commit()
    return {"ok": True, "version": agent.version, "status": agent.status,
            "warnings": warnings + dep_warnings}


@router.delete("/agents/{agent_id}")
def delete_agent(agent_id: str, db: Session = Depends(get_db)):
    """删除智能体与版本配置；历史运行记录保留，在途 / 待审批运行会拒绝删除。"""
    agent = _get_agent(db, agent_id)
    active = (db.query(Run)
                .filter(Run.agent_id == agent_id,
                        Run.status.in_(("CREATED", "RUNNING", "WAITING_APPROVAL")))
                .count())
    if active:
        raise ApiError(f"仍有 {active} 个在途或待审批运行，处理完后再删除",
                       code="AH-RUNTIME-409", status_code=409)
    kept = db.query(Run).filter(Run.agent_id == agent_id).count()
    db.query(AgentVersion).filter(AgentVersion.agent_id == agent_id).delete()
    db.delete(agent)
    db.commit()
    return {"ok": True, "kept_runs": kept}


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
    refs = _skill_refs(db)
    out = []
    for s in rows:
        item = skill_out(s)
        item["refs"] = refs.get(s.id, 0)
        out.append(item)
    return out


@router.post("/skills", status_code=201)
def create_skill(body: SkillCreate, db: Session = Depends(get_db)):
    scripts = sandbox.normalize_scripts(body.scripts)
    skill = Skill(id=uid("sk"), name=body.name, category=body.category, version=1, status="draft",
                  prompt=body.prompt or "custom@v1", model=body.model,
                  tools_json=json.dumps(body.tools, ensure_ascii=False),
                  content=body.content, scripts_json=json.dumps(scripts, ensure_ascii=False),
                  entrypoint=(body.entrypoint or "").strip(),
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
    if body.name is not None:
        skill.name = body.name
    if body.category is not None:
        skill.category = body.category
    if body.status is not None:
        skill.status = body.status
    if body.prompt is not None:
        skill.prompt = body.prompt
    if body.model is not None:
        skill.model = body.model
    if body.tools is not None:
        skill.tools_json = json.dumps(body.tools, ensure_ascii=False)
    if body.content is not None:
        skill.content = body.content
    if body.scripts is not None:
        scripts = sandbox.normalize_scripts(body.scripts)
        skill.scripts_json = json.dumps(scripts, ensure_ascii=False)
        if skill.entrypoint and not any(s["name"] == skill.entrypoint for s in scripts):
            skill.entrypoint = ""
    if body.entrypoint is not None:
        skill.entrypoint = body.entrypoint.strip()
    if body.timeout_s is not None:
        skill.timeout_s = body.timeout_s
    if body.retry is not None:
        skill.retry = body.retry
    if body.description is not None:
        skill.description = body.description
    skill.updated_at = now()
    db.commit()
    return skill_out(skill)


@router.delete("/skills/{skill_id}")
def delete_skill(skill_id: str, db: Session = Depends(get_db)):
    skill = _get_skill(db, skill_id)
    if skill.status == "published":
        raise ApiError("已发布技能不可删除，请先下线为草稿", code="AH-SKILL-409", status_code=409)
    db.query(SkillVersion).filter(SkillVersion.skill_id == skill_id).delete()
    db.delete(skill)
    db.commit()
    return {"ok": True}


@router.post("/skills/{skill_id}/publish")
def publish_skill(skill_id: str, db: Session = Depends(get_db)):
    skill = _get_skill(db, skill_id)
    tools = json.loads(skill.tools_json or "[]")
    missing = [t for t in tools if not db.query(Tool).filter(Tool.name == t).first()]
    errors = [f"工具未注册：{name}" for name in missing]
    if not db.query(Alias).filter(Alias.alias == skill.model).first():
        errors.append(f"模型别名不存在：{skill.model}")
    if not (skill.content or "").strip():
        errors.append("SKILL.md 内容不能为空")
    try:
        scripts = sandbox.normalize_scripts(json.loads(skill.scripts_json or "[]"))
    except json.JSONDecodeError:
        scripts = []
        errors.append("脚本列表不是合法 JSON")
    if skill.entrypoint and not any(s["name"] == skill.entrypoint for s in scripts):
        errors.append(f"入口脚本不存在：{skill.entrypoint}")
    for script in scripts:
        if script["language"] != "python":
            errors.append(f"暂只支持 Python 脚本：{script['name']}（{script['language']}）")
            continue
        errors.extend(f"脚本 {script['name']}：{e}" for e in sandbox.check_code(script["code"]))
    if errors:
        raise ApiError("；".join(errors), code="AH-SKILL-422", status_code=422)
    skill.version += 1
    skill.status = "published"
    skill.updated_at = now()
    db.add(SkillVersion(id=uid("sv"), skill_id=skill.id, version=skill.version,
                        trigger="手动发布", gate="PASS",
                        changelog="SKILL.md、工具、模型与脚本校验通过"))
    db.commit()
    return {"ok": True, "version": skill.version, "status": skill.status}


@router.post("/skills/{skill_id}/test")
def test_skill_script(skill_id: str, body: SandboxRun, db: Session = Depends(get_db)):
    """在当前技能包中运行一个脚本，用于调试 SKILL.md 里的代码。"""
    skill = _get_skill(db, skill_id)
    try:
        scripts = sandbox.normalize_scripts(json.loads(skill.scripts_json or "[]"))
    except json.JSONDecodeError:
        scripts = []
    wanted = body.script or skill.entrypoint
    if wanted and not any(s["name"] == wanted for s in scripts):
        raise ApiError(f"脚本不存在：{wanted}", code="AH-SKILL-422", status_code=422)
    script = sandbox.pick_script(scripts, skill.entrypoint, body.script or None)
    if not script:
        raise ApiError("该技能没有可执行脚本", code="AH-SKILL-422", status_code=422)
    if script["language"] != "python":
        raise ApiError(f"暂只支持 Python 脚本：{script['language']}",
                       code="AH-SKILL-422", status_code=422)
    context = dict(body.input or {})
    if "payload" not in context and "features" not in context:
        context = {"payload": context}
    result = sandbox.run_python(script["code"], context,
                                body.timeout_s or skill.timeout_s)
    return {"skill": skill.id, "script": script["name"], **result}


@router.get("/skills/{skill_id}/versions")
def list_skill_versions(skill_id: str, db: Session = Depends(get_db)):
    _get_skill(db, skill_id)
    rows = (db.query(SkillVersion).filter(SkillVersion.skill_id == skill_id)
              .order_by(SkillVersion.created_at.desc()).all())
    return [skill_version_out(v) for v in rows]


# ---------------- 工具 ----------------
@router.get("/tools")
def list_tools(source: str | None = None, status: str | None = None, q: str | None = None,
               db: Session = Depends(get_db)):
    query = db.query(Tool)
    if source and source != "全部":
        query = query.filter(Tool.source == source)
    if status and status != "全部":
        query = query.filter(Tool.status == status)
    if q:
        query = query.filter(Tool.name.contains(q))
    refs = _tool_refs(db)
    out = []
    for tool in query.order_by(Tool.created_at.asc()).all():
        item = tool_out(tool)
        item["refs"] = refs.get(tool.name, 0)
        if item["schema"] in ("", "{}") and item["builtin"]:
            item["schema"] = json.dumps(builtin_tools.schema_for(tool.name), ensure_ascii=False)
        out.append(item)
    return out


@router.post("/tools", status_code=201)
def create_tool(body: ToolCreate, db: Session = Depends(get_db)):
    if db.query(Tool).filter(Tool.name == body.name).first():
        raise ApiError(f"工具已存在：{body.name}", code="AH-TOOL-409", status_code=409)
    _check_tool_config(body.impl, body.endpoint, body.method, body.headers, body.params_schema)
    tool = Tool(id=uid("tool"), name=body.name, source=body.source, impl=body.impl,
                permission=body.permission, timeout_s=body.timeout_s,
                idempotency=body.idempotency, status="启用", schema_json=body.params_schema or "{}",
                endpoint=body.endpoint or "", method=(body.method or "POST").upper(),
                headers_json=body.headers or "{}")
    db.add(tool)
    db.commit()
    return tool_out(tool)


@router.patch("/tools/{tool_id}")
def update_tool(tool_id: str, body: ToolUpdate, db: Session = Depends(get_db)):
    tool = db.get(Tool, tool_id)
    if not tool:
        raise NotFound("工具不存在")
    if body.name is not None and body.name != tool.name:
        if tool.source == "内置":
            raise ApiError("内置工具名称不可修改（DAG 按名称引用）", code="AH-TOOL-409",
                           status_code=409)
        if db.query(Tool).filter(Tool.name == body.name).first():
            raise ApiError(f"工具已存在：{body.name}", code="AH-TOOL-409", status_code=409)
        tool.name = body.name
    for field in ("source", "impl", "permission", "status"):
        value = getattr(body, field)
        if value is not None:
            setattr(tool, field, value)
    if body.timeout_s is not None:
        tool.timeout_s = body.timeout_s
    if body.idempotency is not None:
        tool.idempotency = body.idempotency
    if body.params_schema is not None:
        tool.schema_json = body.params_schema
    if body.endpoint is not None:
        tool.endpoint = body.endpoint
    if body.method is not None:
        tool.method = (body.method or "POST").upper()
    if body.headers is not None:
        tool.headers_json = body.headers
    # 只改状态 / 名称时不重新校验历史配置，避免旧数据无法停用或改名
    if any(v is not None for v in (body.impl, body.endpoint, body.method,
                                   body.headers, body.params_schema)):
        _check_tool_config(tool.impl, tool.endpoint, tool.method, tool.headers_json, tool.schema_json)
    db.commit()
    return tool_out(tool)


@router.delete("/tools/{tool_id}")
def delete_tool(tool_id: str, db: Session = Depends(get_db)):
    tool = db.get(Tool, tool_id)
    if not tool:
        raise NotFound("工具不存在")
    if tool.source == "内置":
        raise ApiError("内置工具不可删除，可改为停用", code="AH-TOOL-409", status_code=409)
    refs = _tool_refs(db).get(tool.name, 0)
    if refs:
        raise ApiError(f"工具仍被 {refs} 处引用，先解除绑定再删除", code="AH-TOOL-409",
                       status_code=409)
    db.delete(tool)
    db.commit()
    return {"ok": True}


@router.post("/tools/{tool_id}/invoke")
def invoke_tool(tool_id: str, body: ToolInvoke, db: Session = Depends(get_db)):
    tool = db.get(Tool, tool_id)
    if not tool:
        raise NotFound("工具不存在")
    if tool.status != "启用":
        raise ApiError(f"工具已停用：{tool.name}", code="AH-TOOL-409", status_code=409)
    ctx = {"customer_no": body.params.get("customer_no", ""), "features": {}, "db": db}
    started = time.perf_counter()
    try:
        out = tools_runtime.invoke_by_name(db, tool.name, body.params, ctx)
    except ToolFailed as exc:
        dur = int((time.perf_counter() - started) * 1000)
        db.add(ToolCall(id=uid("tc"), tool_name=tool.name,
                        params=json.dumps(body.params, ensure_ascii=False),
                        status="失败", dur_ms=dur, error=exc.message))
        db.commit()
        raise
    except ApiError as exc:
        dur = int((time.perf_counter() - started) * 1000)
        db.add(ToolCall(id=uid("tc"), tool_name=tool.name,
                        params=json.dumps(body.params, ensure_ascii=False),
                        status="失败", dur_ms=dur, error=exc.message))
        db.commit()
        raise
    except KeyError as exc:
        dur = int((time.perf_counter() - started) * 1000)
        db.add(ToolCall(id=uid("tc"), tool_name=tool.name,
                        params=json.dumps(body.params, ensure_ascii=False),
                        status="失败", dur_ms=dur, error=str(exc)))
        db.commit()
        raise ApiError(f"该工具没有可用实现：{exc}", code="AH-TOOL-501", status_code=501)
    dur = int((time.perf_counter() - started) * 1000)
    db.add(ToolCall(id=uid("tc"), tool_name=tool.name,
                    params=json.dumps(body.params, ensure_ascii=False),
                    status="成功", dur_ms=dur, error=""))
    db.commit()
    return {"tool": tool.name, "status": "成功", "dur_ms": dur, "result": out}


@router.get("/tools/calls")
def list_tool_calls(limit: int = 100, offset: int = 0, tool: str | None = None,
                    status: str | None = None, run_id: str | None = None,
                    trace: str | None = None, db: Session = Depends(get_db)):
    query = db.query(ToolCall)
    if tool:
        query = query.filter(ToolCall.tool_name == tool)
    if status and status != "全部":
        query = query.filter(ToolCall.status == status)
    if run_id:
        query = query.filter(ToolCall.run_id == run_id)
    if trace:
        query = query.filter(ToolCall.trace_id == trace)
    rows = (query.order_by(ToolCall.created_at.desc())
              .offset(max(0, offset)).limit(max(1, min(500, limit))).all())
    return [tool_call_out(c) for c in rows]
