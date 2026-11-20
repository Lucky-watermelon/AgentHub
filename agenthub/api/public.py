"""对外调用接口：业务系统嵌入 AgentHub 的统一入口。

两类契约：
1. 原生调用 ``POST /api/v1/invoke/{agent}``：报文直传，同步拿到结论与报告地址，
   或 async 模式拿 run_id 后再订阅事件。
2. OpenAI 兼容 ``POST /v1/chat/completions``：已有 OpenAI SDK 的业务侧只改 base_url
   即可调用智能体（model 填智能体 id 或名称）。
"""
from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .. import runner as runner_mod
from ..auth import key_label, optional_key
from ..db import get_db
from ..errors import ApiError, NotFound
from ..models import Agent, AppKey, Run
from ..runner import emit
from ..schemas import CompatChatRequest, InvokeRequest, run_out

router = APIRouter()
compat = APIRouter()


def _resolve_agent(db: Session, ident: str) -> Agent:
    agent = db.get(Agent, ident)
    if not agent:
        agent = db.query(Agent).filter(Agent.name == ident).first()
    if not agent:
        raise NotFound(f"智能体不存在：{ident}")
    return agent


def _payload_of(body: InvokeRequest) -> dict:
    payload = dict(body.payload or {})
    for field in ("credit", "finance", "judicial"):
        value = getattr(body, field)
        if value:
            payload[field] = value
    return payload


def _report_url(request: Request, run: Run) -> str:
    if not run.report_path:
        return ""
    return str(request.base_url).rstrip("/") + "/" + run.report_path.lstrip("/")


def _run_links(request: Request, run: Run) -> dict:
    base = str(request.base_url).rstrip("/")
    return {
        "self": f"{base}/api/v1/runs/{run.id}",
        "events": f"{base}/api/v1/runs/{run.id}/events",
        "report": _report_url(request, run),
    }


@router.get("/invoke/agents")
def callable_agents(db: Session = Depends(get_db)):
    """可被外部调用的智能体清单（含 DAG 是否就绪）。"""
    from ..dag import validate

    out = []
    for agent in db.query(Agent).order_by(Agent.created_at.desc()).all():
        _, errors, _ = validate(json.loads(agent.dag_json or "{}"))
        out.append({"id": agent.id, "name": agent.name, "version": agent.version,
                    "status": agent.status, "callable": not errors,
                    "endpoint": f"/api/v1/invoke/{agent.id}",
                    "model_alias": agent.id,
                    "error": errors[0] if errors else ""})
    return out


@router.get("/invoke/{agent_id}/info")
def invoke_info(agent_id: str, request: Request, db: Session = Depends(get_db)):
    agent = _resolve_agent(db, agent_id)
    base = str(request.base_url).rstrip("/")
    return {
        "agent": {"id": agent.id, "name": agent.name, "version": agent.version},
        "endpoint": f"{base}/api/v1/invoke/{agent.id}",
        "compat": {"endpoint": f"{base}/v1/chat/completions", "model": agent.id},
        "auth": "Authorization: Bearer <AppKey>（也可用 X-API-Key 或 ?access_token=）",
        "request_schema": {
            "customer_no": "客户编号，用于报告命名",
            "payload": {"credit": "征信报文", "finance": "财报报文", "judicial": "司法报文"},
            "mode": "sync（默认，直接返回结论） / async（返回 run_id）",
        },
        "response_schema": {
            "run_id": "运行 id", "status": "COMPLETED / WAITING_APPROVAL / FAILED",
            "summary": "模型结论", "features": "结构化特征",
            "report_url": "HTML 报告地址", "usage": "tokens / 耗时 / trace_id",
        },
        "recent_calls": [
            {"run_id": r.id, "status": r.status, "customer": r.customer_no,
             "start": r.created_at.strftime("%Y-%m-%d %H:%M:%S")}
            for r in (db.query(Run).filter(Run.agent_id == agent.id)
                        .order_by(Run.created_at.desc()).limit(5).all())
        ],
    }


@router.post("/invoke/{agent_id}")
def invoke(agent_id: str, body: InvokeRequest, request: Request,
           db: Session = Depends(get_db), key: AppKey | None = Depends(optional_key)):
    agent = _resolve_agent(db, agent_id)
    payload = _payload_of(body)
    if not payload:
        raise ApiError("payload 不能为空：至少提供 credit / finance / judicial 之一",
                       code="AH-INVOKE-422", status_code=422)
    if not any(k in payload for k in ("credit", "finance", "judicial", "text")):
        raise ApiError("payload 需包含 credit / finance / judicial 至少一段报文",
                       code="AH-INVOKE-422", status_code=422)

    async_mode = str(body.mode).lower() == "async"
    started = time.perf_counter()
    run = runner_mod.create_run(db, agent, payload,
                               body.customer_no or payload.get("customer_no", ""),
                               async_mode=async_mode)
    emit(db, run.id, "api.invoked", {"caller": key_label(key),
                                     "endpoint": f"/api/v1/invoke/{agent.id}",
                                     "mode": "async" if async_mode else "sync"})

    data = run_out(run)
    result = {
        "run_id": run.id,
        "status": run.status,
        "agent": {"id": agent.id, "name": agent.name, "version": agent.version},
        "customer_no": run.customer_no,
        "summary": data["output"].get("summary", ""),
        "features": data["features"],
        "report_url": _report_url(request, run) if run.status == "COMPLETED" else "",
        "usage": {"tokens": run.tokens, "amount": run.amount, "dur_ms": run.dur_ms,
                  "trace_id": run.trace_id},
        "links": _run_links(request, run),
        "caller": key_label(key),
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
    }
    if async_mode:
        result["hint"] = "运行已提交，订阅 links.events（SSE）或轮询 links.self 获取结果"
    return result


# ---------------- OpenAI 兼容契约 ----------------
def _extract_payload(content: str) -> dict:
    """尝试把 messages 内容解析为报文 JSON；不合法时退化为纯文本输入。"""
    text = (content or "").strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and any(k in parsed for k in ("credit", "finance", "judicial", "payload")):
                if "payload" in parsed and isinstance(parsed["payload"], dict):
                    return parsed["payload"]
                return parsed
        except json.JSONDecodeError:
            pass
    return {"text": text}


@compat.get("/models")
def compat_models(db: Session = Depends(get_db)):
    agents = db.query(Agent).order_by(Agent.created_at.desc()).all()
    return {
        "object": "list",
        "data": [{"id": a.id, "object": "model", "created": int(a.created_at.timestamp()),
                  "owned_by": "agenthub", "name": a.name,
                  "description": a.description} for a in agents],
    }


@compat.post("/chat/completions")
def chat_completions(body: CompatChatRequest, request: Request,
                     db: Session = Depends(get_db), key: AppKey | None = Depends(optional_key)):
    agent = _resolve_agent(db, body.model)
    content = "\n".join(m.content for m in body.messages)
    payload = _extract_payload(content)
    run = runner_mod.create_run(db, agent, payload, payload.get("customer_no", ""), async_mode=False)
    emit(db, run.id, "api.invoked", {"caller": key_label(key), "endpoint": "/v1/chat/completions",
                                     "mode": "sync"})

    output = json.loads(run.output_json or "{}")
    text = output.get("summary") or run.error or "（本次运行没有输出结论）"
    created = int(time.time())
    completion_id = f"chatcmpl-{run.id}"
    usage = {"prompt_tokens": max(1, len(content) // 4), "completion_tokens": max(1, len(text) // 2),
             "total_tokens": run.tokens}

    if body.stream:
        def gen():
            size = max(8, len(text) // 6)
            for i in range(0, len(text), size):
                chunk = {"id": completion_id, "object": "chat.completion.chunk", "created": created,
                         "model": agent.id,
                         "choices": [{"index": 0, "delta": {"content": text[i:i + size]},
                                      "finish_reason": None}]}
                yield "data: " + json.dumps(chunk, ensure_ascii=False) + "\n\n"
            end = {"id": completion_id, "object": "chat.completion.chunk", "created": created,
                   "model": agent.id, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            yield "data: " + json.dumps(end, ensure_ascii=False) + "\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    return {
        "id": completion_id, "object": "chat.completion", "created": created,
        "model": agent.id,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": text},
                     "metadata": {"run_id": run.id, "status": run.status,
                                  "report_url": _report_url(request, run)}}],
        "usage": usage,
    }
