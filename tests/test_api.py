"""接口与运行链路的集成测试（使用临时数据目录）。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agenthub.main import app
from agenthub.seed import SAMPLE_DAG

PAYLOAD = {
    "credit": {"msg_no": "CR-T1", "total_limit": 8000000, "used_limit": 6200000,
               "overdue_30d": 1, "query_3m": 11},
    "finance": {"msg_no": "FR-T1", "revenue": 126000000, "net_profit": -3200000,
                "total_assets": 210000000, "total_liability": 168000000},
    "judicial": {"msg_no": "JD-T1", "cases": 2, "executed": 0, "dishonest": 0},
}

RISKY = {
    "credit": {"msg_no": "CR-T2", "total_limit": 5000000, "used_limit": 4800000,
               "overdue_30d": 3, "query_3m": 14},
    "finance": {"msg_no": "FR-T2", "revenue": 62000000, "net_profit": -8600000,
                "total_assets": 120000000, "total_liability": 108000000},
    "judicial": {"msg_no": "JD-T2", "cases": 3, "executed": 2, "dishonest": 1},
}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_seed_agents(client):
    resp = client.get("/api/v1/agents")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) >= 1
    assert rows[0]["dag_ok"] is True
    assert rows[0]["node_count"] == 10


def test_dag_structure(client):
    resp = client.get("/api/v1/agents/ag_001/dag")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["structure"]["nodes"]) == 10
    assert data["errors"] == []


def test_dag_invalid_returns_422(client):
    bad = {"entry": "a", "nodes": [
        {"id": "a", "type": "start", "name": "A", "next": ["b"]},
        {"id": "b", "type": "tool", "name": "B", "next": ["a"]},
    ]}
    resp = client.post("/api/v1/agents/ag_001/dag/validate", json={"dag": bad})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert any("环路" in e for e in body["errors"])

    resp2 = client.post("/api/v1/agents/ag_001/dag", json={"dag": bad})
    assert resp2.status_code == 422
    assert resp2.json()["code"] == "AH-RUNTIME-422"

    # 保持原 DAG 不变
    assert client.get("/api/v1/agents/ag_001/dag").json()["errors"] == []


def test_run_completes_and_reports(client):
    resp = client.post("/api/v1/runs", json={
        "agent_id": "ag_001", "customer_no": "C-TEST-001",
        "payload": PAYLOAD, "async_mode": False,
    })
    assert resp.status_code == 201
    run = resp.json()
    assert run["status"] == "COMPLETED"
    assert run["report_path"].startswith("reports/")
    assert run["tokens"] > 0

    detail = client.get(f"/api/v1/runs/{run['id']}").json()
    assert len(detail["events"]) > 5
    assert len(detail["checkpoints"]) >= 5
    assert "授信使用率" in detail["output"]["summary"]

    trace = client.get(f"/api/v1/traces/{run['trace_id']}").json()
    assert len(trace["spans"]) > 3


def test_high_risk_needs_approval(client):
    resp = client.post("/api/v1/runs", json={
        "agent_id": "ag_001", "customer_no": "C-TEST-002",
        "payload": RISKY, "async_mode": False,
    })
    assert resp.status_code == 201
    run = resp.json()
    assert run["status"] == "WAITING_APPROVAL"

    approved = client.post(f"/api/v1/runs/{run['id']}/approve",
                           json={"decision": "APPROVED", "comment": "test"})
    assert approved.status_code == 200
    assert approved.json()["status"] == "COMPLETED"
    assert approved.json()["report_path"]


def test_kb_retrieve(client):
    resp = client.post("/api/v1/kb/retrieve", json={"q": "授信使用率 逾期", "top_k": 3})
    assert resp.status_code == 200
    rows = resp.json()["rows"]
    assert len(rows) == 3
    assert rows[0]["score"] >= rows[-1]["score"]


def test_skill_publish_validates_tools(client):
    created = client.post("/api/v1/skills", json={
        "name": "测试技能", "tools": ["不存在的工具"], "prompt": "t@v1",
    })
    assert created.status_code == 201
    skill_id = created.json()["id"]
    pub = client.post(f"/api/v1/skills/{skill_id}/publish")
    assert pub.status_code == 422
    assert pub.json()["code"] == "AH-SKILL-422"


def test_stats_and_tools(client):
    stats = client.get("/api/v1/stats").json()
    assert stats["runs"] >= 2
    assert "tools" in stats
    tools = client.get("/api/v1/tools").json()
    assert any(t["name"] == "征信报文解析" for t in tools)


def test_create_agent_with_default_dag(client):
    resp = client.post("/api/v1/agents", json={"name": "接口测试智能体", "description": "auto"})
    assert resp.status_code == 201
    agent = resp.json()
    assert agent["version"] == 1
    detail = client.get(f"/api/v1/agents/{agent['id']}/dag").json()
    assert len(detail["structure"]["nodes"]) == len(SAMPLE_DAG["nodes"])
    assert json.dumps(detail["dag"], ensure_ascii=False)
