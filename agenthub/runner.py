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
import logging
import threading
import time
import uuid as _uuid
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import evidence as evidence_mod
from . import llm, sandbox, tools_runtime
from .config import settings
from .dag import compile_graph, normalize, topo_order, validate
from .db import SessionLocal
from .errors import ApiError, DagInvalid, NotFound
from .gate import validate_dependencies
from .models import Agent, Checkpoint, Run, RunEvent, Skill, Tool, ToolCall
from .report import render_report, save_report

logger = logging.getLogger("agenthub.runner")


def uid(prefix: str) -> str:
    return f"{prefix}_{_uuid.uuid4().hex[:10]}"


class HitlInterrupt(Exception):
    def __init__(self, point: str, detail: str):
        super().__init__(detail)
        self.point = point
        self.detail = detail


# ---------------- 事件与检查点 ----------------
class EventBuffer:
    """一次运行内批量提交事件 / 快照 / 工具记录。

    压测显示同步吞吐卡在"每条事件一个事务"：一次 10 节点运行约 40 次 commit。
    改为按节点边界 flush 后，事务数降到约 12 次。
    代价是 SSE 从逐条推送变为按节点推送（一帧内含该节点的多条事件）。
    """

    def __init__(self, run_id: str, max_size: int = 12) -> None:
        self.run_id = run_id
        self.max_size = max_size
        self._items: list[tuple] = []
        self._seq = 0
        self._lock = threading.RLock()

    def add_event(self, event: str, data: dict) -> None:
        self._push(("event", event, data))

    def add_checkpoint(self, step: int, node: str, state: dict) -> None:
        self._push(("cp", step, node, state))

    def add_tool_call(self, trace_id: str, tool_name: str, params: dict,
                      status: str, dur_ms: int, error: str) -> None:
        self._push(("tc", trace_id, tool_name, params, status, dur_ms, error))

    def _push(self, item: tuple) -> None:
        with self._lock:
            self._items.append(item)
            full = len(self._items) >= self.max_size
        if full:
            self.flush()

    def flush(self) -> None:
        """把缓冲区内容一次事务写入；失败只丢这一批，不影响运行。"""
        with self._lock:
            if not self._items:
                return
            batch, self._items = self._items, []
            try:
                with SessionLocal() as s:
                    if not self._seq:
                        self._seq = (s.query(func.coalesce(func.max(RunEvent.seq), 0))
                                      .filter(RunEvent.run_id == self.run_id).scalar() or 0)
                    for item in batch:
                        kind = item[0]
                        if kind == "event":
                            self._seq += 1
                            s.add(RunEvent(id=uid("ev"), run_id=self.run_id, seq=self._seq,
                                           event=item[1],
                                           data=json.dumps(item[2], ensure_ascii=False, default=str)))
                        elif kind == "cp":
                            s.add(Checkpoint(id=uid("cp"), run_id=self.run_id, step=item[1],
                                             node=item[2],
                                             state_json=json.dumps(item[3], ensure_ascii=False,
                                                                   default=str)[:20000]))
                        else:
                            s.add(ToolCall(id=uid("tc"), tool_name=item[2], trace_id=item[1],
                                           run_id=self.run_id,
                                           params=json.dumps(item[3], ensure_ascii=False,
                                                             default=str)[:1000],
                                           status=item[4], dur_ms=item[5], error=item[6][:200]))
                    s.commit()
            except Exception:
                logger.warning("事件批量写入失败，丢弃 %s 条 run_id=%s", len(batch), self.run_id,
                               exc_info=True)


# 缓冲区按 run_id 注册。不用 contextvar：LangGraph 的并行分支在新线程执行，
# 线程池不会传播 contextvars，按 run_id 查表才是线程安全的。
_buffers: dict[str, EventBuffer] = {}
_buffers_lock = threading.Lock()


def _buffer_for(run_id: str) -> EventBuffer | None:
    with _buffers_lock:
        return _buffers.get(run_id)


def _register_buffer(buffer: EventBuffer) -> None:
    with _buffers_lock:
        _buffers[buffer.run_id] = buffer


def _drop_buffer(run_id: str) -> None:
    with _buffers_lock:
        _buffers.pop(run_id, None)


def _append_event(run_id: str, event: str, data: dict) -> None:
    """无缓冲区时的直写路径（例如请求线程里记录 api.invoked）。"""
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


def emit(db: Session, run_id: str, event: str, data: dict) -> None:
    """写一条运行事件：该 run 正在执行时走批量缓冲，否则直写。"""
    from .tracing import current_request_id

    rid = current_request_id()
    if rid and isinstance(data, dict):
        data = {**data, "request_id": rid}
    buffer = _buffer_for(run_id)
    if buffer is not None:
        buffer.add_event(event, data)
        return
    _append_event(run_id, event, data)


def checkpoint(db: Session, run_id: str, step: int, node: str, state: dict) -> None:
    buffer = _buffer_for(run_id)
    if buffer is not None:
        buffer.add_checkpoint(step, node, state)
        return
    with SessionLocal() as s:
        s.add(Checkpoint(id=uid("cp"), run_id=run_id, step=step, node=node,
                         state_json=json.dumps(state, ensure_ascii=False, default=str)[:20000]))
        s.commit()


def record_tool_call(db: Session, run: Run, tool_name: str, params: dict,
                     status: str, dur_ms: int, error: str = "") -> None:
    buffer = _buffer_for(run.id)
    if buffer is not None:
        buffer.add_tool_call(run.trace_id, tool_name, params, status, dur_ms, error)
        return
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


def _skill_prompt(skill: Skill, node: dict, state: dict, features: dict) -> str:
    content = (skill.content or "").strip()
    guide = content[:4000] if content else (
        f"模板标识：{skill.prompt}\n技能说明：{skill.description or '—'}")
    return (
        f"你正在执行技能「{skill.name}」（v{skill.version}）。\n"
        f"以下是该技能的说明书（SKILL.md）：\n{guide}\n\n"
        f"客户编号：{state.get('customer_no')}\n"
        f"请按说明书执行，并基于以下结构化特征输出不超过 120 字的综合判断，"
        f"指出主要风险点与建议。\n"
        f"特征：{json.dumps(features, ensure_ascii=False)}"
    )


def make_node_runner(db: Session, run: Run, stats: dict, skip_hitl: bool = False,
                     skip_ids: set[str] | None = None,
                     completed: set[str] | None = None):
    skipped = skip_ids or set()

    def runner(node: dict, state: dict) -> dict:
        nid = node.get("id") or ""
        ntype = node.get("type") or "tool"
        name = node.get("name") or nid

        def finish(payload: dict) -> dict:
            if completed is not None:
                completed.add(nid)
            buffer = _buffer_for(run.id)
            if buffer is not None:  # 节点边界落盘：保证 SSE 按节点推进，且崩溃时丢得少
                buffer.flush()
            return payload

        if nid in skipped:  # 断点续跑：中断前已完成的节点不再重复执行
            emit(db, run.id, "node.skipped", {"node_id": nid, "node": name,
                                              "reason": "已在中断前完成"})
            return {}

        stats["step"] += 1
        stats["current_node"] = nid
        node_started = time.perf_counter()
        emit(db, run.id, "node.start", {"node_id": nid, "node_type": ntype,
                                        "node": name, "step": stats["step"]})
        checkpoint(db, run.id, stats["step"], name, {"features": state.get("features")})

        if ntype == "condition":
            kinds = [k for k in ("credit", "finance", "judicial") if (state.get("payload") or {}).get(k)]
            emit(db, run.id, "node.result", {"node": nid, "matched": kinds,
                                            "branches": len(node.get("branches") or []),
                                            "dur_ms": int((time.perf_counter() - node_started) * 1000)})
            return finish({})

        # 节点级 params 覆盖报文顶层字段：知识库检索的 q / HTTP 工具入参都靠它传入
        tool_payload = dict(state.get("payload") or {})
        if isinstance(node.get("params"), dict):
            tool_payload.update(node["params"])

        if ntype == "tool":
            tool_name = node.get("tool") or name
            params = {"customer_no": state.get("customer_no")}
            if isinstance(node.get("params"), dict):
                params.update(node["params"])
            started = time.perf_counter()
            emit(db, run.id, "tool.call", {"tool": tool_name, "params": params})
            try:
                out = tools_runtime.invoke_by_name(db, tool_name, tool_payload,
                                                   {"customer_no": state.get("customer_no", ""),
                                                    "features": state.get("features") or {},
                                                    "summary": state.get("summary", ""),
                                                    "db": db,
                                                    "evidence_items": stats.setdefault("evidence", []),
                                                    "model": node.get("model") or settings.default_model})
                dur = int((time.perf_counter() - started) * 1000)
                record_tool_call(db, run, tool_name, params, "成功", dur)
                emit(db, run.id, "tool.result", {"tool": tool_name, "status": "OK", "dur_ms": dur,
                                                 "keys": list(out.keys())})
                stats.setdefault("evidence", []).extend(
                    evidence_mod.from_tool(tool_name, nid, out, dur))
            except Exception as exc:
                dur = int((time.perf_counter() - started) * 1000)
                record_tool_call(db, run, tool_name, params, "失败", dur, str(exc))
                emit(db, run.id, "tool.result", {"tool": tool_name, "status": "FAIL",
                                                 "dur_ms": dur, "error": str(exc)})
                stats.setdefault("evidence", []).append(
                    evidence_mod.from_error(tool_name, nid, str(exc)))
                raise
            if "features" in out:
                merged = dict(state.get("features") or {})
                merged.update(out["features"])
                out["features"] = merged
            emit(db, run.id, "node.result", {"node": nid,
                                             "dur_ms": int((time.perf_counter() - node_started) * 1000)})
            return finish(out)

        if ntype == "skill":
            # 技能节点 = 只读工具 → 技能脚本（沙箱）→ 技能模型判读。
            # 产出具（fs:write）与 LLM 类工具跳过，避免提前渲染报告或重复计费。
            skill_name = node.get("skill") or name
            skill = db.query(Skill).filter(Skill.name == skill_name).first()
            if not skill:
                raise ApiError(f"技能不存在：{skill_name}", code="AH-RUNTIME-422", status_code=422)
            if skill.status != "published":
                raise ApiError(f"技能未发布：{skill_name}（当前 {skill.status}）",
                               code="AH-SKILL-409", status_code=409)
            try:
                skill_tools = json.loads(skill.tools_json or "[]")
            except json.JSONDecodeError:
                skill_tools = []
            try:
                skill_scripts = sandbox.normalize_scripts(json.loads(skill.scripts_json or "[]"))
            except json.JSONDecodeError:
                skill_scripts = []
            features = dict(state.get("features") or {})
            executed: list[str] = []
            if node.get("run_tools", True):
                for tool_name in skill_tools:
                    tool = db.query(Tool).filter(Tool.name == tool_name).first()
                    if not tool:
                        raise ApiError(f"技能绑定的工具不存在：{tool_name}",
                                       code="AH-TOOL-422", status_code=422)
                    if tool.status != "启用":
                        raise ApiError(f"技能绑定的工具已停用：{tool_name}",
                                       code="AH-TOOL-409", status_code=409)
                    if tool.permission == "fs:write" or (tool.impl or "").lower() == "llm":
                        continue
                    param_errors = tools_runtime.validate_params(tool.schema_json, tool_payload)
                    if param_errors:
                        emit(db, run.id, "node.notice",
                             {"node": nid, "message": f"跳过技能工具 {tool_name}",
                              "reason": "；".join(param_errors)})
                        continue
                    t_started = time.perf_counter()
                    emit(db, run.id, "tool.call",
                         {"tool": tool_name, "params": {"skill": skill_name}, "via": "skill"})
                    try:
                        out = tools_runtime.invoke_by_name(
                            db, tool_name, tool_payload,
                            {"customer_no": state.get("customer_no", ""), "features": features,
                             "summary": state.get("summary", ""), "db": db,
                             "evidence_items": stats.setdefault("evidence", []),
                             "model": node.get("model") or skill.model})
                    except Exception as exc:
                        dur = int((time.perf_counter() - t_started) * 1000)
                        record_tool_call(db, run, tool_name, {"skill": skill_name}, "失败", dur,
                                         str(exc))
                        emit(db, run.id, "tool.result",
                             {"tool": tool_name, "status": "FAIL", "dur_ms": dur,
                              "error": str(exc), "via": "skill"})
                        stats.setdefault("evidence", []).append(
                            evidence_mod.from_error(tool_name, nid, str(exc)))
                        raise
                    dur = int((time.perf_counter() - t_started) * 1000)
                    record_tool_call(db, run, tool_name, {"skill": skill_name}, "成功", dur)
                    emit(db, run.id, "tool.result", {"tool": tool_name, "status": "OK",
                                                     "dur_ms": dur, "via": "skill"})
                    stats.setdefault("evidence", []).extend(
                        evidence_mod.from_tool(tool_name, nid, out, dur))
                    features.update((out or {}).get("features") or {})
                    executed.append(tool_name)

            if node.get("run_script", True):
                wanted = node.get("script") or skill.entrypoint
                script = sandbox.pick_script(skill_scripts, skill.entrypoint,
                                             node.get("script") or None)
                if wanted and not any(s["name"] == wanted for s in skill_scripts):
                    raise ApiError(f"入口脚本不存在：{wanted}", code="AH-SKILL-422",
                                   status_code=422)
                if script:
                    started = time.perf_counter()
                    label = f"技能脚本:{skill.name}"
                    emit(db, run.id, "tool.call",
                         {"tool": label, "params": {"script": script["name"]}, "via": "skill"})
                    ctx_in = {"payload": tool_payload, "features": features,
                              "customer_no": state.get("customer_no", ""),
                              "params": node.get("params") or {}}
                    limit = max(1, min(int(skill.timeout_s or 5), 20))
                    result = sandbox.run_python(script["code"], ctx_in, limit)
                    dur = int((time.perf_counter() - started) * 1000)
                    ok = bool(result.get("ok"))
                    record_tool_call(
                        db, run, label, {"script": script["name"], "skill": skill_name},
                        "成功" if ok else "失败", dur,
                        "" if ok else (result.get("stderr")
                                       or "；".join(result.get("errors") or [])))
                    emit(db, run.id, "tool.result",
                         {"tool": label, "status": "OK" if ok else "FAIL", "dur_ms": dur,
                          "stdout": (result.get("stdout") or "")[:300], "via": "skill"})
                    if not ok:
                        reason = (result.get("stderr")
                                  or "；".join(result.get("errors") or []) or "未知错误")
                        stats.setdefault("evidence", []).append(
                            evidence_mod.from_error(label, nid, reason))
                        raise ApiError(f"技能脚本执行失败：{reason[:300]}",
                                       code="AH-SKILL-500", status_code=500)
                    stats.setdefault("evidence", []).append(
                        evidence_mod.from_script(skill.name, script["name"], result, nid, dur))
                    parsed = result.get("result")
                    if isinstance(parsed, dict):
                        if isinstance(parsed.get("features"), dict):
                            features.update(parsed["features"])
                        elif "result" in parsed:
                            features["script_result"] = parsed["result"]
                        else:
                            features.update(parsed)
                    elif parsed is not None:
                        features["script_result"] = parsed
                    elif result.get("stdout"):
                        features["script_output"] = result["stdout"][:2000]

            alias = node.get("model") or skill.model
            prompt = _skill_prompt(skill, node, state, features)
            started = time.perf_counter()
            res = llm.complete(db, prompt, alias,
                               {"features": features, "customer_no": state.get("customer_no")})
            stats["tokens"] += int(res.get("tokens") or 0)
            stats.setdefault("evidence", []).append(
                evidence_mod.from_model(alias, res, nid))
            dur = int((time.perf_counter() - started) * 1000)
            emit(db, run.id, "message.delta",
                 {"delta": res["text"][:160], "model": res.get("model"), "alias": res.get("alias"),
                  "skill": skill.name, "skill_version": skill.version, "dur_ms": dur,
                  "degraded": res.get("degraded", False)})
            emit(db, run.id, "node.result",
                 {"node": nid, "skill": skill.name, "skill_version": skill.version,
                  "tools": executed, "dur_ms": int((time.perf_counter() - node_started) * 1000)})
            return finish({"features": features,
                           "messages": (state.get("messages") or []) + [res["text"]],
                           "summary": res["text"]})

        if ntype == "llm":
            alias = node.get("model") or settings.default_model
            prompt = _prompt_for(node, state)
            started = time.perf_counter()
            res = llm.complete(db, prompt, alias,
                               {"features": state.get("features") or {},
                                "customer_no": state.get("customer_no")})
            stats["tokens"] += int(res.get("tokens") or 0)
            stats.setdefault("evidence", []).append(
                evidence_mod.from_model(alias, res, nid))
            dur = int((time.perf_counter() - started) * 1000)
            emit(db, run.id, "message.delta", {"delta": res["text"][:160], "model": res.get("model"),
                                               "alias": res.get("alias"), "dur_ms": dur,
                                               "degraded": res.get("degraded", False)})
            emit(db, run.id, "node.result", {"node": nid, "dur_ms": dur})
            return finish({"messages": (state.get("messages") or []) + [res["text"]],
                           "summary": res["text"]})

        if ntype == "hitl":
            level = ((state.get("features") or {}).get("judicial") or {}).get("level", "低")
            if level == "高" and not skip_hitl:
                # 图执行时 graph.invoke 不回写入参，中断快照必须取自节点收到的状态
                stats["last_state"] = state
                detail = f"客户命中司法高风险（{level}），需人工复核后继续"
                stats.setdefault("evidence", []).append(
                    evidence_mod.from_hitl(name, detail, nid))
                raise HitlInterrupt(name, detail)
            emit(db, run.id, "node.result", {"node": nid, "decision": "SKIP",
                                            "reason": f"风险等级 {level}",
                                            "dur_ms": int((time.perf_counter() - node_started) * 1000)})
            return finish({})

        emit(db, run.id, "node.result", {"node": nid, "skipped": True})
        return finish({})

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
def execute_run(run_id: str, skip_hitl: bool = False, resume: bool = False) -> None:
    """执行一次运行。

    ``resume=True`` 时从上次 HITL 中断处继续：恢复 state 快照，并跳过已完成节点，
    避免工具调用被重复执行。
    """
    db = SessionLocal()
    started = time.perf_counter()
    state: dict = {}
    completed: set[str] = set()
    buffer = EventBuffer(run_id, max_size=max(1, settings.event_batch_size))
    _register_buffer(buffer)
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
        stats = {"tokens": 0, "step": 0, "current_node": ""}
        state = {"customer_no": run.customer_no, "payload": payload, "features": {},
                 "messages": [], "report_html": "", "summary": "", "outputs": {}}

        if resume:
            try:
                saved = json.loads(run.state_json or "{}")
            except json.JSONDecodeError:
                saved = {}
            try:
                progress = json.loads(run.node_progress or "{}")
            except json.JSONDecodeError:
                progress = {}
            completed = set(progress.get("completed") or [])
            if isinstance(saved, dict) and saved:
                saved_items = saved.pop("_evidence_items", [])
                state.update(saved)
                if isinstance(saved_items, list):
                    stats["evidence"] = saved_items
            state["payload"] = payload or state.get("payload") or {}
            state["customer_no"] = run.customer_no or state.get("customer_no") or ""

        run.status = "RUNNING"
        run.trace_id = run.trace_id or uid("tr")
        db.commit()
        emit(db, run.id, "run.resumed" if resume else "run.started",
             {"run_id": run.id, "agent": agent.name,
              "agent_version": run.agent_version,
              "thread_id": run.thread_id,
              "customer_no": run.customer_no,
              "resumed_from": sorted(completed)})
        buffer.flush()  # 开始事件立即落盘，客户端马上能看到进度

        dag_raw = json.loads(agent.dag_json or "{}")
        dag, errors, warnings = validate(dag_raw)
        if errors:
            raise DagInvalid(errors)
        dep_errors, _ = validate_dependencies(db, dag)
        if dep_errors:  # 发布后资产被停用 / 删除时，运行前再拦一次
            raise DagInvalid(dep_errors)

        skip_ids = set(completed)
        if skip_hitl:  # 审批已通过：审批节点本身不再执行
            skip_ids |= {n["id"] for n in dag["nodes"] if n.get("type") == "hitl"}
        runner = make_node_runner(db, run, stats, skip_hitl=skip_hitl,
                                  skip_ids=skip_ids, completed=completed)
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
        evidence = evidence_mod.build_evidence(
            features, summary, stats.get("evidence") or [])
        html = result.get("report_html") or render_report(
            customer_no=run.customer_no, payload=payload, features=features,
            summary=summary, evidence=evidence)
        report_path = save_report(html, run.customer_no, run.id)

        run.status = "COMPLETED"
        run.features_json = json.dumps(features, ensure_ascii=False, default=str)
        run.output_json = json.dumps({"summary": summary, "report_path": report_path,
                                      "findings": features, "evidence": evidence},
                                     ensure_ascii=False, default=str)
        run.report_path = report_path
        run.tokens = stats["tokens"]
        run.amount = round(stats["tokens"] * 0.000012, 6)
        run.dur_ms = int((time.perf_counter() - started) * 1000)
        run.finished_at = datetime.now()
        run.state_json = ""
        run.node_progress = ""
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
            # 保存断点：state 快照 + 已完成节点，审批后从这里继续。
            # 图执行下入参不会被回写，优先取节点内记录的状态。
            snapshot = dict(stats.get("last_state") or state)
            snapshot["_evidence_items"] = stats.get("evidence") or []
            run.state_json = json.dumps(snapshot, ensure_ascii=False, default=str)[:200000]
            run.node_progress = json.dumps({"completed": sorted(completed),
                                            "interrupted_at": stats.get("current_node", "")},
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
        try:
            buffer.flush()  # 兜底：终态事件与残留快照落盘
        finally:
            _drop_buffer(run_id)
        db.close()
        try:  # Langfuse 旁路上报：关闭时零开销，失败不影响运行结果
            from .observability import report_run_async

            report_run_async(run_id)
        except Exception:
            pass


def recover_stale_runs() -> int:
    """服务重启后清理僵死运行：RUNNING / CREATED → INTERRUPTED。

    这些运行所在的执行线程已随进程消失，不处理会永久停在 RUNNING。
    WAITING_APPROVAL 不动：其断点状态已持久化，重启后仍可审批恢复。
    """
    recovered: list[str] = []
    with SessionLocal() as db:
        rows = (db.query(Run)
                  .filter(Run.status.in_(("CREATED", "RUNNING")))
                  .all())
        for run in rows:
            run.status = "INTERRUPTED"
            run.error = "服务重启导致运行中断（AH-RUN-409），可通过重放重跑"
            run.finished_at = datetime.now()
            recovered.append(run.id)
        if recovered:
            db.commit()

    for run_id in recovered:
        try:
            emit(None, run_id, "run.interrupted",
                 {"reason": "server_restart", "code": "AH-RUN-409"})
        except Exception:
            pass
    return len(recovered)


def create_run(db: Session, agent: Agent, payload: dict, customer_no: str,
               async_mode: bool = True, idempotency_key: str = "") -> Run:
    dag, errors, _ = validate(json.loads(agent.dag_json or "{}"))
    if errors:
        raise DagInvalid(errors)
    dep_errors, _ = validate_dependencies(db, dag)
    if dep_errors:
        raise DagInvalid(dep_errors)

    key = (idempotency_key or "").strip()
    if key:
        existing = (db.query(Run)
                      .filter(Run.idempotency_key == key,
                              Run.status.in_(("CREATED", "RUNNING", "WAITING_APPROVAL", "COMPLETED")))
                      .order_by(Run.created_at.desc()).first())
        if existing:
            emit(db, existing.id, "run.idempotent_hit", {"idempotency_key": key})
            setattr(existing, "_idempotent_hit", True)
            return existing

    run = Run(id=uid("run"), agent_id=agent.id, agent_name=agent.name,
              agent_version=agent.version, customer_no=customer_no or "C-DEMO",
              status="CREATED", trace_id=uid("tr"), thread_id=uid("th"),
              idempotency_key=key,
              input_json=json.dumps(payload, ensure_ascii=False))
    db.add(run)
    db.commit()

    if async_mode:
        from .executor import submit_run

        if not submit_run(run.id):
            run.status = "REJECTED"
            run.error = "运行队列已满（AH-RUNTIME-429）"
            run.finished_at = datetime.now()
            db.commit()
            raise ApiError("运行队列已满，请稍后重试", code="AH-RUNTIME-429", status_code=429,
                           headers={"Retry-After": "5"})
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
        # 断点续跑：恢复 state 快照，跳过已完成节点与审批节点，避免重复执行工具
        execute_run(run.id, skip_hitl=True, resume=True)
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
