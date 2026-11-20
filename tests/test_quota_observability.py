"""配额限流与 Langfuse 旁路配置测试。"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from agenthub.auth import DEMO_KEY
from agenthub.main import app

AUTH = {"Authorization": f"Bearer {DEMO_KEY}"}
PAYLOAD = {"credit": {"msg_no": "CR-Q1", "total_limit": 1000000, "used_limit": 100000}}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _key_id(client) -> str:
    rows = client.get("/api/v1/quota", headers=AUTH).json()
    return rows[0]["key_id"]


def _set_quota(client, key_id: str, **kw) -> dict:
    resp = client.put(f"/api/v1/quota/{key_id}", headers=AUTH, json=kw)
    assert resp.status_code == 200
    return resp.json()


def test_quota_defaults(client):
    rows = client.get("/api/v1/quota", headers=AUTH).json()
    assert rows
    row = rows[0]
    assert row["rps"] > 0 and row["concurrency"] > 0
    assert row["daily_calls"] > 0
    assert "live" in row


def test_rps_limit_returns_429(client):
    key_id = _key_id(client)
    time.sleep(1.1)  # 清空上一个测试占用的滑动窗口
    _set_quota(client, key_id, rps=1, concurrency=0, daily_calls=0)
    client.post(f"/api/v1/quota/{key_id}/reset", headers=AUTH)
    try:
        codes = []
        for _ in range(3):
            resp = client.post("/api/v1/invoke/ag_001", headers=AUTH,
                               json={"payload": PAYLOAD, "mode": "async"})
            codes.append(resp.status_code)
        assert codes[0] == 200
        assert codes.count(429) >= 1
        last = client.post("/api/v1/invoke/ag_001", headers=AUTH,
                           json={"payload": PAYLOAD, "mode": "async"})
        assert last.status_code == 429
        assert last.json()["code"] == "AH-QUOTA-429"
        assert last.headers.get("Retry-After")
    finally:
        time.sleep(1.1)
        _set_quota(client, key_id, rps=10, concurrency=4, daily_calls=1000)
        client.post(f"/api/v1/quota/{key_id}/reset", headers=AUTH)


def test_daily_quota_limit(client):
    key_id = _key_id(client)
    client.post(f"/api/v1/quota/{key_id}/reset", headers=AUTH)
    _set_quota(client, key_id, rps=0, concurrency=0, daily_calls=1)
    try:
        first = client.post("/api/v1/invoke/ag_001", headers=AUTH,
                            json={"payload": PAYLOAD, "mode": "async"})
        assert first.status_code == 200
        second = client.post("/api/v1/invoke/ag_001", headers=AUTH,
                             json={"payload": PAYLOAD, "mode": "async"})
        assert second.status_code == 429
        assert "配额" in second.json()["message"]
    finally:
        _set_quota(client, key_id, rps=10, concurrency=4, daily_calls=1000)
        client.post(f"/api/v1/quota/{key_id}/reset", headers=AUTH)


def test_langfuse_config_roundtrip(client):
    before = client.get("/api/v1/observability/langfuse").json()
    assert "enabled" in before and "sampling" in before

    updated = client.post("/api/v1/observability/langfuse", json={"sampling": 0.5}).json()
    assert abs(updated["sampling"] - 0.5) < 1e-6

    # 还原
    client.post("/api/v1/observability/langfuse", json={"sampling": before["sampling"]})


def test_langfuse_test_endpoint_without_keys(client):
    resp = client.post("/api/v1/observability/langfuse/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] in ("failed", "sent")
    if body["status"] == "failed":
        assert "未配置" in body["message"] or "HTTP" in body["message"]
