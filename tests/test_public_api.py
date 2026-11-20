"""对外调用接口测试：原生 invoke、OpenAI 兼容、鉴权开关。"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from agenthub.auth import DEMO_KEY
from agenthub.config import settings
from agenthub.main import app

PAYLOAD = {
    "credit": {"msg_no": "CR-P1", "total_limit": 8000000, "used_limit": 6200000,
               "overdue_30d": 1, "query_3m": 11},
    "finance": {"msg_no": "FR-P1", "revenue": 126000000, "net_profit": -3200000,
                "total_assets": 210000000, "total_liability": 168000000},
    "judicial": {"msg_no": "JD-P1", "cases": 2, "executed": 0, "dishonest": 0},
}

AUTH = {"Authorization": f"Bearer {DEMO_KEY}"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_callable_agents(client):
    rows = client.get("/api/v1/invoke/agents").json()
    assert rows
    assert rows[0]["callable"] is True
    assert rows[0]["endpoint"].startswith("/api/v1/invoke/")


def test_invoke_sync(client):
    resp = client.post("/api/v1/invoke/ag_001", json={
        "customer_no": "C-PUB-001", "payload": PAYLOAD, "mode": "sync",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "COMPLETED"
    assert data["report_url"].endswith(".html")
    assert "授信使用率" in data["summary"]
    assert data["usage"]["tokens"] > 0
    assert data["links"]["events"].endswith("/events")


def test_invoke_with_key_attribution(client):
    resp = client.post("/api/v1/invoke/ag_001", json={"payload": PAYLOAD}, headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["caller"].startswith("本地调试/")


def test_invoke_flat_messages(client):
    """支持把 credit / finance / judicial 平铺在请求体里。"""
    resp = client.post("/api/v1/invoke/ag_001", json={
        "customer_no": "C-PUB-002", "credit": PAYLOAD["credit"],
    })
    assert resp.status_code == 200
    assert resp.json()["status"] in ("COMPLETED", "WAITING_APPROVAL")


def test_invoke_empty_payload_rejected(client):
    resp = client.post("/api/v1/invoke/ag_001", json={"payload": {}})
    assert resp.status_code == 422
    assert resp.json()["code"] == "AH-INVOKE-422"


def test_invoke_unknown_agent(client):
    resp = client.post("/api/v1/invoke/ag_not_exist", json={"payload": PAYLOAD})
    assert resp.status_code == 404


def test_compat_models(client):
    data = client.get("/v1/models").json()
    assert data["object"] == "list"
    assert any(m["id"] == "ag_001" for m in data["data"])


def test_compat_chat_completion(client):
    resp = client.post("/v1/chat/completions", json={
        "model": "ag_001",
        "messages": [{"role": "user", "content": json.dumps(PAYLOAD)}],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["role"] == "assistant"
    assert "授信使用率" in body["choices"][0]["message"]["content"]
    assert body["choices"][0]["metadata"]["run_id"]


def test_compat_stream(client):
    resp = client.post("/v1/chat/completions", json={
        "model": "ag_001", "stream": True,
        "messages": [{"role": "user", "content": json.dumps(PAYLOAD)}],
    })
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    text = resp.text
    assert text.count("data: ") >= 2
    assert "[DONE]" in text
    assert "chat.completion.chunk" in text


def test_auth_required(client):
    original = settings.auth_required
    settings.auth_required = True
    try:
        denied = client.post("/api/v1/invoke/ag_001", json={"payload": PAYLOAD})
        assert denied.status_code == 401
        assert denied.json()["code"] == "AH-AUTH-401"

        allowed = client.post("/api/v1/invoke/ag_001", json={"payload": PAYLOAD}, headers=AUTH)
        assert allowed.status_code == 200

        # SSE 场景可以用 query 传凭证
        query_auth = client.get(f"/api/v1/runs/any/events?access_token={DEMO_KEY}")
        assert query_auth.status_code != 401
    finally:
        settings.auth_required = original
