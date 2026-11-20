"""运行执行器：把 DAG 编译为 LangGraph 并执行，产出事件流、Checkpoint 与 HTML 报告。

一次运行的生命周期：
    CREATED -> RUNNING -> COMPLETED
                       -> WAITING_APPROVAL -> COMPLETED / REJECTED
                       -> FAILED

执行优先走 LangGraph（``dag.compile_graph``）；编译失败时降级为按拓扑序顺序执行，
保证配置问题不会让运行直接不可用。
"""
from __future__ import annotations

import json
import threading
import time
import uuid as _uuid
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import builtin_tools, llm
from .config import settings
from .dag import compile_graph, normalize, topo_order, validate
from .db import SessionLocal
from .errors import ApiError, DagInvalid, NotFound
from .models import Agent, Checkpoint, Run, RunEvent, ToolCall
from .report import render_report, save_report


def uid(prefix: str) -> str:
    return f"{prefix}_{_uuid.uuid4().hex[:10]}"


class HitlInterrupt(Exception):
    def __init__(self, point: str, detail: str):
        super().__init__(detail)
        self.point = point
        self.detail = detail


# ---------------- 事件与检查点 ----------------
def emit(db: Session, run_id: str, event: str, data: dict) -> None:
    """写一条运行事件。

    使用独立短事务：事件写入失败不会污染调用方 session（避免 PendingRollback 连锁），
    失败时重试一次以规避 SQLite 瞬时锁冲突。
    """
    payload = json.dumps(data, ensure_ascii=False, default=str)
    for attempt in range(2):
        try:
            with SessionLocal() as s:
                seq = (s.query(func.coalesce(func.max(RunEvent.seq), 0))
                        .filter(RunEvent.run_id == run_id).scalar() or 0) + 1
                s.add(RunEvent(id=uid("ev"), run_id=run_id, seq=seq, event=event, data=payload))
                s.commit()
            return
        except Exception:
            if attempt == 1:
                raise
            time.sleep(0.02)


def checkpoint(db: Session, run_id: str, step: int, node: str, state: dict) -> None:
    with SessionLocal() as s:
        s.add(Checkpoint(id=uid("cp"), run_id=run_id, step=step, node=node,
                         state_json=json.dumps(state, ensure_ascii=False, default=str)[:20000]))
        s.commit()


def record_tool_call(db: Session, run: Run, tool_name: str, params: dict,
                     status: str, dur_ms: int, error: str = "") -> None:
    with SessionLocal() as s:
        s.add(ToolCall(id=uid("tc"), tool_name=tool_name, trace_id=run.trace_id, run_id=run.id,
                       params=json.dumps(params, ensure_ascii=False, default=str)[:1000],
                       status=status, dur_ms=dur_ms, error=error[:200]))
        s.commit()


# ---------------- 节点执行 ----------------
def _prompt_for(node: dict, state: dict) -> str:
    features = state.get("features") or {}
    return (
        f"你是企业风险分析助手。请基于以下结构化特征给出一段不超过 120 字的综合判断，"
        f"指出主要风险点与建议。\n客户编号：{state.get('customer_no')}\n"
        f"特征：{json.dumps(features, ensure_ascii=False)}"
    )


def make_node_runner(db: Session, run: Run, stats: dict, skip_hitl: bool = False):
    def runner(node: dict, state: dict) -> dict:
        nid = node.get("id") or ""
        ntype = node.get("type") or "tool"
        name = node.get("name") or nid
        stats["step"] += 1
        node_started = time.perf_counter()
        emit(db, run.id, "node.start", {"node_id": nid, "node_type": ntype,
                                        "node": name, "step": stats["step"]})
        checkpoint(db, run.id, stats["step"], name, {"features": state.get("features")})

        if ntype == "condition":
            kinds = [k for k in ("credit", "finance", "judicial") if (state.get("payload") or {}).get(k)]
            emit(db, run.id, "node.result", {"node": nid, "matched": kinds,
                                            "branches": len(node.get("branches") or []),
                                            "dur_ms": int((time.perf_counter() - node_started) * 1000)})
            return {}

        if ntype == "tool":
            tool_name = node.get("tool") or name
            params = {"customer_no": state.get("customer_no")}
            started = time.perf_counter()
            emit(db, run.id, "tool.call", {"tool": tool_name, "params": params})
            try:
                out = builtin_tools.invoke(tool_name, state.get("payload") or {},
                                           {"customer_no": state.get("customer_no", ""),
                                            "features": state.get("features") or {},
                                            "summary": state.get("summary", "")})
                dur = int((time.perf_counter() - started) * 1000)
                record_tool_call(db, run, tool_name, params, "成功", dur)
                emit(db, run.id, "tool.result", {"tool": tool_name, "status": "OK", "dur_ms": dur,
                                                 "keys": list(out.keys())})
            except Exception as exc:
                dur = int((time.perf_counter() - started) * 1000)
                record_tool_call(db, run, tool_name, params, "失败", dur, str(exc))
                emit(db, run.id, "tool.result", {"tool": tool_name, "status": "FAIL",
                                                 "dur_ms": dur, "error": str(exc)})
                raise
            if "features" in out:
                merged = dict(state.get("features") or {})
                merged.update(out["features"])
                out["features"] = merged
            emit(db, run.id, "node.result", {"node": nid,
                                             "dur_ms": int((time.perf_counter() - node_started) * 1000)})
            return out

        if ntype == "llm":
            alias = node.get("model") or settings.default_model
            prompt = _prompt_for(node, state)
            started = time.perf_counter()
            res = llm.complete(db, prompt, alias,
                               {"features": state.get("features") or {},
                                "customer_no": state.get("customer_no")})
            stats["tokens"] += int(res.get("tokens") or 0)
            dur = int((time.perf_counter() - started) * 1000)
            emit(db, run.id, "message.delta", {"delta": res["text"][:160], "model": res.get("model"),
                                               "alias": res.get("alias"), "dur_ms": dur,
                                               "degraded": res.get("degraded", False)})
            emit(db, run.id, "node.result", {"node": nid, "dur_ms": dur})
            return {"messages": (state.get("messages") or []) + [res["text"]],
                    "summary": res["text"]}

        if ntype == "hitl":
            level = ((state.get("features") or {}).get("judicial") or {}).get("level", "低")
            if level == "高" and not skip_hitl:
                raise HitlInterrupt(name, f"客户命中司法高风险（{level}），需人工复核后继续")
            emit(db, run.id, "node.result", {"node": nid, "decision": "SKIP",
                                            "reason": f"风险等级 {level}",
                                            "dur_ms": int((time.perf_counter() - node_started) * 1000)})
            return {}

        emit(db, run.id, "node.result", {"node": nid, "skipped": True})
        return {}

    return runner


def _sequential(dag: dict, state: dict, runner) -> dict:
    by_id = {n["id"]: n for n in dag["nodes"] if n.get("id")}
    for nid in topo_order(dag):
        node = by_id.get(nid)
        if not node or node.get("type") in ("start", "end"):
            continue
        state.update(runner(node, dict(state)))
    return state


# ---------------- 运行主流程 ----------------
def execute_run(run_id: str, skip_hitl: bool = False) -> None:
    db = SessionLocal()
    started = time.perf_counter()
    try:
        run = db.get(Run, run_id)
        if not run:
            return
        agent = db.get(Agent, run.agent_id)
        if not agent:
            run.status = "FAILED"
            run.error = "智能体不存在"
            db.commit()
            return

        payload = json.loads(run.input_json or "{}")
        stats = {"tokens": 0, "step": 0}
        state = {"customer_no": run.customer_no, "payload": payload, "features": {},
                 "messages": [], "report_html": "", "summary": "", "outputs": {}}

        run.status = "RUNNING"
        run.trace_id = run.trace_id or uid("tr")
        db.commit()
        emit(db, run.id, "run.started", {"run_id": run.id, "agent": agent.name,
                                         "agent_version": run.agent_version,
                                         "thread_id": run.thread_id,
                                         "customer_no": run.customer_no})

        dag_raw = json.loads(agent.dag_json or "{}")
        dag, errors, warnings = validate(dag_raw)
        if errors:
            raise DagInvalid(errors)

        runner = make_node_runner(db, run, stats, skip_hitl=skip_hitl)
        result: dict
        try:
            graph = compile_graph(dag, runner)
            result = graph.invoke(state, {"recursion_limit": 200})
        except HitlInterrupt:
            raise
        except Exception as graph_error:  # LangGraph 不可用时降级顺序执行
            emit(db, run.id, "run.notice", {"message": "LangGraph 执行异常，降级为顺序执行",
                                            "error": str(graph_error)[:200]})
            result = _sequential(dag, state, runner)

        features = result.get("features") or {}
        summary = result.get("summary") or ""
        html = result.get("report_html") or render_report(
            customer_no=run.customer_no, payload=payload, features=features, summary=summary)
        report_path = save_report(html, run.customer_no, run.id)

        run.status = "COMPLETED"
        run.features_json = json.dumps(features, ensure_ascii=False, default=str)
        run.output_json = json.dumps({"summary": summary, "report_path": report_path,
                                      "findings": features}, ensure_ascii=False, default=str)
        run.report_path = report_path
        run.tokens = stats["tokens"]
        run.amount = round(stats["tokens"] * 0.000012, 6)
        run.dur_ms = int((time.perf_counter() - started) * 1000)
        run.finished_at = datetime.now()
        db.commit()
        emit(db, run.id, "run.completed", {"status": "COMPLETED", "tokens": run.tokens,
                                           "amount": run.amount, "report_path": report_path,
                                           "dur_ms": run.dur_ms})
    except HitlInterrupt as exc:
        db.rollback()
        run = db.get(Run, run_id)
        if run:
            run.status = "WAITING_APPROVAL"
            run.features_json = json.dumps({"hitl": {"point": exc.point, "detail": exc.detail}},
                                           ensure_ascii=False)
            run.dur_ms = int((time.perf_counter() - started) * 1000)
            db.commit()
            emit(db, run.id, "run.waiting_approval",
                 {"interrupt_point": exc.point, "detail": exc.detail, "code": "AH-RUNTIME-206"})
    except Exception as exc:
        db.rollback()
        run = db.get(Run, run_id)
        if run:
            run.status = "FAILED"
            run.error = str(exc)[:400]
            run.dur_ms = int((time.perf_counter() - started) * 1000)
            run.finished_at = datetime.now()
            db.commit()
            emit(db, run.id, "run.failed", {"error": str(exc)[:400], "code": "AH-RUN-500"})
    finally:
        db.close()
        try:  # Langfuse 旁路上报：关闭时零开销，失败不影响运行结果
            from .observability import report_run_async

            report_run_async(run_id)
        except Exception:
            pass


def create_run(db: Session, agent: Agent, payload: dict, customer_no: str,
               async_mode: bool = True) -> Run:
    dag, errors, _ = validate(json.loads(agent.dag_json or "{}"))
    if errors:
        raise DagInvalid(errors)
    run = Run(id=uid("run"), agent_id=agent.id, agent_name=agent.name,
              agent_version=agent.version, customer_no=customer_no or "C-DEMO",
              status="CREATED", trace_id=uid("tr"), thread_id=uid("th"),
              input_json=json.dumps(payload, ensure_ascii=False))
    db.add(run)
    db.commit()

    if async_mode:
        threading.Thread(target=execute_run, args=(run.id,), daemon=True).start()
    else:
        execute_run(run.id)
    db.refresh(run)
    return run


def approve(db: Session, run_id: str, decision: str, comment: str = "") -> Run:
    run = db.get(Run, run_id)
    if not run:
        raise NotFound("运行不存在")
    if run.status != "WAITING_APPROVAL":
        raise ApiError(f"当前状态 {run.status} 不可审批", code="AH-RUNTIME-409", status_code=409)
    emit(db, run.id, "approvals.decision",
         {"decision": decision, "comment": comment, "actor": "console"})
    if decision.upper() == "APPROVED":
        run.status = "RUNNING"
        db.commit()
        execute_run(run.id, skip_hitl=True)
    else:
        run.status = "REJECTED"
        run.finished_at = datetime.now()
        db.commit()
        emit(db, run.id, "run.rejected", {"decision": decision})
    db.refresh(run)
    return run


def replay(db: Session, run_id: str, checkpoint_id: str) -> Run:
    """从指定 Checkpoint 重放：以原输入开一次新运行。"""
    src = db.get(Run, run_id)
    if not src:
        raise NotFound("运行不存在")
    agent = db.get(Agent, src.agent_id)
    if not agent:
        raise NotFound("智能体不存在")
    return create_run(db, agent, json.loads(src.input_json or "{}"), src.customer_no, async_mode=False)
