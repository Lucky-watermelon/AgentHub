"""DAG 校验与布局的单元测试。"""
from __future__ import annotations

from agenthub.dag import layout, normalize, structure, topo_order, validate
from agenthub.seed import SAMPLE_DAG


def test_sample_dag_is_valid():
    dag, errors, warnings = validate(SAMPLE_DAG)
    assert errors == []
    assert warnings == []
    assert len(dag["nodes"]) == len(SAMPLE_DAG["nodes"])
    assert len(dag["edges"]) == 16
    assert dag["entry"] == "n_start"


def test_branch_edges_are_expanded():
    dag = normalize(SAMPLE_DAG)
    branches = [e for e in dag["edges"] if e["from"] == "n_dispatch" and e["label"]]
    assert len(branches) == 3
    assert {e["label"] for e in branches} == {"credit", "finance", "judicial"}


def test_cycle_detected():
    raw = {"entry": "a", "nodes": [
        {"id": "a", "type": "start", "name": "A", "next": ["b"]},
        {"id": "b", "type": "tool", "name": "B", "next": ["a"]},
        {"id": "c", "type": "end", "name": "C"},
    ]}
    _, errors, _ = validate(raw)
    assert any("环路" in e for e in errors)


def test_isolated_node_detected():
    raw = {"entry": "a", "nodes": [
        {"id": "a", "type": "start", "name": "A", "next": ["b"]},
        {"id": "b", "type": "end", "name": "B"},
        {"id": "x", "type": "tool", "name": "孤立"},
    ]}
    _, errors, _ = validate(raw)
    assert any("孤立节点" in e for e in errors)
    assert any("不可达" in e for e in errors)


def test_dangling_edge_detected():
    raw = {"entry": "a", "nodes": [
        {"id": "a", "type": "start", "name": "A", "next": ["zzz"]},
        {"id": "b", "type": "end", "name": "B"},
    ]}
    _, errors, _ = validate(raw)
    assert any("终点不存在" in e for e in errors)


def test_duplicate_id_detected():
    raw = {"entry": "a", "nodes": [
        {"id": "a", "type": "start", "name": "A", "next": ["b"]},
        {"id": "a", "type": "tool", "name": "重复"},
        {"id": "b", "type": "end", "name": "B"},
    ]}
    _, errors, _ = validate(raw)
    assert any("id 重复" in e for e in errors)


def test_layout_and_structure():
    dag, errors, _ = validate(SAMPLE_DAG)
    assert errors == []
    lay = layout(dag)
    assert lay["width"] > 0 and lay["height"] > 0
    assert lay["level"]["n_start"] == 0
    assert lay["level"]["n_merge"] >= 2
    st = structure(dag)
    assert len(st["nodes"]) == len(SAMPLE_DAG["nodes"])
    assert all("x" in n and "y" in n for n in st["nodes"])


def test_topo_order_covers_all_nodes():
    dag = normalize(SAMPLE_DAG)
    order = topo_order(dag)
    assert set(order) == {n["id"] for n in dag["nodes"]}
    assert order.index("n_start") < order.index("n_end")
