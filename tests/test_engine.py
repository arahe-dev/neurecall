"""Tests for neurecall.engine — deterministic replay, op validation, materialization."""

import json
import os
import shutil
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neurecall.engine import (
    BoardEngine,
    BoardStore,
    GraphState,
    apply_op,
    build_view,
    layout_grid,
    validate_op,
    _new_id,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fresh_engine() -> BoardEngine:
    tmp = tempfile.mkdtemp(prefix="neurecall_test_")
    return BoardEngine(root=tmp), tmp


def cleanup(tmp: str) -> None:
    shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_validate_create_node_ok() -> None:
    op = {"op": "create_node", "payload": {"label": "Hello", "kind": "idea"}}
    assert validate_op(op)["ok"] is True


def test_validate_create_node_bad_kind() -> None:
    op = {"op": "create_node", "payload": {"label": "Hello", "kind": "dragon"}}
    assert validate_op(op)["ok"] is False


def test_validate_unknown_op() -> None:
    assert validate_op({"op": "fly"})["ok"] is False


def test_validate_attach_memory_missing_id() -> None:
    op = {"op": "attach_memory", "payload": {"node_id": "n1"}}
    assert validate_op(op)["ok"] is False


# ---------------------------------------------------------------------------
# State application
# ---------------------------------------------------------------------------

def test_apply_create_node() -> None:
    s = GraphState()
    s, res = apply_op(s, {"op": "create_node", "payload": {"label": "A"}})
    assert s.version == 1
    assert res["node_id"] in s.nodes
    assert s.nodes[res["node_id"]]["label"] == "A"


def test_apply_connect() -> None:
    s = GraphState()
    s, r1 = apply_op(s, {"op": "create_node", "payload": {"label": "A"}})
    s, r2 = apply_op(s, {"op": "create_node", "payload": {"label": "B"}})
    s, r3 = apply_op(s, {"op": "connect", "payload": {"from": r1["node_id"], "to": r2["node_id"]}})
    assert s.version == 3
    assert r3["edge_id"] in s.edges


def test_apply_delete_soft() -> None:
    s = GraphState()
    s, r = apply_op(s, {"op": "create_node", "payload": {"label": "A"}})
    nid = r["node_id"]
    s, _ = apply_op(s, {"op": "delete_node", "payload": {"node_id": nid}})
    assert nid in s.nodes
    assert s.nodes[nid]["deleted"] is True


def test_apply_delete_hard() -> None:
    s = GraphState()
    s, r = apply_op(s, {"op": "create_node", "payload": {"label": "A"}})
    nid = r["node_id"]
    s, _ = apply_op(s, {"op": "delete_node", "payload": {"node_id": nid, "hard": True}})
    assert nid not in s.nodes


def test_apply_move_node() -> None:
    s = GraphState()
    s, r = apply_op(s, {"op": "create_node", "payload": {"label": "A"}})
    nid = r["node_id"]
    s, _ = apply_op(s, {"op": "move_node", "payload": {"node_id": nid, "x": 42, "y": 99}})
    assert s.layouts[nid] == {"x": 42, "y": 99}


def test_apply_group_and_collapse() -> None:
    s = GraphState()
    s, r1 = apply_op(s, {"op": "create_node", "payload": {"label": "A"}})
    s, r2 = apply_op(s, {"op": "create_node", "payload": {"label": "B"}})
    s, r3 = apply_op(s, {"op": "group_nodes", "payload": {"node_ids": [r1["node_id"], r2["node_id"]]}})
    gid = r3["group_id"]
    assert gid in s.groups
    s, _ = apply_op(s, {"op": "collapse_subspace", "payload": {"group_id": gid}})
    assert s.groups[gid]["collapsed"] is True
    s, _ = apply_op(s, {"op": "expand_subspace", "payload": {"group_id": gid}})
    assert s.groups[gid]["collapsed"] is False


def test_apply_tag_untag() -> None:
    s = GraphState()
    s, r = apply_op(s, {"op": "create_node", "payload": {"label": "A"}})
    nid = r["node_id"]
    s, _ = apply_op(s, {"op": "tag_node", "payload": {"node_id": nid, "tag": "mvp"}})
    assert "mvp" in s.nodes[nid]["tags"]
    s, _ = apply_op(s, {"op": "untag_node", "payload": {"node_id": nid, "tag": "mvp"}})
    assert "mvp" not in s.nodes[nid]["tags"]


def test_apply_attach_memory_and_route() -> None:
    s = GraphState()
    s, r = apply_op(s, {"op": "create_node", "payload": {"label": "A"}})
    nid = r["node_id"]
    s, _ = apply_op(s, {"op": "attach_memory", "payload": {"node_id": nid, "memory_id": "mem_1", "route_id": "route_1"}})
    refs = s.nodes[nid]["refs"]
    assert any(r.get("type") == "memory" and r.get("id") == "mem_1" for r in refs)
    s, _ = apply_op(s, {"op": "attach_route", "payload": {"node_id": nid, "route_id": "route_2"}})
    refs = s.nodes[nid]["refs"]
    assert any(r.get("type") == "route" and r.get("id") == "route_2" for r in refs)


# ---------------------------------------------------------------------------
# Deterministic replay
# ---------------------------------------------------------------------------

def test_replay_produces_same_state() -> None:
    eng, tmp = fresh_engine()
    ops = [
        {"op": "create_node", "payload": {"label": "A", "kind": "idea"}},
        {"op": "create_node", "payload": {"label": "B", "kind": "task"}},
        {"op": "create_node", "payload": {"label": "C", "kind": "idea"}},
        {"op": "connect", "payload": {"from": "node_a", "to": "node_b", "relation": "depends_on"}},
        {"op": "group_nodes", "payload": {"node_ids": ["node_a", "node_b"], "label": "MVP"}},
    ]
    # override ids for determinism in this test
    for i, op in enumerate(ops):
        if op["op"] == "create_node":
            op["payload"]["id"] = f"node_{chr(ord('a')+i)}"
        elif op["op"] == "connect":
            op["payload"]["id"] = f"edge_{i}"
        elif op["op"] == "group_nodes":
            op["payload"]["id"] = f"group_{i}"
        eng.commit(op)

    state1 = eng.state
    state2 = eng.rebuild()
    assert state1.to_dict() == state2.to_dict()
    cleanup(tmp)


# ---------------------------------------------------------------------------
# BoardStore persistence
# ---------------------------------------------------------------------------

def test_store_append_and_materialize() -> None:
    eng, tmp = fresh_engine()
    op = {"op": "create_node", "payload": {"label": "X", "id": "node_x"}}
    eng.commit(op)
    # fresh engine reading same files
    eng2 = BoardEngine(root=tmp)
    assert "node_x" in eng2.state.nodes
    cleanup(tmp)


def test_store_event_retrieval() -> None:
    eng, tmp = fresh_engine()
    res = eng.commit({"op": "create_node", "payload": {"label": "Y", "id": "node_y"}})
    eid = res["event_id"]
    evt = eng.event(eid)
    assert evt is not None
    assert evt["op"]["op"] == "create_node"
    cleanup(tmp)


# ---------------------------------------------------------------------------
# Multi-commit
# ---------------------------------------------------------------------------

def test_commit_multi_all_or_nothing() -> None:
    eng, tmp = fresh_engine()
    ops = [
        {"op": "create_node", "payload": {"label": "A", "id": "node_a"}},
        {"op": "create_node", "payload": {"label": "B", "id": "node_b"}},
        {"op": "connect", "payload": {"from": "node_a", "to": "node_missing", "id": "edge_1"}},
    ]
    res = eng.commit_multi(ops)
    assert res["ok"] is False
    # nothing persisted because third op fails validation
    eng2 = BoardEngine(root=tmp)
    assert "node_a" not in eng2.state.nodes
    cleanup(tmp)


def test_commit_multi_success() -> None:
    eng, tmp = fresh_engine()
    ops = [
        {"op": "create_node", "payload": {"label": "A", "id": "node_a"}},
        {"op": "create_node", "payload": {"label": "B", "id": "node_b"}},
        {"op": "connect", "payload": {"from": "node_a", "to": "node_b", "id": "edge_1"}},
    ]
    res = eng.commit_multi(ops)
    assert res["ok"] is True
    assert len(res["event_ids"]) == 3
    cleanup(tmp)


# ---------------------------------------------------------------------------
# View builder
# ---------------------------------------------------------------------------

def test_build_view_filters_deleted() -> None:
    s = GraphState()
    s, r1 = apply_op(s, {"op": "create_node", "payload": {"label": "A", "id": "node_a"}})
    s, r2 = apply_op(s, {"op": "create_node", "payload": {"label": "B", "id": "node_b"}})
    s, _ = apply_op(s, {"op": "delete_node", "payload": {"node_id": "node_a"}})
    v = build_view(s)
    assert "node_a" not in v["nodes"]
    assert "node_b" in v["nodes"]


def test_build_view_filter_tags() -> None:
    s = GraphState()
    s, _ = apply_op(s, {"op": "create_node", "payload": {"label": "A", "id": "node_a", "tags": ["mvp"]}})
    s, _ = apply_op(s, {"op": "create_node", "payload": {"label": "B", "id": "node_b", "tags": ["later"]}})
    v = build_view(s, filter_tags=["mvp"])
    assert "node_a" in v["nodes"]
    assert "node_b" not in v["nodes"]


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------

def test_layout_grid_deterministic() -> None:
    s = GraphState()
    for i in range(5):
        s, _ = apply_op(s, {"op": "create_node", "payload": {"label": str(i), "id": f"n{i}"}})
    patch = layout_grid(s)
    assert len(patch) == 5
    # same call twice should be identical
    patch2 = layout_grid(s)
    assert patch == patch2


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import traceback

    failures = 0
    tests = [
        test_validate_create_node_ok,
        test_validate_create_node_bad_kind,
        test_validate_unknown_op,
        test_validate_attach_memory_missing_id,
        test_apply_create_node,
        test_apply_connect,
        test_apply_delete_soft,
        test_apply_delete_hard,
        test_apply_move_node,
        test_apply_group_and_collapse,
        test_apply_tag_untag,
        test_apply_attach_memory_and_route,
        test_replay_produces_same_state,
        test_store_append_and_materialize,
        test_store_event_retrieval,
        test_commit_multi_all_or_nothing,
        test_commit_multi_success,
        test_build_view_filters_deleted,
        test_build_view_filter_tags,
        test_layout_grid_deterministic,
    ]
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except Exception as exc:
            failures += 1
            print(f"FAIL {t.__name__}: {exc}")
            traceback.print_exc()
    print(f"\n{'='*40}")
    print(f"Ran {len(tests)} tests, {failures} failures")
    sys.exit(0 if failures == 0 else 1)
