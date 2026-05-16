"""NeuRecall MCP Server — stdio JSON-RPC for Model Context Protocol.

Tools:
  neurecall_get_board
  neurecall_update_board
  neurecall_attach_memory
  neurecall_expand_from_query
  neurecall_layout
  neurecall_get_event
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neurecall.engine import BoardEngine, build_view, layout_state, validate_op, apply_op


# ---------------------------------------------------------------------------
# MCP Protocol helpers
# ---------------------------------------------------------------------------

_engine: BoardEngine | None = None


def get_engine() -> BoardEngine:
    global _engine
    if _engine is None:
        root = os.environ.get("NEURECALL_DATA_ROOT")
        _engine = BoardEngine(root=root)
    return _engine


def send(msg: dict) -> None:
    payload = json.dumps(msg)
    sys.stdout.write(payload + "\n")
    sys.stdout.flush()


def log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "neurecall_get_board",
        "description": "Get the current board state or a filtered view.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "filter_tags": {"type": "array", "items": {"type": "string"}, "description": "Tags to filter by"},
                "view_id": {"type": "string", "description": "Named view ID"},
            },
        },
    },
    {
        "name": "neurecall_update_board",
        "description": "Apply one or more validated board operations.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ops": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "List of board ops to apply atomically",
                },
            },
            "required": ["ops"],
        },
    },
    {
        "name": "neurecall_attach_memory",
        "description": "Attach an Alexandria memory reference to a board node.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "node_id": {"type": "string"},
                "memory_id": {"type": "string"},
                "route_id": {"type": "string"},
            },
            "required": ["node_id", "memory_id"],
        },
    },
    {
        "name": "neurecall_expand_from_query",
        "description": "Expand a D-ACCA-like query packet into board nodes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "evidence": {"type": "array", "items": {"type": "object"}},
                "route_id": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "neurecall_layout",
        "description": "Apply an automatic layout to the board.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["grid"]},
                "scope": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "neurecall_get_event",
        "description": "Retrieve a board event by ID.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "event_id": {"type": "string"},
            },
            "required": ["event_id"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_get_board(args: dict) -> dict:
    eng = get_engine()
    filter_tags = args.get("filter_tags")
    view_id = args.get("view_id")
    if view_id and view_id in eng.state.views:
        v = eng.state.views[view_id]
        ft = v.get("filter_tags")
        board = build_view(eng.state, filter_tags=ft)
        node_ids = v.get("node_ids")
        if node_ids:
            board["nodes"] = {k: n for k, n in board["nodes"].items() if k in node_ids}
            board["edges"] = {k: e for k, e in board["edges"].items() if e["from"] in node_ids and e["to"] in node_ids}
        return {"content": [{"type": "text", "text": json.dumps(board, indent=2)}]}
    board = build_view(eng.state, filter_tags=filter_tags)
    return {"content": [{"type": "text", "text": json.dumps(board, indent=2)}]}


def handle_update_board(args: dict) -> dict:
    ops = args.get("ops", [])
    if not ops:
        return {"isError": True, "content": [{"type": "text", "text": "No ops provided"}]}
    eng = get_engine()
    if len(ops) == 1:
        res = eng.commit(ops[0])
    else:
        res = eng.commit_multi(ops)
    if not res["ok"]:
        return {"isError": True, "content": [{"type": "text", "text": json.dumps(res, indent=2)}]}
    return {"content": [{"type": "text", "text": json.dumps(res, indent=2)}]}


def handle_attach_memory(args: dict) -> dict:
    eng = get_engine()
    op = {
        "op": "attach_memory",
        "payload": {
            "node_id": args["node_id"],
            "memory_id": args["memory_id"],
            "route_id": args.get("route_id"),
        },
    }
    res = eng.commit(op)
    if not res["ok"]:
        return {"isError": True, "content": [{"type": "text", "text": json.dumps(res, indent=2)}]}
    return {"content": [{"type": "text", "text": f"Memory attached. Event: {res['event_id']}"}]}


def handle_expand_from_query(args: dict) -> dict:
    packet = {
        "query": args["query"],
        "evidence": args.get("evidence", []),
    }
    proof = {"route_id": args.get("route_id", "")}
    from neurecall.http_service import _packet_to_ops
    ops = _packet_to_ops(packet, proof)
    eng = get_engine()
    res = eng.commit_multi(ops)
    if not res["ok"]:
        return {"isError": True, "content": [{"type": "text", "text": json.dumps(res, indent=2)}]}
    return {"content": [{"type": "text", "text": f"Expanded query into {len(ops)} ops. Events: {res['event_ids']}"}]}


def handle_layout(args: dict) -> dict:
    eng = get_engine()
    mode = args.get("mode", "grid")
    scope = args.get("scope")
    layout_state(eng.state, mode=mode, scope=scope)
    eng.store.write_state(eng.state)
    eng.store.write_layouts(eng.state)
    return {"content": [{"type": "text", "text": f"Layout applied: {mode}"}]}


def handle_get_event(args: dict) -> dict:
    evt = get_engine().event(args["event_id"])
    if evt is None:
        return {"isError": True, "content": [{"type": "text", "text": "Event not found"}]}
    return {"content": [{"type": "text", "text": json.dumps(evt, indent=2)}]}


TOOL_HANDLERS = {
    "neurecall_get_board": handle_get_board,
    "neurecall_update_board": handle_update_board,
    "neurecall_attach_memory": handle_attach_memory,
    "neurecall_expand_from_query": handle_expand_from_query,
    "neurecall_layout": handle_layout,
    "neurecall_get_event": handle_get_event,
}


# ---------------------------------------------------------------------------
# Message dispatch
# ---------------------------------------------------------------------------

def handle_request(req: dict) -> dict | None:
    method = req.get("method")
    req_id = req.get("id")
    params = req.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "neurecall-mcp", "version": "0.1.0"},
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"tools": TOOLS},
        }

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {})
        handler = TOOL_HANDLERS.get(name)
        if handler is None:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Unknown tool: {name}"},
            }
        try:
            result = handler(args)
        except Exception as exc:
            log(traceback.format_exc())
            result = {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": result,
        }

    # notifications have no id
    if req_id is None:
        return None

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def main() -> None:
    log("NeuRecall MCP server starting…")
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            req = json.loads(line)
            resp = handle_request(req)
            if resp:
                send(resp)
        except json.JSONDecodeError as exc:
            log(f"JSON decode error: {exc}")
        except Exception as exc:
            log(f"Unhandled error: {exc}")
            log(traceback.format_exc())


if __name__ == "__main__":
    main()
