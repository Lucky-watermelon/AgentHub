"""迁移机制、幂等调用、HITL 断点续跑的测试。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect

from agenthub.auth import DEMO_KEY
from agenthub.db import SessionLocal, engine
from agenthub.main import app
from agenthub.migrations import applied_versions
from agenthub.models import ToolCall

AUTH = {"Authorization": f"Bearer {DEMO_KEY}"}
PAYLOAD = {"credit": {"total_limit": 8000000, "used_limit": 6200000, "overdue_30d": 1},
           "judicial": {"cases": 2, "executed": 0, "dishonest": 0}}
RISKY = {"credit": {"total_limit": 5000000, "used_limit": 4800000, "overdue_30d": 3},
         "judicial": {"cases": 3, "executed": 2, "dishonest": 1}}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_migrations_applied():
    versions = applied_versions(engine)
    assert 1 in versions and 2 in versions
    columns = {c["name"] for c in inspect(engine).get_columns("runs")}
    assert {"idempotency_key", "state_json", "node_progress"} <= columns


def test_idempotency_key_reuses_run(client):
    body = {"payload": PAYLOAD, "idempotency_key": "test-idem-001"}
    first = client.post("/api/v1/invoke/ag_001", headers=AUTH, json=body).json()
    second = client.post("/api/v1/invoke/ag_001", headers=AUTH, json=body).json()
    other = client.post("/api/v1/invoke/ag_001", headers=AUTH,
                        json={"payload": PAYLOAD, "idempotency_key": "test-idem-002"}).json()

    assert first["run_id"] == second["run_id"]
    assert second["idempotent_reuse"] is True
    assert other["run_id"] != first["run_id"]


def test_idempotency_header_supported(client):
    headers = {**AUTH, "Idempotency-Key": "test-idem-header"}
    first = client.post("/api/v1/invoke/ag_001", headers=headers, json={"payload": PAYLOAD}).json()
    second = client.post("/api/v1/invoke/ag_001", headers=headers, json={"payload": PAYLOAD}).json()
    assert first["run_id"] == second["run_id"]
    assert second["idempotent_reuse"] is True


def test_hitl_resume_skips_completed_nodes(client):
    run = client.post("/api/v1/invoke/ag_001", headers=AUTH,
                      json={"payload": RISKY}).json()
    assert run["status"] == "WAITING_APPROVAL"

    db = SessionLocal()
    try:
        before = db.query(ToolCall).filter(ToolCall.run_id == run["run_id"]).count()
    finally:
        db.close()

    detail = client.get(f"/api/v1/runs/{run['run_id']}").json()
    # 断点信息落库（通过 API 可见的状态为等待审批）
    assert detail["status"] == "WAITING_APPROVAL"

    approved = client.post(f"/api/v1/runs/{run['run_id']}/approve",
                           headers=AUTH, json={"decision": "APPROVED"}).json()
    assert approved["status"] == "COMPLETED"
    assert approved["report_path"]

    db = SessionLocal()
    try:
        after = db.query(ToolCall).filter(ToolCall.run_id == run["run_id"]).count()
    finally:
        db.close()

    # 中断前已完成 3 个解析工具 + 报告渲染在恢复后执行 → 只新增 1 次工具调用
    assert after - before == 1

    events = client.get(f"/api/v1/runs/{run['run_id']}").json()["events"]
    names = [e["event"] for e in events]
    assert "run.resumed" in names
    skipped = [e for e in events if e["event"] == "node.skipped"]
    assert len(skipped) >= 5  # 5 个已完成节点 + hitl 节点


def test_run_progress_persisted_on_interrupt(client):
    run = client.post("/api/v1/invoke/ag_001", headers=AUTH,
                      json={"payload": RISKY, "idempotency_key": "test-progress"}).json()
    from agenthub.models import Run

    db = SessionLocal()
    try:
        row = db.get(Run, run["run_id"])
        progress = json.loads(row.node_progress or "{}")
        assert progress.get("interrupted_at") == "n_hitl"
        assert "n_credit" in (progress.get("completed") or [])
        assert row.state_json  # 存在状态快照
    finally:
        db.close()


def test_executor_pool_reported(client):
    health = client.get("/api/v1/system/health").json()
    pool = health["run_pool"]
    assert pool["workers"] >= 1
    assert pool["queue_limit"] >= 1
    assert "running" in pool and "pending" in pool
