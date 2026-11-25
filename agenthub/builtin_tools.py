"""内置工具：征信/财报/司法报文解析、HTML 报告渲染、JSON 提取、计算器。

工具契约：``fn(payload: dict, ctx: dict) -> dict``
    payload  本次运行的输入报文（credit / finance / judicial 三段）
    ctx      {"customer_no": str, "features": dict, "db": Session}
返回值会合并进运行状态（features 子字典会被 update）。
"""
from __future__ import annotations

import ast
import json
import operator
from typing import Any, Callable

from . import llm

TOOL_REGISTRY: dict[str, Callable[[dict, dict], dict]] = {}

# 内置工具的参数 Schema：控制台展示与调用前校验共用。
# 只声明常用字段，未声明的字段仍然允许传入（保持 DAG 报文透传的宽松口径）。
TOOL_SCHEMAS: dict[str, dict] = {
    "征信报文解析": {"type": "object", "properties": {"credit": {"type": "object"}}},
    "财报指标计算": {"type": "object", "properties": {"finance": {"type": "object"}}},
    "司法风险标签": {"type": "object", "properties": {"judicial": {"type": "object"}}},
    "JSON 提取器": {"type": "object", "required": ["paths"],
                 "properties": {"paths": {"type": "array", "items": {"type": "string"}}}},
    "计算器": {"type": "object", "required": ["expr"],
             "properties": {"expr": {"type": "string"}}},
    "AI 风险要点": {"type": "object", "properties": {"raw_text": {"type": "string"}}},
    "HTML 报告渲染": {"type": "object", "properties": {}},
    "知识库检索": {
        "type": "object", "required": ["q"],
        "properties": {
            "q": {"type": "string"},
            "top_k": {"type": "integer"},
            "mode": {"type": "string", "enum": ["hybrid", "vector"]},
        },
    },
}


def register(name: str):
    def deco(fn):
        TOOL_REGISTRY[name] = fn
        return fn

    return deco


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@register("征信报文解析")
def parse_credit(payload: dict, ctx: dict) -> dict:
    credit = payload.get("credit") or {}
    total = _num(credit.get("total_limit"))
    used = _num(credit.get("used_limit"))
    overdue = int(_num(credit.get("overdue_30d")))
    query = int(_num(credit.get("query_3m")))
    usage = round(used / total, 4) if total else None
    risk = []
    if usage is not None and usage >= 0.8:
        risk.append("授信使用率偏高")
    if overdue >= 2:
        risk.append("30 天以上逾期 2 次及以上")
    if query >= 10:
        risk.append("近 3 个月查询频繁")
    return {
        "features": {
            "credit": {
                "msg_no": credit.get("msg_no", ""),
                "total_limit": total, "used_limit": used,
                "usage_rate": usage, "overdue_30d": overdue, "query_3m": query,
                "risk_flags": risk,
            }
        }
    }


@register("财报指标计算")
def parse_finance(payload: dict, ctx: dict) -> dict:
    fin = payload.get("finance") or {}
    revenue = _num(fin.get("revenue"))
    profit = _num(fin.get("net_profit"))
    assets = _num(fin.get("total_assets"))
    liability = _num(fin.get("total_liability"))
    ratio = round(liability / assets, 4) if assets else None
    margin = round(profit / revenue, 4) if revenue else None
    flags = []
    if ratio is not None and ratio > 0.75:
        flags.append("资产负债率超过 75%")
    if profit < 0:
        flags.append("当期亏损")
    return {
        "features": {
            "finance": {
                "msg_no": fin.get("msg_no", ""),
                "revenue": revenue, "net_profit": profit,
                "total_assets": assets, "total_liability": liability,
                "debt_ratio": ratio, "net_margin": margin, "risk_flags": flags,
            }
        }
    }


@register("司法风险标签")
def parse_judicial(payload: dict, ctx: dict) -> dict:
    jd = payload.get("judicial") or {}
    cases = int(_num(jd.get("cases")))
    executed = int(_num(jd.get("executed")))
    dishonest = int(_num(jd.get("dishonest")))
    labels = []
    if cases:
        labels.append(f"涉诉 {cases}")
    if executed:
        labels.append(f"被执行 {executed}")
    if dishonest:
        labels.append(f"失信 {dishonest}")
    level = "高" if (dishonest or executed) else ("中" if cases else "低")
    return {
        "features": {
            "judicial": {
                "msg_no": jd.get("msg_no", ""),
                "cases": cases, "executed": executed, "dishonest": dishonest,
                "labels": labels, "level": level,
            }
        }
    }


@register("JSON 提取器")
def json_extract(payload: dict, ctx: dict) -> dict:
    """按点号路径提取字段，例如 {"paths": ["credit.used_limit"]}。"""
    paths = payload.get("paths") or []
    out = {}
    for path in paths:
        cur: Any = payload
        for part in str(path).split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                cur = None
                break
        out[str(path)] = cur
    return {"features": {"extracted": out}}


_SAFE_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.USub: operator.neg,
}


@register("计算器")
def calculator(payload: dict, ctx: dict) -> dict:
    expr = str(payload.get("expr") or "0")

    def ev(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _SAFE_OPS:
            return _SAFE_OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _SAFE_OPS:
            return _SAFE_OPS[type(node.op)](ev(node.operand))
        raise ValueError("表达式不支持")

    return {"features": {"calc": {"expr": expr, "value": ev(ast.parse(expr, mode="eval").body)}}}


def _extract_json(text: str) -> dict:
    """从模型输出中抠出 JSON（兼容 ```json 代码块与前后多余文字）。"""
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lstrip().lower().startswith("json"):
            raw = raw.lstrip()[4:]
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        parsed = json.loads(raw[start:end + 1])
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


@register("AI 风险要点")
def ai_risk_points(payload: dict, ctx: dict) -> dict:
    """调用真实大模型抽取风险要点（JSON 结构化输出，失败自动降级不阻断运行）。"""
    features = ctx.get("features") or {}
    db = ctx.get("db")
    raw_text = str(payload.get("raw_text") or "")[:1500]
    prompt = (
        "你是银行对公风控专家。请基于以下结构化特征与客户说明，只输出严格 JSON，不要任何解释：\n"
        '{"level": "高|中|低", "points": ["不超过 20 字的要点"], "suggestion": "一句话建议"}\n'
        f"结构化特征：{json.dumps(features, ensure_ascii=False)[:1800]}\n"
        f"客户说明或公告：{raw_text or '（无）'}"
    )
    try:
        result = llm.complete(db, prompt, ctx.get("model") or "default-chat",
                              {"features": features},
                              extra={"response_format": {"type": "json_object"}})
        data = _extract_json(result.get("text", ""))
        points = [str(p)[:40] for p in (data.get("points") or [])][:4]
        level = str(data.get("level") or "").strip()
        if level not in ("高", "中", "低"):
            level = "中"
        if not points:
            points = [str(result.get("text", ""))[:60]]
        return {"features": {"ai": {
            "level": level, "points": points,
            "suggestion": str(data.get("suggestion") or "")[:80],
            "model": result.get("model"), "alias": result.get("alias"),
            "degraded": result.get("degraded", False),
            "tokens": result.get("tokens", 0),
        }}}
    except Exception as exc:  # 模型不可用时不阻断运行，报告省略该段落
        return {"features": {"ai": {"level": "", "points": [], "suggestion": "",
                                    "model": "unavailable", "error": str(exc)[:120]}}}


@register("知识库检索")
def retrieve_kb(payload: dict, ctx: dict) -> dict:
    """按查询语句检索知识库切片，返回命中片段、文档名与分数。"""
    query = str(payload.get("q") or payload.get("query") or "").strip()
    if not query:
        raise ValueError("缺少查询语句：q")
    try:
        top_k = max(1, min(10, int(payload.get("top_k") or 3)))
    except (TypeError, ValueError):
        top_k = 3
    mode = "vector" if str(payload.get("mode") or "").lower() == "vector" else "hybrid"
    db = ctx.get("db")
    if db is None:
        return {"features": {"kb": {"q": query, "mode": mode, "rows": [],
                                    "error": "缺少数据库会话，无法检索"}}}
    from .kb import retrieve

    rows = retrieve(db, query, top_k, mode=mode)
    return {"features": {"kb": {"q": query, "mode": mode, "top_k": top_k, "rows": rows}}}


@register("HTML 报告渲染")
def render_report_tool(payload: dict, ctx: dict) -> dict:
    """在 DAG 中作为报告节点：把已聚合的特征渲染成 HTML。"""
    from .evidence import build_evidence
    from .report import render_report

    features = ctx.get("features") or {}
    summary = ctx.get("summary", "")
    evidence = ctx.get("evidence") or build_evidence(
        features, summary, ctx.get("evidence_items") or [])
    html = render_report(
        customer_no=ctx.get("customer_no", ""),
        payload=payload,
        features=features,
        summary=summary,
        evidence=evidence,
    )
    return {"report_html": html}


def invoke(name: str, payload: dict, ctx: dict) -> dict:
    fn = TOOL_REGISTRY.get(name)
    if not fn:
        raise KeyError(f"未注册的工具：{name}")
    return fn(payload, ctx) or {}


def names() -> list[str]:
    return list(TOOL_REGISTRY)


def describe(name: str) -> dict:
    fn = TOOL_REGISTRY.get(name)
    doc = (fn.__doc__ or "").strip().splitlines()
    return {"name": name, "description": doc[0] if doc else "", "builtin": fn is not None}


def schema_for(name: str) -> dict:
    """内置工具的参数 Schema；未登记时返回空对象（允许任意参数）。"""
    return TOOL_SCHEMAS.get(name) or {"type": "object", "properties": {}}
