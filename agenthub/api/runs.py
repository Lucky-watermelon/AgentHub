"""运行接口：触发运行、SSE 事件、Checkpoint、审批、Trace 与统计。"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import runner as runner_mod
from ..config import settings
from ..db import SessionLocal, get_db
from ..errors import NotFound
from ..models import Agent, Checkpoint, Run, RunEvent, ToolCall
from ..runner import uid
from ..schemas import ApproveRequest, RunCreate, checkpoint_out, run_out

router = APIRouter()

TERMINAL = ("COMPLETED", "FAILED", "REJECTED", "CANCELLED")


def _get_run(db: Session, run_id: str) -> Run:
    run = db.get(Run, run_id)
    if not run:
        raise NotFound(f"运行不存在：{run_id}")
    return run


@router.get("/runs")
def list_runs(limit: int = 50, agent_id: str | None = None, db: Session = Depends(get_db)):
    query = db.query(Run)
    if agent_id:
        query = query.filter(Run.agent_id == agent_id)
    rows = query.order_by(Run.created_at.desc()).limit(limit).all()
    return [run_out(r) for r in rows]


@router.post("/runs", status_code=201)
def create_run(body: RunCreate, db: Session = Depends(get_db)):
    agent = db.get(Agent, body.agent_id)
    if not agent:
        raise NotFound(f"智能体不存在：{body.agent_id}")
    customer_no = body.customer_no or (body.payload.get("customer_no") or "C-DEMO")
    run = runner_mod.create_run(db, agent, body.payload, customer_no,
                                async_mode=body.async_mode,
                                idempotency_key=body.idempotency_key)
    return run_out(run)


@router.get("/runs/{run_id}")
def get_run(run_id: str, db: Session = Depends(get_db)):
    run = _get_run(db, run_id)
    events = (db.query(RunEvent).filter(RunEvent.run_id == run_id)
                .order_by(RunEvent.seq).all())
    cps = (db.query(Checkpoint).filter(Checkpoint.run_id == run_id)
             .order_by(Checkpoint.step).all())
    data = run_out(run)
    data["events"] = [{"seq": e.seq, "event": e.event,
                       "data": json.loads(e.data or "{}"),
                       "time": e.created_at.strftime("%H:%M:%S")} for e in events]
    data["checkpoints"] = [checkpoint_out(c) for c in cps]
    return data


@router.get("/runs/{run_id}/events")
async def stream_events(run_id: str, request_offset: int = 0, db: Session = Depends(get_db)):
    """SSE 事件流；支持从指定 seq 之后继续（等价 Last-Event-ID）。"""
    run = db.get(Run, run_id)
    if not run:
        raise NotFound(f"运行不存在：{run_id}")

    async def gen():
        last = max(0, request_offset)
        deadline = time.time() + settings.run_timeout_s
        idle = 0
        while True:
            rows, status = [], "UNKNOWN"
            db = SessionLocal()
            try:
                run = db.get(Run, run_id)
                status = run.status if run else "UNKNOWN"
                events = (db.query(RunEvent)
                            .filter(RunEvent.run_id == run_id, RunEvent.seq > last)
                            .order_by(RunEvent.seq).all())
                for e in events:
                    rows.append((e.seq, e.event, e.data or "{}"))
            finally:
                db.close()

            for seq, event, data in rows:
                last = seq
                payload = json.dumps({"seq": seq, "event": event, "data": json.loads(data)},
                                     ensure_ascii=False)
                yield f"id: {seq}\nevent: {event}\ndata: {payload}\n\n"

            if status in TERMINAL and not rows:
                idle += 1
                if idle >= 2:
                    yield f"event: stream.closed\ndata: {json.dumps({'status': status})}\n\n"
                    return
            if time.time() > deadline:
                yield "event: stream.timeout\ndata: {}\n\n"
                return
            await asyncio.sleep(0.2)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/runs/{run_id}/checkpoints")
def list_checkpoints(run_id: str, db: Session = Depends(get_db)):
    _get_run(db, run_id)
    rows = (db.query(Checkpoint).filter(Checkpoint.run_id == run_id)
              .order_by(Checkpoint.step).all())
    return [checkpoint_out(c) for c in rows]


@router.post("/runs/{run_id}/approve")
def approve_run(run_id: str, body: ApproveRequest, db: Session = Depends(get_db)):
    _get_run(db, run_id)
    run = runner_mod.approve(db, run_id, body.decision, body.comment)
    return run_out(run)


@router.post("/runs/{run_id}/replay")
def replay_run(run_id: str, payload: dict | None = None, db: Session = Depends(get_db)):
    _get_run(db, run_id)
    run = runner_mod.replay(db, run_id, (payload or {}).get("checkpoint_id", ""))
    return run_out(run)


# ---------------- 运行监控 ----------------
def _spans(db: Session, run: Run) -> list[dict]:
    calls = (db.query(ToolCall).filter(ToolCall.run_id == run.id)
               .order_by(ToolCall.created_at).all())
    events = (db.query(RunEvent).filter(RunEvent.run_id == run.id)
                .order_by(RunEvent.seq).all())
    spans = [
        {"n": "agenthub.request", "k": "网关", "d": run.dur_ms, "depth": 0,
         "a": f"POST /api/v1/runs agent={run.agent_id} v{run.agent_version}"},
        {"n": "agenthub.run", "k": "运行时", "d": run.dur_ms, "depth": 1,
         "a": f"thread={run.thread_id} status={run.status}"},
    ]
    steps = [e for e in events if e.event in ("node.start", "node.result", "tool.result", "message.delta")]
    pending: dict[str, list[dict]] = {}
    for e in steps:
        data = json.loads(e.data or "{}")
        if e.event == "node.start":
            span = {"n": f"node:{data.get('node_id')}", "k": "节点", "d": 0, "depth": 2,
                    "a": f"{data.get('node')} step={data.get('step')}"}
            spans.append(span)
            pending.setdefault(data.get("node_id") or "", []).append(span)
        elif e.event == "node.result":
            queue = pending.get(data.get("node") or "")
            if queue:
                queue.pop(0)["d"] = data.get("dur_ms", 0)
        elif e.event == "tool.result":
            spans.append({"n": f"tool:{data.get('tool')}", "k": "工具", "d": data.get("dur_ms", 0),
                          "depth": 3, "a": f"status={data.get('status')}"})
        elif e.event == "message.delta":
            spans.append({"n": "gen_ai.chat", "k": "模型", "d": data.get("dur_ms", 0),
                          "depth": 3, "a": f"model={data.get('model')} alias={data.get('alias')}"})
    # 用工具调用记录补全耗时（更准确）
    by_name = {}
    for c in calls:
        by_name.setdefault(c.tool_name, []).append(c.dur_ms)
    for s in spans:
        if s["n"].startswith("tool:") and not s["d"]:
            durs = by_name.get(s["n"][5:], [])
            if durs:
                s["d"] = durs[-1]
    return spans


@router.get("/traces")
def list_traces(limit: int = 50, db: Session = Depends(get_db)):
    rows = db.query(Run).order_by(Run.created_at.desc()).limit(limit).all()
    return [{"id": r.trace_id or r.id, "run_id": r.id, "agent": r.agent_name,
             "v": r.agent_version, "customer": r.customer_no, "status": r.status,
             "dur": r.dur_ms, "tokens": r.tokens,
             "at": r.created_at.strftime("%Y-%m-%d %H:%M:%S")} for r in rows]


@router.get("/traces/{trace_id}")
def get_trace(trace_id: str, db: Session = Depends(get_db)):
    run = (db.query(Run).filter(Run.trace_id == trace_id).first()
           or db.query(Run).filter(Run.id == trace_id).first())
    if not run:
        raise NotFound("Trace 不存在")
    return {"id": run.trace_id or run.id, "run_id": run.id, "agent": run.agent_name,
            "status": run.status, "dur": run.dur_ms, "tokens": run.tokens,
            "spans": _spans(db, run)}


@router.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    total = db.query(Run).count()
    completed = db.query(Run).filter(Run.status == "COMPLETED").count()
    failed = db.query(Run).filter(Run.status == "FAILED").count()
    waiting = db.query(Run).filter(Run.status == "WAITING_APPROVAL").count()
    avg = db.query(func.avg(Run.dur_ms)).filter(Run.status == "COMPLETED").scalar() or 0

    tool_rows = (db.query(ToolCall.tool_name, func.count(ToolCall.id), func.avg(ToolCall.dur_ms))
                   .group_by(ToolCall.tool_name).all())
    tools = []
    for name, calls, avg_ms in tool_rows:
        ok = db.query(ToolCall).filter(ToolCall.tool_name == name, ToolCall.status == "成功").count()
        tools.append({"name": name, "calls": calls, "ok": ok, "fail": calls - ok,
                      "avg": int(avg_ms or 0)})

    failures = (db.query(ToolCall).filter(ToolCall.status != "成功")
                  .order_by(ToolCall.created_at.desc()).limit(10).all())
    return {
        "runs": total, "completed": completed, "failed": failed, "waiting": waiting,
        "success_rate": round(completed / total, 4) if total else 0,
        "avg_dur": int(avg), "tools": tools,
        "failures": [{"tool": c.tool_name, "error": c.error, "dur_ms": c.dur_ms,
                      "time": c.created_at.strftime("%m-%d %H:%M:%S")} for c in failures],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
