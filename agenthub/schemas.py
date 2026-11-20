"""请求模型与序列化工具。响应体统一用 dict，序列化集中在这里。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .models import (
    Agent,
    AgentVersion,
    Alias,
    AppKey,
    Checkpoint,
    KbChunk,
    KbDoc,
    Provider,
    Run,
    RunEvent,
    Skill,
    SkillVersion,
    Tool,
    ToolCall,
)


def dt(value) -> str | None:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value else None


# ---------------- 请求体 ----------------
class AgentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    dag: dict | None = None


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None


class DagImport(BaseModel):
    dag: dict
    publish: bool = False


class SkillCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = "业务"
    prompt: str = ""
    model: str = "default-chat"
    tools: list[str] = []
    timeout_s: int = 30
    retry: int = 1
    description: str = ""


class SkillUpdate(BaseModel):
    prompt: str | None = None
    model: str | None = None
    tools: list[str] | None = None
    timeout_s: int | None = None
    retry: int | None = None
    description: str | None = None


class ToolCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    source: str = "内置"
    impl: str = "Python"
    permission: str = "read"
    timeout_s: int = 10
    idempotency: bool = False
    params_schema: str = "{}"


class ToolInvoke(BaseModel):
    params: dict[str, Any] = {}


class KbDocCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: str = "口径"
    note: str = ""
    content: str = ""


class RetrieveRequest(BaseModel):
    q: str = Field(min_length=1)
    top_k: int = 4


class ProviderCreate(BaseModel):
    name: str
    kind: str = "本地"
    base_url: str = ""
    models: str = ""
    caps: str = "聊天 / 流式"
    api_key: str = ""


class AliasCreate(BaseModel):
    alias: str
    provider_name: str = "rule"
    model: str = ""
    caps: str = "聊天 / 流式"


class RoutingUpdate(BaseModel):
    chain: list[str]


class RunCreate(BaseModel):
    agent_id: str
    customer_no: str = ""
    payload: dict[str, Any] = {}
    async_mode: bool = False
    idempotency_key: str = ""


class InvokeRequest(BaseModel):
    """对外调用入参：报文可直接放在 payload，也允许扁平传入 credit/finance/judicial。"""

    customer_no: str = ""
    payload: dict[str, Any] = {}
    mode: str = "sync"  # sync / async
    idempotency_key: str = ""
    credit: dict[str, Any] | None = None
    finance: dict[str, Any] | None = None
    judicial: dict[str, Any] | None = None


class CompatMessage(BaseModel):
    role: str = "user"
    content: str = ""


class CompatChatRequest(BaseModel):
    """OpenAI 兼容调用体：model 填智能体 id 或名称。"""

    model: str
    messages: list[CompatMessage] = []
    stream: bool = False
    temperature: float | None = None
    user: str | None = None


class ApproveRequest(BaseModel):
    decision: str = "APPROVED"  # APPROVED / REJECTED
    comment: str = ""


class KeyCreate(BaseModel):
    app: str
    type: str = "AppKey"
    days: int = 365


class QuotaUpdate(BaseModel):
    rps: int | None = None
    concurrency: int | None = None
    daily_calls: int | None = None
    enabled: bool | None = None


class LangfuseUpdate(BaseModel):
    enabled: bool | None = None
    sampling: float | None = None
    host: str | None = None


# ---------------- 序列化 ----------------
def agent_out(a: Agent) -> dict:
    return {
        "id": a.id, "name": a.name, "description": a.description, "version": a.version,
        "status": a.status, "updated_at": dt(a.updated_at), "created_at": dt(a.created_at),
    }


def agent_version_out(v: AgentVersion) -> dict:
    return {"id": v.id, "agent_id": v.agent_id, "version": v.version, "trigger": v.trigger,
            "gate": v.gate, "note": v.note, "time": dt(v.created_at)}


def skill_out(s: Skill, tools: list[str] | None = None) -> dict:
    import json

    return {
        "id": s.id, "name": s.name, "category": s.category, "version": s.version,
        "status": s.status, "prompt": s.prompt, "model": s.model,
        "tools": tools if tools is not None else json.loads(s.tools_json or "[]"),
        "timeout_s": s.timeout_s, "retry": s.retry, "refs": s.refs,
        "description": s.description, "updated_at": dt(s.updated_at),
    }


def skill_version_out(v: SkillVersion) -> dict:
    return {"id": v.id, "skill_id": v.skill_id, "version": v.version, "trigger": v.trigger,
            "gate": v.gate, "changelog": v.changelog, "time": dt(v.created_at)}


def tool_out(t: Tool) -> dict:
    return {"id": t.id, "name": t.name, "source": t.source, "impl": t.impl,
            "permission": t.permission, "timeout_s": t.timeout_s, "idempotency": t.idempotency,
            "status": t.status, "schema": t.schema_json, "refs": t.refs}


def tool_call_out(c: ToolCall) -> dict:
    return {"id": c.id, "tool": c.tool_name, "trace": c.trace_id, "run_id": c.run_id,
            "params": c.params, "status": c.status, "dur_ms": c.dur_ms,
            "error": c.error, "time": dt(c.created_at)}


def run_out(r: Run) -> dict:
    import json

    return {
        "id": r.id, "agent_id": r.agent_id, "agent": r.agent_name, "v": r.agent_version,
        "customer": r.customer_no, "status": r.status, "trace_id": r.trace_id,
        "thread_id": r.thread_id, "idempotency_key": r.idempotency_key or "",
        "features": json.loads(r.features_json or "{}"),
        "output": json.loads(r.output_json or "{}"), "report_path": r.report_path,
        "tokens": r.tokens, "amount": r.amount, "dur_ms": r.dur_ms, "error": r.error,
        "start": dt(r.created_at), "finished": dt(r.finished_at),
    }


def event_out(e: RunEvent) -> dict:
    import json

    return {"seq": e.seq, "event": e.event, "data": json.loads(e.data or "{}"),
            "time": dt(e.created_at)}


def checkpoint_out(c: Checkpoint) -> dict:
    return {"id": c.id, "step": c.step, "node": c.node, "at": dt(c.created_at)}


def kb_doc_out(d: KbDoc) -> dict:
    return {"id": d.id, "name": d.name, "kind": d.kind, "status": d.status,
            "chunks": d.chunks, "note": d.note,
            "updated": dt(d.updated_at), "created": dt(d.created_at)}


def kb_chunk_out(c: KbChunk) -> dict:
    return {"id": c.id, "doc": c.doc_id, "idx": c.idx, "text": c.text}


def provider_out(p: Provider) -> dict:
    return {"id": p.id, "name": p.name, "kind": p.kind, "url": p.base_url,
            "models": p.models, "caps": p.caps, "status": p.status,
            "last": dt(p.updated_at)}


def alias_out(a: Alias) -> dict:
    return {"id": a.id, "alias": a.alias, "provider": a.provider_name, "model": a.model,
            "caps": a.caps, "status": a.status}


def key_out(k: AppKey) -> dict:
    return {"id": k.id, "app": k.app, "type": k.type, "prefix": k.prefix,
            "status": k.status, "created": dt(k.created_at), "expires": k.expires_at}
