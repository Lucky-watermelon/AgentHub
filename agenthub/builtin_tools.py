"""内置工具：征信/财报/司法报文解析、HTML 报告渲染、JSON 提取、计算器。

工具契约：``fn(payload: dict, ctx: dict) -> dict``
    payload  本次运行的输入报文（credit / finance / judicial 三段）
    ctx      {"customer_no": str, "features": dict, "db": Session}
返回值会合并进运行状态（features 子字典会被 update）。
"""
from __future__ import annotations

import ast
import operator
from typing import Any, Callable

TOOL_REGISTRY: dict[str, Callable[[dict, dict], dict]] = {}


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


@register("HTML 报告渲染")
def render_report_tool(payload: dict, ctx: dict) -> dict:
    """在 DAG 中作为报告节点：把已聚合的特征渲染成 HTML。"""
    from .report import render_report

    features = ctx.get("features") or {}
    html = render_report(
        customer_no=ctx.get("customer_no", ""),
        payload=payload,
        features=features,
        summary=ctx.get("summary", ""),
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
