"""Langfuse 旁路上报（可选）。

设计原则：
- 旁路：上报失败只记录状态，不影响运行结果；关闭时零开销。
- 零新增依赖：直接调用 Langfuse Ingestion API（``POST /api/public/ingestion``），
  用 ``httpx`` 发送 batch 事件，兼容 v2 / v3 自托管实例。
- 一次运行 = 一条 trace；节点 / 工具 / 模型调用映射为 span / generation。
- 采样：按 ``langfuse_sampling`` 随机采样，采样窗口外的运行不上报。
"""
from __future__ import annotations

import base64
import json
import random
import threading
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .models import Run, RunEvent, Setting, ToolCall

TIMEOUT = 6.0


def _now_iso(value: datetime | None = None) -> str:
    dt = value or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _get(db: Session, key: str, default: str = "") -> str:
    row = db.get(Setting, key)
    return row.value if row and row.value else default


def _set(db: Session, key: str, value: str) -> None:
    row = db.get(Setting, key)
    if row:
        row.value = str(value)
    else:
        db.add(Setting(key=key, value=str(value)))
    db.commit()


def _bump(db: Session, key: str, delta: int = 1) -> int:
    try:
        current = int(_get(db, key, "0") or 0)
    except ValueError:
        current = 0
    _set(db, key, current + delta)
    return current + delta


def config_view(db: Session) -> dict:
    """当前生效配置：环境变量给凭证，数据库存开关与采样率。"""
    enabled = _get(db, "langfuse_enabled", "1" if settings.langfuse_enabled else "0") == "1"
    try:
        sampling = float(_get(db, "langfuse_sampling", str(settings.langfuse_sampling)))
    except ValueError:
        sampling = settings.langfuse_sampling
    host = _get(db, "langfuse_host", settings.langfuse_host).rstrip("/")
    configured = bool(settings.langfuse_public_key and settings.langfuse_secret_key)
    return {
        "enabled": enabled,
        "configured": configured,
        "host": host,
        "sampling": round(sampling, 3),
        "public_key": (settings.langfuse_public_key[:8] + "…") if settings.langfuse_public_key else "",
        "sent": int(_get(db, "langfuse_sent", "0") or 0),
        "failed": int(_get(db, "langfuse_failed", "0") or 0),
        "skipped": int(_get(db, "langfuse_skipped", "0") or 0),
        "last_error": _get(db, "langfuse_last_error", ""),
        "last_at": _get(db, "langfuse_last_at", ""),
        "mode": "旁路（失败不影响运行）",
    }


def update_config(db: Session, enabled: bool | None = None,
                  sampling: float | None = None, host: str | None = None) -> dict:
    if enabled is not None:
        _set(db, "langfuse_enabled", "1" if enabled else "0")
    if sampling is not None:
        _set(db, "langfuse_sampling", str(max(0.0, min(1.0, sampling))))
    if host:
        _set(db, "langfuse_host", host.rstrip("/"))
    return config_view(db)


def _build_batch(db: Session, run: Run) -> dict:
    events = (db.query(RunEvent).filter(RunEvent.run_id == run.id)
                .order_by(RunEvent.seq).all())
    calls = (db.query(ToolCall).filter(ToolCall.run_id == run.id)
               .order_by(ToolCall.created_at).all())
    start = _now_iso(run.created_at)
    end = _now_iso(run.finished_at or run.created_at)
    trace_id = run.trace_id or run.id

    batch = [{
        "id": str(uuid.uuid4()), "type": "trace-create", "timestamp": start,
        "body": {
            "id": trace_id,
            "name": f"{run.agent_name} v{run.agent_version}",
            "timestamp": start,
            "metadata": {
                "run_id": run.id, "customer_no": run.customer_no, "status": run.status,
                "tokens": run.tokens, "dur_ms": run.dur_ms, "error": run.error,
                "source": "agenthub",
            },
            "tags": ["agenthub", run.status.lower()],
            "release": "agenthub-0.1.0",
        },
    }]

    for event in events:
        try:
            data = json.loads(event.data or "{}")
        except json.JSONDecodeError:
            data = {}
        at = _now_iso(event.created_at)
        if event.event == "node.start":
            batch.append({
                "id": str(uuid.uuid4()), "type": "span-create", "timestamp": at,
                "body": {"id": f"{trace_id}-{event.id}", "traceId": trace_id,
                         "name": f"node:{data.get('node_id')}",
                         "startTime": at, "metadata": data},
            })
        elif event.event == "tool.result":
            batch.append({
                "id": str(uuid.uuid4()), "type": "span-create", "timestamp": at,
                "body": {"id": f"{trace_id}-{event.id}", "traceId": trace_id,
                         "name": f"tool:{data.get('tool')}", "startTime": at, "endTime": at,
                         "level": "DEFAULT" if data.get("status") == "OK" else "ERROR",
                         "metadata": data},
            })
        elif event.event == "message.delta":
            batch.append({
                "id": str(uuid.uuid4()), "type": "generation-create", "timestamp": at,
                "body": {"id": f"{trace_id}-{event.id}", "traceId": trace_id,
                         "name": "gen_ai.chat", "startTime": at, "endTime": at,
                         "model": data.get("model"), "modelParameters": {"alias": data.get("alias")},
                         "output": data.get("delta"), "metadata": data},
            })

    if run.tokens:
        for item in reversed(batch[1:]):
            if item["type"] == "generation-create":
                item["body"]["usage"] = {"total": run.tokens}
                break
    # trace 收尾：统计信息挂在 trace metadata 上
    batch[0]["body"]["metadata"]["span_count"] = sum(1 for i in batch[1:] if i["type"] == "span-create")
    batch[0]["body"]["metadata"]["generation_count"] = sum(
        1 for i in batch[1:] if i["type"] == "generation-create")
    batch[0]["body"]["metadata"]["tool_calls"] = len(calls)
    batch[0]["body"]["metadata"]["finished_at"] = end
    return {"batch": batch}


def _post(host: str, payload: dict) -> tuple[bool, str]:
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return False, "未配置 AH_LANGFUSE_PUBLIC_KEY / AH_LANGFUSE_SECRET_KEY"
    token = base64.b64encode(
        f"{settings.langfuse_public_key}:{settings.langfuse_secret_key}".encode()
    ).decode()
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            resp = client.post(f"{host}/api/public/ingestion", json=payload,
                               headers={"Authorization": f"Basic {token}"})
        if resp.status_code >= 300:
            return False, f"HTTP {resp.status_code}: {resp.text[:160]}"
        return True, ""
    except Exception as exc:  # 网络不可达、DNS 失败等
        return False, f"{type(exc).__name__}: {exc}"[:200]


def report_run(run_id: str, force: bool = False) -> dict:
    """上报一次运行。返回 {status: sent|skipped|failed|disabled, message}。"""
    db = SessionLocal()
    try:
        cfg = config_view(db)
        if not cfg["enabled"]:
            return {"status": "disabled", "message": "Langfuse 未启用"}
        run = db.get(Run, run_id)
        if not run:
            return {"status": "failed", "message": "运行不存在"}
        if run.status not in ("COMPLETED", "FAILED", "WAITING_APPROVAL", "REJECTED"):
            return {"status": "skipped", "message": f"运行尚未结束（{run.status}）"}
        if not force and cfg["sampling"] < 1.0 and random.random() > cfg["sampling"]:
            _bump(db, "langfuse_skipped")
            return {"status": "skipped", "message": f"采样未命中（{cfg['sampling']}）"}

        payload = _build_batch(db, run)
        ok, error = _post(cfg["host"], payload)
        _set(db, "langfuse_last_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        if ok:
            _bump(db, "langfuse_sent")
            _set(db, "langfuse_last_error", "")
            return {"status": "sent", "message": f"已上报 {len(payload['batch'])} 条事件"}
        _bump(db, "langfuse_failed")
        _set(db, "langfuse_last_error", error)
        return {"status": "failed", "message": error}
    finally:
        db.close()


def report_run_async(run_id: str) -> None:
    """非阻塞上报；关闭时直接返回。"""

    def worker():
        try:
            report_run(run_id)
        except Exception:
            pass

    threading.Thread(target=worker, daemon=True).start()


def send_test_event() -> dict:
    """发送一条测试 trace，用于验证连通性。"""
    db = SessionLocal()
    try:
        cfg = config_view(db)
        if not cfg["configured"]:
            return {"status": "failed", "message": "未配置公钥/私钥（AH_LANGFUSE_PUBLIC_KEY / AH_LANGFUSE_SECRET_KEY）"}
        trace_id = f"agenthub-test-{uuid.uuid4().hex[:8]}"
        now = _now_iso()
        payload = {"batch": [{
            "id": str(uuid.uuid4()), "type": "trace-create", "timestamp": now,
            "body": {"id": trace_id, "name": "agenthub connectivity test", "timestamp": now,
                     "tags": ["agenthub", "test"], "metadata": {"source": "console"}},
        }]}
        ok, error = _post(cfg["host"], payload)
        _set(db, "langfuse_last_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        if ok:
            _bump(db, "langfuse_sent")
            _set(db, "langfuse_last_error", "")
            return {"status": "sent", "message": f"测试事件已发送（trace {trace_id}）"}
        _bump(db, "langfuse_failed")
        _set(db, "langfuse_last_error", error)
        return {"status": "failed", "message": error}
    finally:
        db.close()
