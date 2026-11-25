"""DAG 依赖门禁、知识库工具与 skill 节点的集成测试。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agenthub.main import app

PAYLOAD = {
    "credit": {"msg_no": "CR-G1", "total_limit": 8000000, "used_limit": 6200000,
               "overdue_30d": 1, "query_3m": 11},
    "finance": {"msg_no": "FR-G1", "revenue": 126000000, "net_profit": -3200000,
                "total_assets": 210000000, "total_liability": 168000000},
    "judicial": {"msg_no": "JD-G1", "cases": 2, "executed": 0, "dishonest": 0},
}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _tool(client, name: str) -> dict:
    rows = client.get("/api/v1/tools").json()
    return next(t for t in rows if t["name"] == name)


def _dag(nodes: list[dict]) -> dict:
    return {"name": "门禁测试流程", "version": "v1", "entry": nodes[0]["id"], "nodes": nodes}


# ---------------- 知识库工具 ----------------
def test_kb_tool_registered_and_callable(client):
    tool = _tool(client, "知识库检索")
    assert tool["builtin"] is True

    ok = client.post(f"/api/v1/tools/{tool['id']}/invoke",
                     json={"params": {"q": "授信使用率", "top_k": 2}})
    assert ok.status_code == 200
    rows = ok.json()["result"]["features"]["kb"]["rows"]
    assert rows and rows[0]["doc_name"]

    missing = client.post(f"/api/v1/tools/{tool['id']}/invoke", json={"params": {}})
    assert missing.status_code == 422
    assert missing.json()["code"] == "AH-TOOL-422"


def test_disabled_builtin_tool_blocks_execution(client):
    tool = _tool(client, "计算器")
    assert client.patch(f"/api/v1/tools/{tool['id']}", json={"status": "停用"}).status_code == 200
    try:
        resp = client.post(f"/api/v1/tools/{tool['id']}/invoke", json={"params": {"expr": "1+1"}})
        assert resp.status_code == 409
        assert resp.json()["code"] == "AH-TOOL-409"
    finally:
        client.patch(f"/api/v1/tools/{tool['id']}", json={"status": "启用"})


# ---------------- 依赖门禁 ----------------
def test_dag_gate_rejects_missing_tool_and_alias(client):
    agent = client.post("/api/v1/agents", json={"name": "门禁临时智能体"}).json()

    bad_tool = _dag([
        {"id": "s", "type": "start", "name": "开始", "next": ["t"]},
        {"id": "t", "type": "tool", "name": "不存在的工具", "tool": "不存在的工具", "next": ["e"]},
        {"id": "e", "type": "end", "name": "结束"},
    ])
    checked = client.post(f"/api/v1/agents/{agent['id']}/dag/validate", json={"dag": bad_tool})
    assert checked.status_code == 200
    body = checked.json()
    assert body["ok"] is False
    assert any("工具不存在" in e for e in body["dependency_errors"])

    saved = client.post(f"/api/v1/agents/{agent['id']}/dag", json={"dag": bad_tool})
    assert saved.status_code == 422
    assert saved.json()["code"] == "AH-RUNTIME-422"

    bad_model = _dag([
        {"id": "s", "type": "start", "name": "开始", "next": ["l"]},
        {"id": "l", "type": "llm", "name": "判读", "model": "no-such-alias", "next": ["e"]},
        {"id": "e", "type": "end", "name": "结束"},
    ])
    model_resp = client.post(f"/api/v1/agents/{agent['id']}/dag", json={"dag": bad_model})
    assert model_resp.status_code == 422
    assert "模型别名不存在" in model_resp.json()["message"]


def test_disabled_tool_blocks_dag_save_and_run(client):
    tool = _tool(client, "司法风险标签")
    agent = client.post("/api/v1/agents", json={"name": "停用工具门禁"}).json()
    dag = _dag([
        {"id": "s", "type": "start", "name": "开始", "next": ["t"]},
        {"id": "t", "type": "tool", "name": tool["name"], "tool": tool["name"], "next": ["e"]},
        {"id": "e", "type": "end", "name": "结束"},
    ])
    assert client.post(f"/api/v1/agents/{agent['id']}/dag", json={"dag": dag}).status_code == 200

    client.patch(f"/api/v1/tools/{tool['id']}", json={"status": "停用"})
    try:
        blocked = client.post("/api/v1/runs", json={
            "agent_id": agent["id"], "customer_no": "C-GATE", "payload": PAYLOAD,
            "async_mode": False,
        })
        assert blocked.status_code == 422
        assert "工具已停用" in blocked.json()["message"]
    finally:
        client.patch(f"/api/v1/tools/{tool['id']}", json={"status": "启用"})

    resumed = client.post("/api/v1/runs", json={
        "agent_id": agent["id"], "customer_no": "C-GATE", "payload": PAYLOAD,
        "async_mode": False,
    })
    assert resumed.status_code == 201
    assert resumed.json()["status"] == "COMPLETED"


# ---------------- skill 节点 ----------------
def test_skill_node_runs_bound_tools_and_model(client):
    dag = _dag([
        {"id": "s", "type": "start", "name": "开始", "next": ["k"]},
        {"id": "k", "type": "skill", "name": "技能判读", "skill": "客户全景分析技能",
         "next": ["r"]},
        {"id": "r", "type": "tool", "name": "报告渲染", "tool": "HTML 报告渲染", "next": ["e"]},
        {"id": "e", "type": "end", "name": "结束"},
    ])
    agent = client.post("/api/v1/agents", json={
        "name": "技能节点智能体", "description": "skill node", "dag": dag,
    })
    assert agent.status_code == 201
    agent_id = agent.json()["id"]

    run = client.post("/api/v1/runs", json={
        "agent_id": agent_id, "customer_no": "C-SKILL", "payload": PAYLOAD,
        "async_mode": False,
    })
    assert run.status_code == 201
    body = run.json()
    assert body["status"] == "COMPLETED"
    assert body["report_path"]

    detail = client.get(f"/api/v1/runs/{body['id']}").json()
    assert detail["output"]["findings"]["credit"]["usage_rate"] > 0
    assert "综合判断" in detail["output"]["summary"]

    calls = client.get("/api/v1/tools/calls", params={"run_id": body["id"]}).json()
    names = {c["tool"] for c in calls}
    assert {"征信报文解析", "财报指标计算", "司法风险标签"} <= names


def test_skill_detail_exposes_skill_md_and_code(client):
    skill = client.get("/api/v1/skills/sk_02").json()
    assert skill["content"].startswith("---")
    assert "# 征信特征提取" in skill["content"]
    assert skill["scripts"][0]["name"] == "usage_rate.py"
    assert "emit(" in skill["scripts"][0]["code"]
    assert skill["entrypoint"] == "usage_rate.py"

    empty = client.post("/api/v1/skills", json={"name": "空内容技能", "model": "default-chat"})
    assert empty.status_code == 201
    blocked = client.post(f"/api/v1/skills/{empty.json()['id']}/publish")
    assert blocked.status_code == 422
    assert "SKILL.md" in blocked.json()["message"]


def test_skill_scripts_sandbox_and_publish_gate(client):
    """SKILL.md 内容与脚本：沙箱可运行、危险代码在发布时被拒。"""
    ok = client.post("/api/v1/skills", json={
        "name": "沙箱验证技能", "model": "default-chat", "tools": ["计算器"],
        "content": "# 沙箱验证\n\n运行脚本计算 1+1。\n",
        "scripts": [{"name": "main.py", "language": "python",
                     "code": 'emit({"features": {"calc": {"value": 2}}})'}],
        "entrypoint": "main.py",
    })
    assert ok.status_code == 201
    skill_id = ok.json()["id"]
    assert ok.json()["scripts"][0]["name"] == "main.py"

    tested = client.post(f"/api/v1/skills/{skill_id}/test",
                         json={"input": {"payload": {"x": 1}}})
    assert tested.status_code == 200
    assert tested.json()["ok"] is True
    assert tested.json()["result"]["features"]["calc"]["value"] == 2

    bad = client.post("/api/v1/skills", json={
        "name": "危险脚本技能", "model": "default-chat",
        "content": "# 危险脚本\n",
        "scripts": [{"name": "bad.py", "language": "python",
                     "code": "import os\nos.system('whoami')"}],
        "entrypoint": "bad.py",
    })
    assert bad.status_code == 201
    blocked = client.post(f"/api/v1/skills/{bad.json()['id']}/publish")
    assert blocked.status_code == 422
    assert "禁止导入" in blocked.json()["message"]

    assert client.post(f"/api/v1/skills/{skill_id}/publish").status_code == 200


def test_skill_node_runs_bundled_script(client):
    dag = _dag([
        {"id": "s", "type": "start", "name": "开始", "next": ["k"]},
        {"id": "k", "type": "skill", "name": "征信特征提取", "skill": "征信特征提取",
         "next": ["e"]},
        {"id": "e", "type": "end", "name": "结束"},
    ])
    agent = client.post("/api/v1/agents", json={"name": "脚本技能智能体", "dag": dag})
    assert agent.status_code == 201
    credit_payload = {"credit": {"msg_no": "CR-S1", "total_limit": 8000000,
                                 "used_limit": 7500000, "overdue_30d": 1, "query_3m": 11}}
    run = client.post("/api/v1/runs", json={
        "agent_id": agent.json()["id"], "customer_no": "C-SCRIPT",
        "payload": credit_payload, "async_mode": False,
    })
    assert run.status_code == 201
    assert run.json()["status"] == "COMPLETED"

    detail = client.get(f"/api/v1/runs/{run.json()['id']}").json()
    assert detail["output"]["findings"]["credit"]["risk_band"] == "关注"
    assert detail["output"]["findings"]["credit"]["script_reasons"]

    calls = client.get("/api/v1/tools/calls",
                       params={"run_id": run.json()["id"]}).json()
    assert any(c["tool"] == "技能脚本:征信特征提取" and c["status"] == "成功" for c in calls)


def test_skill_node_missing_skill_rejected(client):
    dag = _dag([
        {"id": "s", "type": "start", "name": "开始", "next": ["k"]},
        {"id": "k", "type": "skill", "name": "缺失技能", "skill": "不存在的技能", "next": ["e"]},
        {"id": "e", "type": "end", "name": "结束"},
    ])
    resp = client.post("/api/v1/agents", json={"name": "缺失技能智能体", "dag": dag})
    assert resp.status_code == 422
    assert "技能不存在" in resp.json()["message"]


def test_kb_tool_node_in_dag(client):
    dag = _dag([
        {"id": "s", "type": "start", "name": "开始", "next": ["t"]},
        {"id": "t", "type": "tool", "name": "口径检索", "tool": "知识库检索",
         "params": {"q": "授信使用率 口径", "top_k": 2}, "next": ["e"]},
        {"id": "e", "type": "end", "name": "结束"},
    ])
    agent = client.post("/api/v1/agents", json={"name": "知识库检索智能体", "dag": dag})
    assert agent.status_code == 201
    run = client.post("/api/v1/runs", json={
        "agent_id": agent.json()["id"], "customer_no": "C-KB", "payload": {},
        "async_mode": False,
    })
    assert run.status_code == 201
    detail = client.get(f"/api/v1/runs/{run.json()['id']}").json()
    assert detail["status"] == "COMPLETED"
    assert detail["output"]["findings"]["kb"]["rows"]
