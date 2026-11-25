"""可解释证据链：把模型结论回链到工具 / 知识库 / 技能脚本 / 模型调用。

设计原则：
- 运行过程中采集证据条目（不额外调用模型，零 token 成本）
- 运行结束后按关键词把结论句与证据匹配，生成「结论 → 证据」列表
- 报告与运行详情共用同一份 evidence，避免两套口径
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

KIND_LABEL = {"tool": "工具", "kb": "知识库", "skill": "技能脚本", "model": "模型",
              "hitl": "人工审批", "error": "失败"}

_DOMAINS: dict[str, tuple[str, list[str]]] = {
    "credit": ("征信报文解析", ["授信", "额度", "逾期", "查询", "征信", "使用率"]),
    "finance": ("财报指标计算", ["资产负债", "负债", "净利", "亏损", "营收", "杠杆", "财报", "偿债"]),
    "judicial": ("司法风险标签", ["司法", "涉诉", "被执行", "失信", "风险等级"]),
    "ai": ("AI 风险要点", ["AI", "建议", "风险", "要点"]),
    "quick_check": ("单客户快速体检", ["体检", "分数", "评分", "风险"]),
}


def _pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:.1f}%"
    except Exception:
        return "—"


def _num(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return "—"


def _format_domain(domain: str, data: dict) -> str:
    data = data or {}
    if domain == "credit":
        parts = [f"授信使用率 {_pct(data.get('usage_rate'))}",
                 f"30 天以上逾期 {data.get('overdue_30d') or 0} 次",
                 f"近 3 个月查询 {data.get('query_3m') or 0} 次"]
        flags = data.get("risk_flags") or []
        if flags:
            parts.append("风险标签：" + "、".join(str(x) for x in flags))
        return "；".join(parts)
    if domain == "finance":
        parts = [f"资产负债率 {_pct(data.get('debt_ratio'))}",
                 f"净利润 {_num(data.get('net_profit'))}",
                 f"净利率 {_pct(data.get('net_margin'))}"]
        flags = data.get("risk_flags") or []
        if flags:
            parts.append("风险标签：" + "、".join(str(x) for x in flags))
        return "；".join(parts)
    if domain == "judicial":
        parts = [f"涉诉 {data.get('cases') or 0} 起",
                 f"被执行 {data.get('executed') or 0} 起",
                 f"失信 {data.get('dishonest') or 0} 条",
                 f"风险等级 {data.get('level') or '低'}"]
        labels = data.get("labels") or []
        if labels:
            parts.append("标签：" + "、".join(str(x) for x in labels))
        return "；".join(parts)
    if domain == "ai":
        points = data.get("points") or []
        text = "；".join(str(x) for x in points[:4])
        return f"等级 {data.get('level') or '—'}；{text or '无要点'}"
    if domain == "quick_check":
        return f"风险分 {data.get('score')}；等级 {data.get('level') or '—'}"
    return json.dumps(data, ensure_ascii=False)[:300]


def _base_item(kind: str, title: str, detail: str, node: str,
               keywords: list[str], ref: str, meta: dict | None = None) -> dict:
    return {
        "kind": kind, "kind_label": KIND_LABEL.get(kind, kind),
        "title": title, "detail": (detail or "")[:500], "node": node,
        "keywords": [k for k in keywords if k], "ref": ref, "meta": meta or {},
    }


def from_tool(tool_name: str, node: str, out: dict | None,
              dur_ms: int = 0) -> list[dict]:
    """工具结果 → 证据条目。知识库按切片拆开，便于逐条引用。"""
    features = (out or {}).get("features") or {}
    if not isinstance(features, dict):
        return []
    items: list[dict] = []

    kb = features.get("kb") or {}
    query = str(kb.get("q") or "")
    query_terms = [t for t in re.split(r"[\s,，、；;]+", query) if len(t) >= 2]
    for row in kb.get("rows") or []:
        doc = row.get("doc_name") or row.get("doc") or "知识库"
        idx = row.get("idx", 0)
        items.append(_base_item(
            "kb", f"{doc} #{idx}",
            str(row.get("text") or "")[:500],
            node,
            query_terms + ["口径", "知识库", "标准"],
            f"kb://{row.get('doc', '')}#{idx}",
            {"score": row.get("score"), "vec": row.get("vec"), "hit": row.get("hit")},
        ))

    for domain, (title, keywords) in _DOMAINS.items():
        data = features.get(domain)
        if not isinstance(data, dict):
            continue
        detail = _format_domain(domain, data)
        if not detail:
            continue
        if domain == "credit":
            script_reasons = data.get("script_reasons") or []
            if script_reasons:
                detail += "；技能判定：" + "、".join(str(x) for x in script_reasons)
            if data.get("risk_band"):
                detail += f"（{data['risk_band']}）"
        if domain == "finance":
            script_flags = data.get("script_flags") or []
            if script_flags:
                detail += "；技能标签：" + "、".join(str(x) for x in script_flags)
        items.append(_base_item(
            "tool", title, detail, node, keywords,
            f"tool:{tool_name}", {"dur_ms": dur_ms},
        ))

    if not items:
        keys = [str(k) for k in features.keys()]
        if keys:
            items.append(_base_item(
                "tool", tool_name,
                "输出字段：" + "、".join(keys) + "；" + json.dumps(features, ensure_ascii=False)[:300],
                node, [tool_name], f"tool:{tool_name}", {"dur_ms": dur_ms},
            ))
    return items


def from_script(skill_name: str, script_name: str, result: dict,
                node: str, dur_ms: int = 0) -> dict:
    parsed = result.get("result")
    if parsed is not None:
        detail = json.dumps(parsed, ensure_ascii=False)
    elif result.get("stdout"):
        detail = str(result.get("stdout"))[:400]
    else:
        detail = str(result.get("stderr") or "脚本无输出")[:300]
    return _base_item(
        "skill", f"技能脚本 · {script_name}",
        detail, node,
        ["技能", "脚本", "分级", "使用率", "关注", "高风险", skill_name],
        f"skill:{skill_name}/{script_name}",
        {"dur_ms": dur_ms, "exit_code": result.get("exit_code")},
    )


def from_model(alias: str, result: dict, node: str) -> dict:
    return _base_item(
        "model", f"模型判读 · {alias}",
        str(result.get("text") or "")[:500], node, [],
        f"model:{alias}",
        {"tokens": result.get("tokens"), "degraded": result.get("degraded", False),
         "model": result.get("model")},
    )


def from_hitl(point: str, detail: str, node: str) -> dict:
    return _base_item(
        "hitl", f"人工审批 · {point}", detail, node,
        ["审批", "人工", "复核", "高风险"], f"hitl:{point}",
    )


def from_error(tool_name: str, node: str, error: str) -> dict:
    return _base_item(
        "error", f"失败 · {tool_name}", str(error or "")[:300], node,
        [tool_name], f"error:{tool_name}",
    )


def _split_claims(summary: str) -> list[str]:
    text = re.sub(r"\s+", " ", summary or "").strip()
    if not text:
        return []
    parts = [p.strip(" ：:，,") for p in re.split(r"[；。！？\n]+", text)]
    return [p for p in parts if len(p) >= 6][:8]


def _fallback_claims(features: dict) -> list[str]:
    credit = (features or {}).get("credit") or {}
    finance = (features or {}).get("finance") or {}
    judicial = (features or {}).get("judicial") or {}
    flags = list((credit.get("risk_flags") or []) + (finance.get("risk_flags") or []))
    flags += list(credit.get("script_reasons") or []) + list(finance.get("script_flags") or [])
    if flags:
        return [str(x) for x in dict.fromkeys(flags)][:6]
    parts = []
    if credit.get("usage_rate") is not None:
        parts.append(f"授信使用率 {_pct(credit['usage_rate'])}")
    if finance.get("debt_ratio") is not None:
        parts.append(f"资产负债率 {_pct(finance['debt_ratio'])}")
    if judicial.get("level"):
        parts.append(f"司法风险等级 {judicial['level']}")
    return parts


def build_evidence(features: dict, summary: str, raw_items: list[dict] | None) -> dict:
    """生成 claims + items。纯规则匹配，不调用模型。"""
    items: list[dict] = []
    seen: set[tuple] = set()
    for raw in raw_items or []:
        if not isinstance(raw, dict):
            continue
        key = (raw.get("kind"), raw.get("title"), str(raw.get("detail"))[:80])
        if key in seen:
            continue
        seen.add(key)
        item = dict(raw)
        item["id"] = f"e{len(items) + 1}"
        item.setdefault("kind_label", KIND_LABEL.get(item.get("kind"), item.get("kind")))
        items.append(item)
        if len(items) >= 40:
            break

    claims_text = _split_claims(summary) or _fallback_claims(features)
    model_ids = [it["id"] for it in items if it.get("kind") == "model"]
    claims: list[dict] = []
    for idx, text in enumerate(claims_text[:8]):
        scored: list[tuple[int, dict]] = []
        for item in items:
            score = sum(1 for kw in item.get("keywords") or [] if kw and str(kw) in text)
            if score:
                scored.append((score, item))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
        refs = [item["id"] for _, item in scored[:3]]
        if not refs:
            refs = model_ids[-1:] or [items[0]["id"]] if items else []
        claims.append({"id": f"c{idx + 1}", "text": text, "evidence": refs})

    return {
        "summary": summary or "",
        "claims": claims,
        "items": items,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
