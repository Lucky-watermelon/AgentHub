"""DAG 编排：JSON 校验、分层布局、编译为 LangGraph StateGraph。

DAG JSON 结构（与前端导入格式一致）::

    {
      "name": "客户全景分析流程",
      "version": "v1",
      "entry": "n_start",
      "state_schema": {"features": "dict"},
      "nodes": [
        {"id": "n_start", "type": "start", "name": "开始", "next": ["n_dispatch"]},
        {"id": "n_dispatch", "type": "condition", "name": "报文分流",
         "branches": [{"when": "credit", "to": "n_credit"}]},
        {"id": "n_credit", "type": "tool", "name": "征信报文解析", "tool": "征信报文解析"},
        {"id": "n_end", "type": "end", "name": "结束"}
      ],
      "edges": [{"from": "a", "to": "b", "label": ""}]
    }

边既可来自节点的 next / branches / on_error，也可显式写在 edges 中。
"""
from __future__ import annotations

from typing import Annotated, Any, Callable

NODE_TYPES = ("start", "end", "llm", "tool", "skill", "condition", "hitl", "subgraph")
TYPE_LABEL = {
    "start": "开始", "end": "结束", "llm": "LLM", "tool": "工具", "skill": "技能",
    "condition": "条件分支", "hitl": "人工审批", "subgraph": "子图",
}

NODE_W, NODE_H, GAP_X, GAP_Y, PAD = 148, 56, 84, 30, 24


def normalize(raw: dict) -> dict:
    raw = raw or {}
    nodes = raw.get("nodes") if isinstance(raw.get("nodes"), list) else []
    edges: list[dict] = []

    def push(src, dst, label=""):
        if not src or not dst:
            return
        if any(e["from"] == src and e["to"] == dst and e["label"] == label for e in edges):
            return
        edges.append({"from": src, "to": dst, "label": label or ""})

    for n in nodes:
        if not isinstance(n, dict):
            continue
        for t in n.get("next") or []:
            push(n.get("id"), t)
        for b in n.get("branches") or []:
            if isinstance(b, dict):
                push(n.get("id"), b.get("to"), b.get("when") or "else")
        if n.get("on_error"):
            push(n.get("id"), n.get("on_error"), "error")
    for e in raw.get("edges") or []:
        if isinstance(e, (list, tuple)) and len(e) >= 2:
            push(e[0], e[1], e[2] if len(e) > 2 else "")
        elif isinstance(e, dict):
            push(e.get("from"), e.get("to"), e.get("label") or "")

    entry = raw.get("entry") or next(
        (n.get("id") for n in nodes if isinstance(n, dict) and n.get("type") == "start"), ""
    ) or (nodes[0].get("id") if nodes and isinstance(nodes[0], dict) else "")

    return {
        "name": raw.get("name") or "未命名流程",
        "version": raw.get("version") or "v1",
        "state_schema": raw.get("state_schema") or {},
        "entry": entry,
        "nodes": [n for n in nodes if isinstance(n, dict)],
        "edges": edges,
    }


def validate(raw: dict) -> tuple[dict, list[str], list[str]]:
    """返回 (规范化后的 dag, errors, warnings)。errors 非空时不允许发布或运行。"""
    dag = normalize(raw)
    errors: list[str] = []
    warnings: list[str] = []

    if not dag["nodes"]:
        return dag, ["nodes 为空：至少需要 start 与 end 两个节点"], warnings

    ids: dict[str, dict] = {}
    for n in dag["nodes"]:
        nid = n.get("id")
        if not nid:
            errors.append(f"存在缺少 id 的节点：{n.get('name') or '(未命名)'}")
        elif nid in ids:
            errors.append(f"节点 id 重复：{nid}")
        else:
            ids[nid] = n
        ntype = n.get("type")
        if not ntype:
            warnings.append(f"节点未声明 type，按 tool 处理：{nid or '(未命名)'}")
        elif ntype not in NODE_TYPES:
            errors.append(f"未知节点类型：{nid} -> {ntype}")

    for e in dag["edges"]:
        if e["from"] not in ids:
            errors.append(f"边的起点不存在：{e['from']} -> {e['to']}")
        if e["to"] not in ids:
            errors.append(f"边的终点不存在：{e['from']} -> {e['to']}")
        if e["from"] == e["to"]:
            errors.append(f"自环边：{e['from']}")

    if not dag["entry"]:
        errors.append("缺少 entry：未指定入口节点，且没有 type=start 的节点")
    elif dag["entry"] not in ids:
        errors.append(f"entry 指向不存在的节点：{dag['entry']}")

    indeg: dict[str, int] = {nid: 0 for nid in ids}
    outdeg: dict[str, int] = {nid: 0 for nid in ids}
    for e in dag["edges"]:
        if e["to"] in indeg:
            indeg[e["to"]] += 1
        if e["from"] in outdeg:
            outdeg[e["from"]] += 1

    for n in dag["nodes"]:
        nid, ntype = n.get("id"), n.get("type")
        if nid not in ids:
            continue
        if ntype != "start" and indeg[nid] == 0:
            errors.append(f"孤立节点：{n.get('name')}（{nid}）没有任何入边")
        if ntype != "end" and outdeg[nid] == 0:
            errors.append(f"不可达终节点：{n.get('name')}（{nid}）没有出边")
        if ntype == "condition" and not (isinstance(n.get("branches"), list) and n.get("branches")):
            errors.append(f"条件节点缺少 branches：{nid}")

    # 环检测（三色 DFS）
    color: dict[str, int] = {}
    cycle: list[str] = []

    def dfs(nid: str, path: list[str]) -> None:
        if cycle:
            return
        color[nid] = 1
        path.append(nid)
        for e in dag["edges"]:
            if e["from"] != nid or e["to"] not in ids:
                continue
            if color.get(e["to"]) == 1:
                cycle.extend(path[path.index(e["to"]):] + [e["to"]])
                return
            if not color.get(e["to"]):
                dfs(e["to"], path)
        path.pop()
        color[nid] = 2

    if dag["entry"] in ids:
        dfs(dag["entry"], [])
    for nid in ids:
        if not color.get(nid) and not cycle:
            dfs(nid, [])
    if cycle:
        errors.append("检测到环路：" + " -> ".join(cycle) + "（DAG 不允许有向环）")

    # 可达性
    if dag["entry"] in ids and not cycle:
        seen: set[str] = set()
        stack = [dag["entry"]]
        while stack:
            nid = stack.pop()
            if nid in seen:
                continue
            seen.add(nid)
            stack.extend(e["to"] for e in dag["edges"] if e["from"] == nid)
        for n in dag["nodes"]:
            if n.get("id") not in seen:
                errors.append(f"从入口不可达：{n.get('name')}（{n.get('id')}）")

    if not any(n.get("type") == "end" for n in dag["nodes"]):
        warnings.append("建议显式保留 type=end 的终止节点，便于运行时判定终态")

    return dag, errors, warnings


def topo_order(dag: dict) -> list[str]:
    ids = [n["id"] for n in dag["nodes"] if n.get("id")]
    indeg = {i: 0 for i in ids}
    for e in dag["edges"]:
        if e["to"] in indeg:
            indeg[e["to"]] += 1
    queue = [i for i in ids if indeg[i] == 0]
    if dag.get("entry") in queue:
        queue.remove(dag["entry"])
        queue.insert(0, dag["entry"])
    order: list[str] = []
    while queue:
        nid = queue.pop(0)
        order.append(nid)
        for e in dag["edges"]:
            if e["from"] == nid and e["to"] in indeg:
                indeg[e["to"]] -= 1
                if indeg[e["to"]] == 0:
                    queue.append(e["to"])
    return order or ids


def layout(dag: dict) -> dict:
    """按拓扑层级做分层布局，同层纵向居中。返回节点坐标与画布尺寸。"""
    ids = [n["id"] for n in dag["nodes"] if n.get("id")]
    level = {i: 0 for i in ids}
    indeg = {i: 0 for i in ids}
    for e in dag["edges"]:
        if e["to"] in indeg:
            indeg[e["to"]] += 1
    queue = [i for i in ids if indeg[i] == 0]
    order: list[str] = []
    while queue:
        nid = queue.pop(0)
        order.append(nid)
        for e in dag["edges"]:
            if e["from"] == nid and e["to"] in indeg:
                indeg[e["to"]] -= 1
                if indeg[e["to"]] == 0:
                    queue.append(e["to"])
    if dag.get("entry") in level:
        level[dag["entry"]] = 0
    for nid in order:
        for e in dag["edges"]:
            if e["from"] == nid and e["to"] in level:
                level[e["to"]] = max(level[e["to"]], level[nid] + 1)

    by_level: dict[int, list[str]] = {}
    for nid in ids:
        by_level.setdefault(level.get(nid, 0), []).append(nid)
    keys = sorted(by_level)
    max_count = max((len(by_level[k]) for k in keys), default=1)
    full_h = max_count * (NODE_H + GAP_Y) - GAP_Y

    pos: dict[str, dict] = {}
    max_x = 0
    for k in keys:
        arr = by_level[k]
        total = len(arr) * (NODE_H + GAP_Y) - GAP_Y
        start_y = PAD + (full_h - total) / 2
        for i, nid in enumerate(arr):
            node = next(n for n in dag["nodes"] if n.get("id") == nid)
            offset = 18 if node.get("type") in ("start", "end") else 0
            x = PAD + k * (NODE_W + GAP_X) + offset
            y = start_y + i * (NODE_H + GAP_Y)
            pos[nid] = {"x": x, "y": y}
            max_x = max(max_x, x + NODE_W)
    return {"pos": pos, "width": max_x + PAD, "height": full_h + PAD * 2,
            "node_w": NODE_W, "node_h": NODE_H, "level": level}


def structure(dag: dict) -> dict:
    """给前端反显用的图结构（坐标 + 边 + 层级）。"""
    lay = layout(dag)
    nodes = []
    for n in dag["nodes"]:
        nid = n.get("id")
        p = lay["pos"].get(nid, {"x": PAD, "y": PAD})
        meta = (
            n.get("tool") if n.get("type") == "tool"
            else n.get("skill") if n.get("type") == "skill"
            else n.get("model") if n.get("type") == "llm"
            else f"{len(n.get('branches') or [])} 条分支" if n.get("type") == "condition"
            else "WAITING_APPROVAL" if n.get("type") == "hitl"
            else "—"
        )
        nodes.append({
            "id": nid, "type": n.get("type") or "tool", "name": n.get("name") or nid,
            "meta": meta, "x": p["x"], "y": p["y"],
            "level": lay["level"].get(nid, 0),
            "tool": n.get("tool"), "model": n.get("model"), "skill": n.get("skill"),
        })
    return {
        "name": dag["name"], "version": dag["version"], "entry": dag["entry"],
        "nodes": nodes, "edges": dag["edges"],
        "width": lay["width"], "height": lay["height"],
        "node_w": lay["node_w"], "node_h": lay["node_h"],
    }


def _merge_state_dict(left: dict | None, right: dict | None) -> dict:
    """并行分支同时写同一个 map 键时的合并策略（后者覆盖同名键）。"""
    merged = dict(left or {})
    merged.update(right or {})
    return merged


def _concat_state_list(left: list | None, right: list | None) -> list:
    return list(left or []) + list(right or [])


def compile_graph(dag: dict, node_runner: Callable[[dict, dict], dict]):
    """把 DAG 编译成 LangGraph StateGraph。节点执行委托给 node_runner。

    - start 节点的出边从 START 出发
    - end 节点映射为 END
    - 条件分支按并行出边处理，是否真正执行由节点自身根据报文类型判断
    """
    from typing import Any as _Any

    from langgraph.graph import END, START, StateGraph
    from typing_extensions import TypedDict

    class RunState(TypedDict, total=False):
        customer_no: str
        payload: dict
        # 并行分支（征信/财报/司法）会同时返回 features，必须声明 reducer，
        # 否则 LangGraph 报 INVALID_CONCURRENT_GRAPH_UPDATE 并退化为顺序执行。
        features: Annotated[dict, _merge_state_dict]
        messages: Annotated[list, _concat_state_list]
        report_html: str
        outputs: Annotated[dict, _merge_state_dict]
        summary: str
        route: str

    builder = StateGraph(RunState)
    byid = {n["id"]: n for n in dag["nodes"] if n.get("id")}

    for n in dag["nodes"]:
        nid = n.get("id")
        if not nid or n.get("type") in ("start", "end"):
            continue

        def make(node=n):
            def fn(state: RunState) -> dict:
                return node_runner(node, dict(state))

            return fn

        builder.add_node(nid, make())

    for e in dag["edges"]:
        src, dst = e["from"], e["to"]
        src_type = (byid.get(src) or {}).get("type")
        dst_type = (byid.get(dst) or {}).get("type")
        target = END if dst_type == "end" else dst
        if src_type == "start":
            builder.add_edge(START, target)
        else:
            builder.add_edge(src, target)

    # 没有任何 start 节点时，把 entry 作为入口
    if not any(n.get("type") == "start" for n in dag["nodes"]):
        if dag.get("entry") in byid:
            builder.add_edge(START, dag["entry"])

    return builder.compile()
