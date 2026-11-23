"""多 worker 部署验证：启动 2 个 uvicorn worker，检查并发初始化与提交吞吐。

用法::

    python scripts/multiworker_check.py

验证点：
1. 多个 worker 同时 startup 时，示例数据初始化不冲突（进程级 seed 锁）
2. 并发提交运行不因多进程而失败
3. 说明：限流窗口与运行线程池是进程内状态，多副本时各算各的（需换 Redis）
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

PORT = 8011
BASE = f"http://127.0.0.1:{PORT}"


def wait_health(timeout: int = 40) -> bool:
    for _ in range(timeout):
        time.sleep(1)
        try:
            resp = httpx.get(f"{BASE}/health", timeout=2)
            if resp.status_code == 200:
                return True
        except Exception:
            continue
    return False


async def burst(total: int = 100, concurrency: int = 20) -> dict:
    payload = {
        "customer_no": "C-MULTI",
        "mode": "async",
        "payload": {"credit": {"total_limit": 8000000, "used_limit": 6200000}},
    }
    sem = asyncio.Semaphore(concurrency)
    codes: list[tuple[int, str]] = []

    async with httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=concurrency * 2)) as client:
        async def worker():
            async with sem:
                try:
                    resp = await client.post(f"{BASE}/api/v1/invoke/ag_001", json=payload)
                    code = ""
                    if resp.status_code >= 400:
                        try:
                            code = resp.json().get("code", "")
                        except Exception:
                            code = ""
                    codes.append((resp.status_code, code))
                except Exception:
                    codes.append((-1, "EXC"))

        started = time.perf_counter()
        await asyncio.gather(*[worker() for _ in range(total)])
        wall = time.perf_counter() - started
    ok = sum(1 for status, _ in codes if status in (200, 201))
    buckets: dict[str, int] = {}
    for status, code in codes:
        key = f"{status}:{code}" if code else str(status)
        buckets[key] = buckets.get(key, 0) + 1
    return {"total": total, "concurrency": concurrency, "ok": ok,
            "wall_s": round(wall, 2), "qps": round(total / wall, 2),
            "codes": buckets}


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    env.update({"AH_DATA_DIR": "data_multi", "AH_REPORTS_DIR": "reports_multi",
                "AH_LLM_MOCK": "true", "AH_RUN_WORKERS": "4"})

    log_path = root / "data_multi_server.log"
    log = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "agenthub.main:app", "--host", "127.0.0.1",
         "--port", str(PORT), "--workers", "2", "--no-access-log"],
        cwd=str(root), env=env, stdout=log, stderr=subprocess.STDOUT, text=True,
    )
    try:
        up = wait_health()
        agents = 0
        if up:
            try:
                agents = len(httpx.get(f"{BASE}/api/v1/agents", timeout=5).json())
            except Exception:
                agents = -1
        result = {"workers": 2, "started": up, "agents": agents}
        if up:
            # 默认配额 10rps/进程；先记录受限表现，再放开配额测真实提交吞吐
            try:
                quotas = httpx.get(f"{BASE}/api/v1/quota", timeout=5).json()
                kid = quotas[0]["key_id"]
                result["burst_with_quota"] = asyncio.run(burst(total=60, concurrency=20))
                httpx.put(f"{BASE}/api/v1/quota/{kid}",
                          json={"rps": 0, "concurrency": 0, "daily_calls": 0}, timeout=5)
                time.sleep(1.2)
                result["burst_no_quota"] = asyncio.run(burst())
            except Exception as exc:
                result["burst_error"] = str(exc)[:120]
        print(json.dumps(result, ensure_ascii=False))
        out = root / "bench_results" / "multiworker.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


if __name__ == "__main__":
    main()
