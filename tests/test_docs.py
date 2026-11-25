"""OpenAPI 文档增强与控制台契约数据测试。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agenthub.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_openapi_has_chinese_summaries_and_auth(client):
    spec = client.get("/openapi.json").json()
    invoke = spec["paths"]["/api/v1/invoke/{agent_id}"]["post"]
    assert invoke["summary"] == "调用智能体（sync / async）"
    assert "idempotency_key" in invoke["description"]
    assert invoke["requestBody"]["content"]["application/json"]["examples"]

    schemes = spec["components"]["securitySchemes"]
    assert schemes["AppKey"]["scheme"] == "bearer"
    assert schemes["AppKeyQuery"]["in"] == "query"
    assert spec["security"]

    # health 不要求鉴权
    assert spec["paths"]["/health"]["get"]["security"] == []

    # 不应残留 FastAPI 默认英文 summary
    prefixes = ("Get ", "List ", "Create ", "Update ", "Delete ", "Set ",
                "Test ", "Check ", "Rotate ", "Revoke ", "Reindex ")
    for path, item in spec["paths"].items():
        for method, op in item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            assert op.get("summary"), f"{method} {path} 缺少 summary"
            assert not op["summary"].startswith(prefixes), f"{method} {path} 仍是英文默认摘要"


def test_contract_detail_fields(client):
    contract = client.get("/api/v1/system/contract").json()
    assert contract["count"] > 50
    assert contract["base_url"].startswith("http://")

    invoke = next(r for r in contract["endpoints"]
                  if r["method"] == "POST" and r["path"] == "/api/v1/invoke/{agent_id}")
    assert invoke["summary"] == "调用智能体（sync / async）"
    assert invoke["tag"] == "对外调用"
    assert invoke["auth"] == "AppKey"
    assert invoke["testable"] is True
    assert any(p["name"] == "agent_id" and p["in"] == "path" for p in invoke["params"])
    assert invoke["request_example"]["payload"]
    assert invoke["response_example"]["run_id"]
    assert "curl -X POST" in invoke["curl"]
    assert "ag_001" in invoke["curl"]

    sse = next(r for r in contract["endpoints"]
               if r["path"] == "/api/v1/runs/{run_id}/events")
    assert sse["testable"] is False

    delete_tool = next(r for r in contract["endpoints"]
                       if r["method"] == "DELETE" and r["path"] == "/api/v1/tools/{tool_id}")
    assert delete_tool["destructive"] is True


def test_docs_page_available(client):
    resp = client.get("/docs")
    assert resp.status_code == 200
    assert "swagger" in resp.text.lower()
