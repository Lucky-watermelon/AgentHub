"""非智能体模块的改造回归：工具运行、知识库编辑、模型检查、监控统计与凭证生命周期。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agenthub.auth import DEMO_KEY
from agenthub.main import app

AUTH = {"Authorization": f"Bearer {DEMO_KEY}"}
PAYLOAD = {"credit": {"msg_no": "CR-M1", "total_limit": 1000000, "used_limit": 100000}}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


# ---------------- 工具 ----------------
def test_tool_jinja_runtime_and_validation(client):
    created = client.post("/api/v1/tools", json={
        "name": "测试报文拼装", "source": "自定义", "impl": "Jinja2",
        "endpoint": '{"no":"{{ params.customer_no }}"}',
        "params_schema": '{"type":"object","required":["customer_no"],'
                         '"properties":{"customer_no":{"type":"string"}}}',
    })
    assert created.status_code == 201
    tool_id = created.json()["id"]

    missing = client.post(f"/api/v1/tools/{tool_id}/invoke", json={"params": {}})
    assert missing.status_code == 422
    assert missing.json()["code"] == "AH-TOOL-422"

    ok = client.post(f"/api/v1/tools/{tool_id}/invoke",
                     json={"params": {"customer_no": "C-M1"}})
    assert ok.status_code == 200
    assert "C-M1" in ok.json()["result"]["rendered"]

    deleted = client.delete(f"/api/v1/tools/{tool_id}")
    assert deleted.status_code == 200


def test_http_tool_requires_endpoint_and_builtin_not_deletable(client):
    bad = client.post("/api/v1/tools", json={"name": "坏 HTTP 工具", "impl": "HTTP"})
    assert bad.status_code == 422

    rows = client.get("/api/v1/tools").json()
    builtin = next(t for t in rows if t["name"] == "征信报文解析")
    assert builtin["builtin"] is True
    assert builtin["refs"] > 0
    assert client.delete(f"/api/v1/tools/{builtin['id']}").status_code == 409


def test_tool_calls_filter(client):
    rows = client.get("/api/v1/tools/calls", params={"status": "失败", "limit": 20}).json()
    assert all(r["status"] == "失败" for r in rows)


# ---------------- 知识库 ----------------
def test_kb_create_edit_detail_and_vector_mode(client):
    created = client.post("/api/v1/kb/documents", json={
        "name": "模块改造测试口径.md", "kind": "口径", "note": "test",
        "content": "第一段：授信使用率超过 80% 需要提示。\n\n第二段：逾期 2 次进入关注名单。",
    })
    assert created.status_code == 201
    doc_id = created.json()["id"]

    detail = client.get(f"/api/v1/kb/documents/{doc_id}").json()
    assert "第一段" in detail["content"]
    assert detail["chars"] > 0

    updated = client.patch(f"/api/v1/kb/documents/{doc_id}", json={
        "content": "授信使用率 90% 需要重点提示，并核验还款来源。",
        "note": "已编辑",
    })
    assert updated.status_code == 200
    assert updated.json()["chunks"] >= 1
    assert "还款来源" in client.get(f"/api/v1/kb/documents/{doc_id}").json()["content"]

    hybrid = client.post("/api/v1/kb/retrieve",
                         json={"q": "授信使用率", "top_k": 2, "mode": "hybrid"}).json()
    assert hybrid["mode"] == "hybrid"
    assert hybrid["rows"][0]["doc_name"] == "模块改造测试口径.md"
    vector = client.post("/api/v1/kb/retrieve",
                         json={"q": "授信使用率", "top_k": 2, "mode": "vector"}).json()
    assert vector["mode"] == "vector"

    assert client.delete(f"/api/v1/kb/documents/{doc_id}").status_code == 200


def test_kb_rejects_empty_content(client):
    resp = client.post("/api/v1/kb/documents", json={"name": "空文档", "content": "  "})
    assert resp.status_code == 422
    assert resp.json()["code"] == "AH-KB-422"


# ---------------- 模型接入 ----------------
def test_provider_check_and_delete_guard(client):
    providers = client.get("/api/v1/providers").json()
    rule = next(p for p in providers if p["name"] == "rule")
    checked = client.post(f"/api/v1/providers/{rule['id']}/check")
    assert checked.status_code == 200
    assert checked.json()["ok"] is True
    assert "latency_ms" in checked.json()

    blocked = client.delete(f"/api/v1/providers/{rule['id']}")
    assert blocked.status_code == 409
    assert blocked.json()["code"] == "AH-MODEL-409"


def test_provider_rename_cascades_to_aliases(client):
    created_provider = client.post("/api/v1/providers", json={
        "name": "临时ProviderX", "kind": "本地", "base_url": "http://127.0.0.1:9/v1",
        "models": "demo",
    })
    assert created_provider.status_code == 201
    provider_id = created_provider.json()["id"]

    created_alias = client.post("/api/v1/aliases", json={
        "alias": "temp-alias-x", "provider_name": "临时ProviderX", "model": "demo",
    })
    assert created_alias.status_code == 201

    renamed = client.patch(f"/api/v1/providers/{provider_id}", json={"name": "临时ProviderY"})
    assert renamed.status_code == 200
    alias = next(a for a in client.get("/api/v1/aliases").json() if a["alias"] == "temp-alias-x")
    assert alias["provider"] == "临时ProviderY"

    assert client.delete("/api/v1/aliases/temp-alias-x").status_code == 200
    assert client.delete(f"/api/v1/providers/{provider_id}").status_code == 200


def test_alias_delete_guard_and_model_stats(client):
    chain = client.get("/api/v1/routing").json()["chain"]
    blocked = client.delete(f"/api/v1/aliases/{chain[0]}")
    assert blocked.status_code == 409

    stats = client.get("/api/v1/models/stats").json()
    assert "aliases" in stats and "providers" in stats
    aliases = client.get("/api/v1/aliases").json()
    assert any(a["in_chain"] for a in aliases)


# ---------------- 监控 ----------------
def test_trace_filters_and_stats_shape(client):
    completed = client.get("/api/v1/traces?status=COMPLETED&limit=5").json()
    assert all(t["status"] == "COMPLETED" for t in completed)

    stats = client.get("/api/v1/stats?days=7").json()
    assert stats["range_days"] == 7
    for key in ("p50", "p95", "terminal", "agents"):
        assert key in stats
    assert isinstance(stats["agents"], list)


def test_contract_is_generated_from_openapi(client):
    data = client.get("/api/v1/system/contract").json()
    assert data["count"] > 10
    paths = {row["path"] for row in data["endpoints"]}
    assert "/api/v1/invoke/{agent_id}" in paths
    assert "/v1/chat/completions" in paths


# ---------------- 凭证 ----------------
def test_expired_key_is_rejected(client):
    created = client.post("/api/v1/keys", json={"app": "过期测试", "type": "AppKey", "days": -1})
    assert created.status_code == 201
    body = created.json()
    assert body["expired"] is True

    resp = client.post("/api/v1/invoke/ag_001", headers={"Authorization": f"Bearer {body['plain']}"},
                       json={"payload": PAYLOAD, "mode": "async"})
    assert resp.status_code == 401
    assert resp.json()["code"] == "AH-AUTH-401"


def test_key_rotate_invalidates_old_plain(client):
    created = client.post("/api/v1/keys", json={"app": "轮换测试", "type": "AppKey", "days": 30})
    key_id, old_plain = created.json()["id"], created.json()["plain"]

    first = client.post("/api/v1/invoke/ag_001", headers={"Authorization": f"Bearer {old_plain}"},
                        json={"payload": PAYLOAD, "mode": "async"})
    assert first.status_code == 200

    rotated = client.post(f"/api/v1/keys/{key_id}/rotate", json={"days": 30})
    assert rotated.status_code == 200
    new_plain = rotated.json()["plain"]
    assert new_plain != old_plain

    stale = client.post("/api/v1/invoke/ag_001", headers={"Authorization": f"Bearer {old_plain}"},
                        json={"payload": PAYLOAD, "mode": "async"})
    assert stale.status_code == 401
    fresh = client.post("/api/v1/invoke/ag_001", headers={"Authorization": f"Bearer {new_plain}"},
                        json={"payload": PAYLOAD, "mode": "async"})
    assert fresh.status_code == 200

    revoked = client.post(f"/api/v1/keys/{key_id}/revoke")
    assert revoked.json()["status"] == "停用"


# ---------------- 技能 ----------------
def test_delete_agent_keeps_run_history(client):
    agent = client.post("/api/v1/agents", json={"name": "待删除智能体", "description": "delete me"})
    assert agent.status_code == 201
    agent_id = agent.json()["id"]

    run = client.post("/api/v1/runs", json={
        "agent_id": agent_id, "customer_no": "C-DELETE", "payload": PAYLOAD,
        "async_mode": False,
    })
    assert run.status_code == 201
    run_id = run.json()["id"]

    deleted = client.delete(f"/api/v1/agents/{agent_id}")
    assert deleted.status_code == 200
    assert deleted.json()["kept_runs"] >= 1

    assert client.get(f"/api/v1/agents/{agent_id}").status_code == 404
    assert client.get(f"/api/v1/runs/{run_id}").status_code == 200


def test_skill_publish_validates_alias_and_version_history(client):
    created = client.post("/api/v1/skills", json={
        "name": "别名校验技能", "tools": ["征信报文解析"], "model": "no-such-alias",
        "content": "# 别名校验技能\n\n## 步骤\n1. 解析征信报文。\n2. 输出结论。\n",
    })
    assert created.status_code == 201
    skill_id = created.json()["id"]

    blocked = client.post(f"/api/v1/skills/{skill_id}/publish")
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "AH-SKILL-422"
    assert "模型别名不存在" in blocked.json()["message"]

    fixed = client.patch(f"/api/v1/skills/{skill_id}", json={"model": "default-chat"})
    assert fixed.status_code == 200
    published = client.post(f"/api/v1/skills/{skill_id}/publish")
    assert published.status_code == 200
    assert published.json()["version"] == 2

    versions = client.get(f"/api/v1/skills/{skill_id}/versions").json()
    assert len(versions) >= 2
    assert client.delete(f"/api/v1/skills/{skill_id}").status_code == 409
