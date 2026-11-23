"""并发压测脚本：同步运行 / 异步提交两种模式，输出吞吐与分位数。

用法::

    python scripts/bench.py --base-url http://127.0.0.1:8010 --mode sync  --concurrency 20 --total 60
    python scripts/bench.py --base-url http://127.0.0.1:8010 --mode async --concurrency 50 --total 300

说明：
- sync 模式等待每次运行跑完（端到端延迟，包含工具与模型节点）
- async 模式只统计提交响应（测量接入层吞吐与队列能力）
- 压测实例建议用 AH_LLM_MOCK=true，避免真实模型费用干扰平台自身开销
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from pathlib import Path

import httpx

PAYLOAD = {
    "customer_no": "C-BENCH",
    "payload": {
        "credit": {"total_limit": 8000000, "used_limit": 6200000,
                   "overdue_30d": 1, "query_3m": 11},
        "finance": {"revenue": 126000000, "net_profit": -3200000,
                    "total_assets": 210000000, "total_liability": 168000000},
        "judicial": {"cases": 1, "executed": 0, "dishonest": 0},
    },
}


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((p / 100) * (len(ordered) - 1))))
    return round(ordered[idx], 1)


async def one(client: httpx.AsyncClient, url: str, mode: str, headers: dict) -> tuple[int, float]:
    body = dict(PAYLOAD)
    body["mode"] = mode
    started = time.perf_counter()
    try:
        resp = await client.post(url, json=body, headers=headers)
        return resp.status_code, (time.perf_counter() - started) * 1000
    except Exception:
        return -1, (time.perf_counter() - started) * 1000


async def run(args) -> dict:
    url = f"{args.base_url.rstrip('/')}/api/v1/invoke/{args.agent_id}"
    headers = {"Content-Type": "application/json"}
    if args.key:
        headers["Authorization"] = f"Bearer {args.key}"

    sem = asyncio.Semaphore(args.concurrency)
    results: list[tuple[int, float]] = []

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(args.timeout),
        limits=httpx.Limits(max_connections=args.concurrency * 2,
                            max_keepalive_connections=args.concurrency),
    ) as client:
        async def worker():
            async with sem:
                results.append(await one(client, url, args.mode, headers))

        started = time.perf_counter()
        await asyncio.gather(*[worker() for _ in range(args.total)])
        wall = time.perf_counter() - started

    codes = Counter(code for code, _ in results)
    latencies = [ms for code, ms in results if code in (200, 201)]
    ok = sum(v for k, v in codes.items() if k in (200, 201))
    report = {
        "mode": args.mode,
        "concurrency": args.concurrency,
        "total": args.total,
        "wall_s": round(wall, 2),
        "qps": round(args.total / wall, 2) if wall else 0,
        "ok": ok,
        "failed": args.total - ok,
        "status_codes": dict(codes),
        "latency_ms": {
            "avg": round(sum(latencies) / len(latencies), 1) if latencies else 0,
            "p50": pct(latencies, 50),
            "p95": pct(latencies, 95),
            "p99": pct(latencies, 99),
            "max": round(max(latencies), 1) if latencies else 0,
        },
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="AgentHub 并发压测")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--mode", choices=["sync", "async"], default="sync")
    parser.add_argument("--agent-id", default="ag_001")
    parser.add_argument("--concurrency", type=int, default=10)
    parser.add_argument("--total", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--key", default="ah_pat_demo_local_dev")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    report = asyncio.run(run(args))
    line = json.dumps(report, ensure_ascii=False)
    print(line)
    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(line + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
