"""模型接入：别名解析、Provider 调用、降级链。

- 别名（alias）是业务侧唯一引用入口；Provider 统一走 OpenAI 兼容协议。
- ``llm_mock=true``（默认）时不真正出网，用规则化文本兜底，保证离线可跑通。
"""
from __future__ import annotations

import json
import threading
import time

import httpx
from sqlalchemy.orm import Session

from .config import settings
from .errors import ModelUnavailable
from .models import Alias, Provider, Setting

# 进程内调用统计：按别名累计调用数、成功率、平均延迟与最近错误。
# 多副本部署时不共享，仅用于控制台「降级链」页的即时观测。
_stats: dict[str, dict] = {}
_stats_lock = threading.Lock()


def _record(alias: str, ok: bool, dur_ms: int, degraded: bool = False, error: str = "") -> None:
    with _stats_lock:
        item = _stats.setdefault(alias, {"calls": 0, "ok": 0, "fail": 0, "degraded": 0,
                                         "total_ms": 0, "last_error": "", "last_at": ""})
        item["calls"] += 1
        item["total_ms"] += max(0, dur_ms)
        if ok:
            item["ok"] += 1
            if degraded:
                item["degraded"] += 1
        else:
            item["fail"] += 1
            item["last_error"] = error[:200]
        item["last_at"] = time.strftime("%Y-%m-%d %H:%M:%S")


def stats() -> list[dict]:
    with _stats_lock:
        rows = []
        for alias, item in _stats.items():
            calls = item["calls"] or 1
            rows.append({
                "alias": alias, "calls": item["calls"], "ok": item["ok"], "fail": item["fail"],
                "degraded": item["degraded"],
                "success_rate": round(item["ok"] / calls, 4),
                "avg_ms": int(item["total_ms"] / calls),
                "last_error": item["last_error"], "last_at": item["last_at"],
            })
    rows.sort(key=lambda r: r["calls"], reverse=True)
    return rows


def get_chain(db: Session) -> list[str]:
    row = db.get(Setting, "fallback")
    if row and row.value:
        try:
            chain = json.loads(row.value)
            if isinstance(chain, list) and chain:
                return [str(x) for x in chain]
        except json.JSONDecodeError:
            pass
    return [settings.default_model]


def set_chain(db: Session, chain: list[str]) -> None:
    row = db.get(Setting, "fallback")
    if row:
        row.value = json.dumps(chain)
    else:
        db.add(Setting(key="fallback", value=json.dumps(chain)))
    db.commit()


def resolve(db: Session, alias_name: str) -> tuple[Alias | None, Provider | None]:
    alias = db.query(Alias).filter(Alias.alias == alias_name).first()
    if not alias:
        return None, None
    provider = db.query(Provider).filter(Provider.name == alias.provider_name).first()
    return alias, provider


def _mock_text(prompt: str, context: dict | None) -> str:
    ctx = context or {}
    features = ctx.get("features") or {}
    credit = features.get("credit") or {}
    finance = features.get("finance") or {}
    judicial = features.get("judicial") or {}
    parts = []
    usage = credit.get("usage_rate")
    if usage is not None:
        parts.append(f"授信使用率 {usage * 100:.1f}%")
    if credit.get("overdue_30d"):
        parts.append(f"30 天以上逾期 {credit['overdue_30d']} 次")
    ratio = finance.get("debt_ratio")
    if ratio is not None:
        parts.append(f"资产负债率 {ratio * 100:.1f}%")
    if finance.get("net_profit", 0) < 0:
        parts.append("当期净利润为负")
    if judicial.get("labels"):
        parts.append("司法标签：" + "、".join(judicial["labels"]))
    body = "；".join(parts) if parts else "未获取到有效报文特征"
    return f"综合判断：{body}。整体风险等级为{judicial.get('level', '中')}风险，建议结合人工复核结论后出具报告。"


def _remote(provider: Provider, alias: Alias, prompt: str, extra: dict | None = None) -> dict:
    url = provider.base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if provider.api_key:
        headers["Authorization"] = f"Bearer {provider.api_key}"
    body = {
        "model": alias.model or provider.models.split("/")[0].strip() or "default",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": settings.llm_temperature,
        "max_tokens": settings.llm_max_tokens,
    }
    if settings.llm_disable_thinking:
        # DeepSeek 默认开启思考模式；抽取/改写类任务关闭后更省 token、延迟更低
        body["thinking"] = {"type": "disabled"}
    if extra:
        body.update(extra)
    with httpx.Client(timeout=settings.llm_timeout_s) as client:
        resp = client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    usage = data.get("usage") or {}
    tokens = int(usage.get("total_tokens") or 0) or max(1, len(prompt) // 3)
    cached = int((usage.get("prompt_tokens_details") or {}).get("prompt_cache_hit_tokens") or 0)
    return {"text": text, "tokens": tokens, "provider": provider.name,
            "model": data.get("model") or alias.model, "alias": alias.alias,
            "cached_tokens": cached, "degraded": False}


def complete(db: Session, prompt: str, alias_name: str | None = None,
             context: dict | None = None, extra: dict | None = None) -> dict:
    """按别名 + 降级链依次尝试，全部失败抛 AH-MODEL-502。"""
    chain = get_chain(db)
    names: list[str] = []
    if alias_name:
        names.append(alias_name)
    names.extend(n for n in chain if n not in names)
    errors: list[str] = []

    for name in names:
        started = time.perf_counter()
        alias, provider = resolve(db, name)
        if not alias or not provider:
            errors.append(f"{name}: 别名或 Provider 不存在")
            _record(name, False, 0, error="别名或 Provider 不存在")
            continue
        if provider.status == "stopped":
            errors.append(f"{name}: Provider {provider.name} 未启用")
            _record(name, False, 0, error=f"Provider {provider.name} 未启用")
            continue
        if settings.llm_mock or provider.kind == "规则" or not provider.base_url:
            text = _mock_text(prompt, context)
            degraded = provider.kind == "规则"
            _record(name, True, int((time.perf_counter() - started) * 1000), degraded=degraded)
            return {"text": text, "tokens": max(1, len(prompt) // 3 + len(text) // 3),
                    "provider": provider.name, "model": alias.model,
                    "alias": alias.alias, "cached_tokens": 0,
                    "degraded": degraded}
        try:
            result = _remote(provider, alias, prompt, extra=extra)
            _record(name, True, int((time.perf_counter() - started) * 1000))
            return result
        except Exception as exc:  # 网络/协议异常都进入下一级
            errors.append(f"{name}: {exc}")
            _record(name, False, int((time.perf_counter() - started) * 1000), error=str(exc))

    raise ModelUnavailable("；".join(errors) or "降级链为空")
