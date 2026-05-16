"""NeuRecall Board Engine — deterministic graph reducer over JSONL storage.

MVP 0: Headless engine.  All state changes flow through validate_op + apply_op,
append_event writes to graph_events.jsonl, and materialize_state rebuilds
current board state from the event log.
"""

from __future__ import annotations

import copy
import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Constants & defaults
# ---------------------------------------------------------------------------

DEFAULT_DATA_ROOT = Path(r"C:\ivy-data\alexandria\engine\graph")
EVENT_LOG = "graph_events.jsonl"
STATE_FILE = "graph_state.json"
LAYOUTS_FILE = "layouts.json"
VIEWS_FILE = "board_views.jsonl"

NODE_KINDS = {"idea", "task", "memory_ref", "route_ref", "github_ref", "packet", "group", "view"}
RELATIONS = {"uses", "relates_to", "depends_on", "proves", "rejects", "contains", "part_of"}


def _ts() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Schemas (lightweight dict validators)
# ---------------------------------------------------------------------------

def _validate_node_shape(payload: dict) -> list[str]:
    errs: list[str] = []
    if "id" in payload and not isinstance(payload["id"], str):
        errs.append("node.id must be a string")
    if "kind" in payload and payload["kind"] not in NODE_KINDS:
        errs.append(f"node.kind must be one of {NODE_KINDS}")
    if "label" in payload and not isinstance(payload["label"], str):
        errs.append("node.label must be a string")
    return errs


def _validate_edge_shape(payload: dict) -> list[str]:
    errs: list[str] = []
    for key in ("from", "to"):
        if key not in payload:
            errs.append(f"edge missing required field '{key}'")
    if payload.get("relation") and payload["relation"] not in RELATIONS:
        errs.append(f"edge.relation must be one of {RELATIONS}")
    return errs


# ---------------------------------------------------------------------------
# Core data classes
# ---------------------------------------------------------------------------

@dataclass
class GraphState:
    nodes: dict[str, dict] = field(default_factory=dict)
    edges: dict[str, dict] = field(default_factory=dict)
    groups: dict[str, dict] = field(default_factory=dict)
    layouts: dict[str, dict] = field(default_factory=dict)
    views: dict[str, dict] = field(default_factory=dict)
    version: int = 0

    def to_dict(self) -> dict:
        return {
            "nodes": self.nodes,
            "edges": self.edges,
            "groups": self.groups,
            "layouts": self.layouts,
            "views": self.views,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict) -> GraphState:
        return cls(
            nodes=d.get("nodes", {}),
            edges=d.get("edges", {}),
            groups=d.get("groups", {}),
            layouts=d.get("layouts", {}),
            views=d.get("views", {}),
            version=d.get("version", 0),
        )


# ---------------------------------------------------------------------------
# Operation handlers
# ---------------------------------------------------------------------------

OpHandler = Callable[[GraphState, dict], tuple[GraphState, dict]]


def _op_create_node(state: GraphState, op: dict) -> tuple[GraphState, dict]:
    payload = op.get("payload", {})
    node_id = payload.get("id") or _new_id("node")
    if node_id in state.nodes:
        raise ValueError(f"node_id already exists: {node_id}")
    node = {
        "id": node_id,
        "kind": payload.get("kind", "idea"),
        "label": payload.get("label", ""),
        "summary": payload.get("summary", ""),
        "tags": list(payload.get("tags", [])),
        "refs": list(payload.get("refs", [])),
        "provenance": {
            "created_by": payload.get("created_by", "agent"),
            "created_at": payload.get("created_at") or _ts(),
            "event_id": op.get("event_id"),
        },
        "deleted": False,
    }
    state.nodes[node_id] = node
    # auto-layout if coordinates provided
    if "x" in payload and "y" in payload:
        state.layouts[node_id] = {"x": payload["x"], "y": payload["y"]}
    return state, {"node_id": node_id}


def _op_update_node(state: GraphState, op: dict) -> tuple[GraphState, dict]:
    payload = op.get("payload", {})
    node_id = payload["node_id"]
    if node_id not in state.nodes:
        raise ValueError(f"node not found: {node_id}")
    node = state.nodes[node_id]
    for key in ("label", "summary", "kind"):
        if key in payload:
            node[key] = payload[key]
    if "tags" in payload:
        node["tags"] = list(payload["tags"])
    if "refs" in payload:
        node["refs"] = list(payload["refs"])
    node["modified_at"] = _ts()
    return state, {"node_id": node_id}


def _op_delete_node(state: GraphState, op: dict) -> tuple[GraphState, dict]:
    payload = op.get("payload", {})
    node_id = payload["node_id"]
    hard = payload.get("hard", False)
    if node_id not in state.nodes:
        raise ValueError(f"node not found: {node_id}")
    if hard:
        del state.nodes[node_id]
        state.layouts.pop(node_id, None)
    else:
        state.nodes[node_id]["deleted"] = True
    return state, {"node_id": node_id, "hard": hard}


def _op_connect(state: GraphState, op: dict) -> tuple[GraphState, dict]:
    payload = op.get("payload", {})
    edge_id = payload.get("id") or _new_id("edge")
    from_id = payload["from"]
    to_id = payload["to"]
    if from_id not in state.nodes:
        raise ValueError(f"from node not found: {from_id}")
    if to_id not in state.nodes:
        raise ValueError(f"to node not found: {to_id}")
    edge = {
        "id": edge_id,
        "from": from_id,
        "to": to_id,
        "relation": payload.get("relation", "relates_to"),
        "label": payload.get("label", ""),
        "tags": list(payload.get("tags", [])),
        "provenance": {
            "created_by": payload.get("created_by", "agent"),
            "created_at": payload.get("created_at") or _ts(),
            "event_id": op.get("event_id"),
        },
    }
    state.edges[edge_id] = edge
    return state, {"edge_id": edge_id}


def _op_disconnect(state: GraphState, op: dict) -> tuple[GraphState, dict]:
    payload = op.get("payload", {})
    edge_id = payload["edge_id"]
    if edge_id not in state.edges:
        raise ValueError(f"edge not found: {edge_id}")
    del state.edges[edge_id]
    return state, {"edge_id": edge_id}


def _op_move_node(state: GraphState, op: dict) -> tuple[GraphState, dict]:
    payload = op.get("payload", {})
    node_id = payload["node_id"]
    if node_id not in state.nodes:
        raise ValueError(f"node not found: {node_id}")
    state.layouts[node_id] = {
        "x": payload.get("x", 0),
        "y": payload.get("y", 0),
    }
    return state, {"node_id": node_id, "layout": state.layouts[node_id]}


def _op_group_nodes(state: GraphState, op: dict) -> tuple[GraphState, dict]:
    payload = op.get("payload", {})
    group_id = payload.get("id") or _new_id("group")
    node_ids = list(payload.get("node_ids", []))
    for nid in node_ids:
        if nid not in state.nodes:
            raise ValueError(f"node not found in group: {nid}")
    group = {
        "id": group_id,
        "label": payload.get("label", "Group"),
        "node_ids": node_ids,
        "collapsed": payload.get("collapsed", False),
        "provenance": {
            "created_by": payload.get("created_by", "agent"),
            "created_at": _ts(),
            "event_id": op.get("event_id"),
        },
    }
    state.groups[group_id] = group
    return state, {"group_id": group_id}


def _op_ungroup_nodes(state: GraphState, op: dict) -> tuple[GraphState, dict]:
    payload = op.get("payload", {})
    group_id = payload["group_id"]
    if group_id not in state.groups:
        raise ValueError(f"group not found: {group_id}")
    del state.groups[group_id]
    return state, {"group_id": group_id}


def _op_collapse_subspace(state: GraphState, op: dict) -> tuple[GraphState, dict]:
    payload = op.get("payload", {})
    group_id = payload["group_id"]
    if group_id not in state.groups:
        raise ValueError(f"group not found: {group_id}")
    state.groups[group_id]["collapsed"] = True
    return state, {"group_id": group_id}


def _op_expand_subspace(state: GraphState, op: dict) -> tuple[GraphState, dict]:
    payload = op.get("payload", {})
    group_id = payload["group_id"]
    if group_id not in state.groups:
        raise ValueError(f"group not found: {group_id}")
    state.groups[group_id]["collapsed"] = False
    return state, {"group_id": group_id}


def _op_tag_node(state: GraphState, op: dict) -> tuple[GraphState, dict]:
    payload = op.get("payload", {})
    node_id = payload["node_id"]
    tag = payload["tag"]
    if node_id not in state.nodes:
        raise ValueError(f"node not found: {node_id}")
    tags = set(state.nodes[node_id].get("tags", []))
    tags.add(tag)
    state.nodes[node_id]["tags"] = sorted(tags)
    return state, {"node_id": node_id, "tags": list(tags)}


def _op_untag_node(state: GraphState, op: dict) -> tuple[GraphState, dict]:
    payload = op.get("payload", {})
    node_id = payload["node_id"]
    tag = payload["tag"]
    if node_id not in state.nodes:
        raise ValueError(f"node not found: {node_id}")
    tags = set(state.nodes[node_id].get("tags", []))
    tags.discard(tag)
    state.nodes[node_id]["tags"] = sorted(tags)
    return state, {"node_id": node_id, "tags": list(tags)}


def _op_attach_memory(state: GraphState, op: dict) -> tuple[GraphState, dict]:
    payload = op.get("payload", {})
    node_id = payload["node_id"]
    memory_id = payload["memory_id"]
    route_id = payload.get("route_id")
    if node_id not in state.nodes:
        raise ValueError(f"node not found: {node_id}")
    refs = state.nodes[node_id].get("refs", [])
    # dedupe by memory_id
    refs = [r for r in refs if not (r.get("type") == "memory" and r.get("id") == memory_id)]
    ref: dict[str, Any] = {"type": "memory", "id": memory_id}
    if route_id:
        ref["route_id"] = route_id
    refs.append(ref)
    state.nodes[node_id]["refs"] = refs
    return state, {"node_id": node_id, "memory_id": memory_id}


def _op_attach_route(state: GraphState, op: dict) -> tuple[GraphState, dict]:
    payload = op.get("payload", {})
    node_id = payload["node_id"]
    route_id = payload["route_id"]
    if node_id not in state.nodes:
        raise ValueError(f"node not found: {node_id}")
    refs = state.nodes[node_id].get("refs", [])
    refs = [r for r in refs if not (r.get("type") == "route" and r.get("id") == route_id)]
    refs.append({"type": "route", "id": route_id})
    state.nodes[node_id]["refs"] = refs
    return state, {"node_id": node_id, "route_id": route_id}


def _op_create_view(state: GraphState, op: dict) -> tuple[GraphState, dict]:
    payload = op.get("payload", {})
    view_id = payload.get("id") or _new_id("view")
    view = {
        "id": view_id,
        "label": payload.get("label", "View"),
        "filter_tags": list(payload.get("filter_tags", [])),
        "node_ids": list(payload.get("node_ids", [])) if "node_ids" in payload else None,
        "created_at": _ts(),
    }
    state.views[view_id] = view
    return state, {"view_id": view_id}


def _op_set_view_filter(state: GraphState, op: dict) -> tuple[GraphState, dict]:
    payload = op.get("payload", {})
    view_id = payload["view_id"]
    if view_id not in state.views:
        raise ValueError(f"view not found: {view_id}")
    if "filter_tags" in payload:
        state.views[view_id]["filter_tags"] = list(payload["filter_tags"])
    return state, {"view_id": view_id}


def _op_layout(state: GraphState, op: dict) -> tuple[GraphState, dict]:
    """Explicit layout batch — replaces specified coordinates."""
    payload = op.get("payload", {})
    coordinates = payload.get("coordinates", {})
    for node_id, pos in coordinates.items():
        if node_id in state.nodes:
            state.layouts[node_id] = {"x": pos.get("x", 0), "y": pos.get("y", 0)}
    return state, {"updated": list(coordinates.keys())}


OP_REGISTRY: dict[str, OpHandler] = {
    "create_node": _op_create_node,
    "update_node": _op_update_node,
    "delete_node": _op_delete_node,
    "connect": _op_connect,
    "disconnect": _op_disconnect,
    "move_node": _op_move_node,
    "group_nodes": _op_group_nodes,
    "ungroup_nodes": _op_ungroup_nodes,
    "collapse_subspace": _op_collapse_subspace,
    "expand_subspace": _op_expand_subspace,
    "tag_node": _op_tag_node,
    "untag_node": _op_untag_node,
    "attach_memory": _op_attach_memory,
    "attach_route": _op_attach_route,
    "create_view": _op_create_view,
    "set_view_filter": _op_set_view_filter,
    "layout": _op_layout,
}


# ---------------------------------------------------------------------------
# Validation & application
# ---------------------------------------------------------------------------

def validate_op(op: dict, state: GraphState | None = None) -> dict:
    """Validate a single board operation.  Returns {"ok": bool, "errors": [...]}."""
    if not isinstance(op, dict):
        return {"ok": False, "errors": ["op must be a dict"]}
    op_type = op.get("op")
    if not op_type:
        return {"ok": False, "errors": ["op missing 'op' field"]}
    if op_type not in OP_REGISTRY:
        return {"ok": False, "errors": [f"unknown op: {op_type}"]}
    payload = op.get("payload", {})
    errs: list[str] = []
    if op_type in ("create_node", "update_node"):
        errs.extend(_validate_node_shape(payload))
    if op_type == "connect":
        errs.extend(_validate_edge_shape(payload))
    if op_type == "attach_memory" and "memory_id" not in payload:
        errs.append("attach_memory requires memory_id")
    if op_type == "attach_route" and "route_id" not in payload:
        errs.append("attach_route requires route_id")
    # TODO: more fine-grained checks (node existence for update/delete, etc.)
    return {"ok": len(errs) == 0, "errors": errs}


def apply_op(state: GraphState, op: dict) -> tuple[GraphState, dict]:
    """Apply a validated operation to state, returning (new_state, result)."""
    op_type = op["op"]
    handler = OP_REGISTRY[op_type]
    new_state = copy.deepcopy(state)
    new_state, result = handler(new_state, op)
    new_state.version += 1
    return new_state, result


# ---------------------------------------------------------------------------
# Event log & materialization
# ---------------------------------------------------------------------------

class BoardStore:
    """Manages graph_events.jsonl, graph_state.json, and layouts."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root or DEFAULT_DATA_ROOT)
        self.root.mkdir(parents=True, exist_ok=True)
        self.event_path = self.root / EVENT_LOG
        self.state_path = self.root / STATE_FILE
        self.layouts_path = self.root / LAYOUTS_FILE
        self.views_path = self.root / VIEWS_FILE

    # -- internal helpers --------------------------------------------------

    def _append_line(self, path: Path, obj: dict) -> None:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(obj, ensure_ascii=False) + "\n")

    def _read_jsonl(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        items: list[dict] = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        items.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return items

    # -- public API --------------------------------------------------------

    def append_event(self, op: dict, result: dict | None = None) -> str:
        event_id = _new_id("board_event")
        event = {
            "event_id": event_id,
            "op": op,
            "result": result or {},
            "timestamp": _ts(),
        }
        self._append_line(self.event_path, event)
        return event_id

    def materialize_state(self) -> GraphState:
        """Re-play entire event log to produce current state."""
        events = self._read_jsonl(self.event_path)
        state = GraphState()
        for evt in events:
            op = evt.get("op", {})
            if not validate_op(op).get("ok"):
                continue
            try:
                state, _ = apply_op(state, op)
            except Exception:
                # skip unplayable events
                continue
        # overlay persisted layouts if present
        if self.layouts_path.exists():
            try:
                with open(self.layouts_path, "r", encoding="utf-8") as fh:
                    layouts = json.load(fh)
                state.layouts.update(layouts)
            except Exception:
                pass
        # overlay persisted views if present
        if self.views_path.exists():
            try:
                views = self._read_jsonl(self.views_path)
                for v in views:
                    vid = v.get("id")
                    if vid:
                        state.views[vid] = v
            except Exception:
                pass
        return state

    def write_state(self, state: GraphState) -> None:
        with open(self.state_path, "w", encoding="utf-8") as fh:
            json.dump(state.to_dict(), fh, indent=2, ensure_ascii=False)

    def write_layouts(self, state: GraphState) -> None:
        with open(self.layouts_path, "w", encoding="utf-8") as fh:
            json.dump(state.layouts, fh, indent=2, ensure_ascii=False)

    def write_views(self, state: GraphState) -> None:
        # overwrite views file from state
        with open(self.views_path, "w", encoding="utf-8") as fh:
            for v in state.views.values():
                fh.write(json.dumps(v, ensure_ascii=False) + "\n")

    def get_event(self, event_id: str) -> dict | None:
        for evt in self._read_jsonl(self.event_path):
            if evt.get("event_id") == event_id:
                return evt
        return None


# ---------------------------------------------------------------------------
# Board view builder
# ---------------------------------------------------------------------------

def build_view(state: GraphState, view_id: str | None = None, filter_tags: list[str] | None = None) -> dict:
    """Return a serializable board view."""
    nodes = {}
    for nid, node in state.nodes.items():
        if node.get("deleted"):
            continue
        # tag filter
        if filter_tags:
            node_tags = set(node.get("tags", []))
            if not any(t in node_tags for t in filter_tags):
                continue
        nodes[nid] = node
    edges = {}
    for eid, edge in state.edges.items():
        if edge["from"] in nodes and edge["to"] in nodes:
            edges[eid] = edge
    groups = {}
    for gid, group in state.groups.items():
        visible = [nid for nid in group.get("node_ids", []) if nid in nodes]
        if visible:
            groups[gid] = {**group, "visible_node_ids": visible}
    layouts = {nid: state.layouts.get(nid, {"x": 0, "y": 0}) for nid in nodes}
    return {
        "version": state.version,
        "node_count": len(nodes),
        "edge_count": len(edges),
        "group_count": len(groups),
        "nodes": nodes,
        "edges": edges,
        "groups": groups,
        "layouts": layouts,
    }


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------

def layout_grid(state: GraphState, scope: list[str] | None = None, spacing: int = 180) -> dict:
    """Simple deterministic grid layout for nodes without coordinates."""
    targets = scope or [nid for nid, n in state.nodes.items() if not n.get("deleted")]
    patch: dict[str, dict] = {}
    cols = int(max(1, (len(targets) ** 0.5)))
    for i, nid in enumerate(targets):
        if nid not in state.nodes:
            continue
        x = (i % cols) * spacing
        y = (i // cols) * spacing
        patch[nid] = {"x": x, "y": y}
    return patch


def layout_state(state: GraphState, mode: str = "grid", scope: list[str] | None = None) -> GraphState:
    """Apply a layout mode and return updated state."""
    if mode == "grid":
        patch = layout_grid(state, scope)
        state.layouts.update(patch)
    return state


# ---------------------------------------------------------------------------
# High-level convenience
# ---------------------------------------------------------------------------

class BoardEngine:
    """Thin orchestrator over Store + State."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.store = BoardStore(root)
        self._state: GraphState | None = None

    @property
    def state(self) -> GraphState:
        if self._state is None:
            self._state = self.store.materialize_state()
        return self._state

    def refresh(self) -> GraphState:
        self._state = self.store.materialize_state()
        return self._state

    def commit(self, op: dict) -> dict:
        """Validate, apply, append event, and persist state."""
        v = validate_op(op, self.state)
        if not v["ok"]:
            return {"ok": False, "errors": v["errors"]}
        new_state, result = apply_op(self.state, op)
        event_id = self.store.append_event(op, result)
        self.store.write_state(new_state)
        self.store.write_layouts(new_state)
        self.store.write_views(new_state)
        self._state = new_state
        return {
            "ok": True,
            "event_id": event_id,
            "state_version": new_state.version,
            "result": result,
        }

    def commit_multi(self, ops: list[dict]) -> dict:
        """Commit a batch of ops atomically (all-or-nothing validation)."""
        test_state = copy.deepcopy(self.state)
        results: list[dict] = []
        for op in ops:
            v = validate_op(op, test_state)
            if not v["ok"]:
                return {"ok": False, "errors": v["errors"], "failed_op": op}
            try:
                test_state, res = apply_op(test_state, op)
            except Exception as exc:
                return {"ok": False, "errors": [str(exc)], "failed_op": op}
            results.append(res)
        event_ids: list[str] = []
        for op, res in zip(ops, results):
            eid = self.store.append_event(op, res)
            event_ids.append(eid)
        self.store.write_state(test_state)
        self.store.write_layouts(test_state)
        self.store.write_views(test_state)
        self._state = test_state
        return {
            "ok": True,
            "event_ids": event_ids,
            "state_version": test_state.version,
            "results": results,
        }

    def view(self, **kwargs: Any) -> dict:
        return build_view(self.state, **kwargs)

    def event(self, event_id: str) -> dict | None:
        return self.store.get_event(event_id)

    def rebuild(self) -> GraphState:
        """Force full materialization from event log."""
        return self.refresh()
