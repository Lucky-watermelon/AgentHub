"""故障演练脚本：模型不可用、工具异常、数据库锁竞争。

用法::

    python scripts/fault_test.py --base-url http://127.0.0.1:8010

设计：每个场景尽量「可回滚」——改配置 → 触发运行 → 校验行为 → 还原配置。
进程被 kill 的场景在文档中手工记录（需外部操作）。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
import time
from pathlib import Path

import httpx

PAYLOAD = {
    "customer_no": "C-FAULT",
    "payload": {
        "credit": {"total_limit": 8000000, "used_limit": 6200000,
                   "overdue_30d": 1, "query_3m": 11},
        "judicial": {"cases": 1, "executed": 0, "dishonest": 0},
    },
}

RESULTS: list[dict] = []


def record(scene: str, passed: bool, detail: str) -> None:
    RESULTS.append({"scene": scene, "passed": passed, "detail": detail})
    print(f"[{'PASS' if passed else 'FAIL'}] {scene} — {detail}")


def run_once(client: httpx.Client, base: str, headers: dict, extra: dict | None = None) -> dict:
    body = dict(PAYLOAD)
    if extra:
        body.update(extra)
    resp = client.post(f"{base}/api/v1/invoke/ag_001", json=body, headers=headers, timeout=120)
    if resp.status_code >= 500:
        try:
            payload = resp.json()
        except Exception:
            payload = {"message": resp.text[:160]}
        return {"run_id": "", "status": f"HTTP_{resp.status_code}",
                "summary": "", "error": payload.get("message", ""), "payload": payload}
    return resp.json()


def scene_provider_down(client: httpx.Client, base: str, headers: dict) -> None:
    """模型 Provider 不可用 → 应以 degraded 文本完成，而不是整条运行失败。"""
    providers = client.get(f"{base}/api/v1/providers", headers=headers).json()
    target = next(p for p in providers if p["name"] == "DeepSeek")
    original = {"base_url": target["url"], "status": target["status"]}
    try:
        client.patch(f"{base}/api/v1/providers/{target['id']}", headers=headers,
                     json={"base_url": "http://127.0.0.1:9/v1"})
        result = run_once(client, base, headers)
        detail = f"status={result['status']} summary_len={len(result.get('summary') or '')}"
        record("模型不可用（连接拒绝）", result["status"] == "COMPLETED", detail)
    finally:
        client.patch(f"{base}/api/v1/providers/{target['id']}", headers=headers, json=original)


def scene_tool_missing(client: httpx.Client, base: str, headers: dict) -> None:
    """DAG 引用未注册工具 → 运行应 FAILED 且事件含 tool.result FAIL，而非静默成功。"""
    dag = client.get(f"{base}/api/v1/agents/ag_001/dag", headers=headers).json()["dag"]
    original = dag
    broken = json.loads(json.dumps(dag))
    for node in broken["nodes"]:
        if node.get("type") == "tool" and node.get("tool") == "征信报文解析":
            node["tool"] = "不存在的工具"
            break
    try:
        client.post(f"{base}/api/v1/agents/ag_001/dag", headers=headers,
                    json={"dag": broken, "publish": False})
        result = run_once(client, base, headers)
        run = client.get(f"{base}/api/v1/runs/{result['run_id']}", headers=headers).json()
        events = [e["event"] for e in run["events"]]
        detail = f"status={run['status']} has_fail_event={'tool.result' in events} error={run['error'][:40]}"
        record("工具未注册", run["status"] == "FAILED" and "tool.result" in events, detail)
    finally:
        client.post(f"{base}/api/v1/agents/ag_001/dag", headers=headers,
                    json={"dag": original, "publish": False})


def scene_db_lock(db_path: Path, client: httpx.Client, base: str, headers: dict) -> None:
    """外部长事务持锁 → 写入应等待 busy_timeout 而非立即失败。"""
    stop = threading.Event()

    def holder():
        conn = sqlite3.connect(db_path)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE runs SET error = error WHERE id = 'no-such-run'")
        while not stop.is_set():
            time.sleep(0.05)
        conn.rollback()
        conn.close()

    thread = threading.Thread(target=holder, daemon=True)
    thread.start()
    time.sleep(0.5)
    started = time.perf_counter()
    try:
        result = run_once(client, base, headers)
        elapsed = time.perf_counter() - started
        code = (result.get("payload") or {}).get("code", "")
        # 期望：要么等到锁释放后完成，要么快速返回可重试的 AH-DB-503，而不是裸 500
        ok = result["status"] in ("COMPLETED", "WAITING_APPROVAL") or code == "AH-DB-503"
        detail = f"status={result['status']} code={code or '-'} elapsed={elapsed:.2f}s"
        record("数据库写锁竞争", ok, detail)
    except Exception as exc:
        elapsed = time.perf_counter() - started
        record("数据库写锁竞争", False, f"{type(exc).__name__} after {elapsed:.2f}s: {exc}")
    finally:
        stop.set()
        thread.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentHub 故障演练")
    parser.add_argument("--base-url", default="http://127.0.0.1:8010")
    parser.add_argument("--db", default="data_bench/agenthub.db")
    parser.add_argument("--key", default="ah_pat_demo_local_dev")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {args.key}"}
    db_path = Path(args.db)

    with httpx.Client() as client:
        scene_provider_down(client, base, headers)
        scene_tool_missing(client, base, headers)
        if db_path.exists():
            scene_db_lock(db_path, client, base, headers)
        else:
            record("数据库写锁竞争", False, f"数据库不存在：{db_path}")

    passed = sum(1 for r in RESULTS if r["passed"])
    summary = {"total": len(RESULTS), "passed": passed, "results": RESULTS}
    print(json.dumps(summary, ensure_ascii=False))
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
