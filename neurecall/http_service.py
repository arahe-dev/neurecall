"""NeuRecall HTTP Hook Contract — built on stdlib http.server.

Endpoints:
  GET  /board/health
  GET  /board?project=alexandria
  POST /board/update
  POST /board/from-packet
  POST /board/layout
  GET  /board/event/{event_id}
  GET  /board/view/{view_id}
"""

from __future__ import annotations

import json
import mimetypes
import os
import re
import sys
import threading
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# allow running without install
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neurecall.engine import BoardEngine, layout_state, build_view, _new_id


# ---------------------------------------------------------------------------
# Shared engine instance (thread-safe via RLock)
# ---------------------------------------------------------------------------

_engine_lock = threading.RLock()
_engine: BoardEngine | None = None


def get_engine() -> BoardEngine:
    global _engine
    if _engine is None:
        root = os.environ.get("NEURECALL_DATA_ROOT")
        _engine = BoardEngine(root=root)
    return _engine


def set_engine(engine: BoardEngine) -> None:
    global _engine
    _engine = engine


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------

def _json_response(handler, status: int, data: dict) -> None:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler) -> dict | None:
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return None
    try:
        return json.loads(handler.rfile.read(length).decode("utf-8"))
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def _handle_health(handler) -> None:
    eng = get_engine()
    state = eng.state
    _json_response(handler, 200, {
        "ok": True,
        "version": state.version,
        "nodes": len(state.nodes),
        "edges": len(state.edges),
    })


def _handle_get_board(handler, query: dict) -> None:
    eng = get_engine()
    filter_tags = query.get("tags", "").split(",") if query.get("tags") else None
    if filter_tags:
        filter_tags = [t.strip() for t in filter_tags if t.strip()]
    view = build_view(eng.state, filter_tags=filter_tags)
    _json_response(handler, 200, {"ok": True, "board": view})


def _handle_post_board_update(handler) -> None:
    body = _read_json(handler)
    if body is None:
        _json_response(handler, 400, {"ok": False, "error": "Expected JSON body"})
        return
    ops = body if isinstance(body, list) else [body]
    eng = get_engine()
    with _engine_lock:
        if len(ops) == 1:
            res = eng.commit(ops[0])
        else:
            res = eng.commit_multi(ops)
    _json_response(handler, 200 if res["ok"] else 422, res)


def _handle_post_board_from_packet(handler) -> None:
    body = _read_json(handler)
    if not body or "packet" not in body:
        _json_response(handler, 400, {"ok": False, "error": "Missing 'packet' field"})
        return
    packet = body["packet"]
    proof = body.get("proof")
    ops = _packet_to_ops(packet, proof)
    eng = get_engine()
    with _engine_lock:
        res = eng.commit_multi(ops)
    _json_response(handler, 200 if res["ok"] else 422, res)


def _packet_to_ops(packet: dict, proof: dict | None = None) -> list[dict]:
    """Convert a D-ACCA-like packet into board ops."""
    ops: list[dict] = []
    # query node
    qnode_id = _new_id("node")
    ops.append({
        "op": "create_node",
        "payload": {
            "id": qnode_id,
            "kind": "packet",
            "label": packet.get("query", "Query"),
            "summary": packet.get("query", ""),
            "tags": ["packet", "auto"],
        },
    })
    # evidence nodes
    for item in packet.get("evidence", []):
        nid = _new_id("node")
        ops.append({
            "op": "create_node",
            "payload": {
                "id": nid,
                "kind": "memory_ref",
                "label": item.get("title", "Evidence"),
                "summary": item.get("summary", "")[:200],
                "tags": ["evidence", "auto"],
                "refs": [{"type": "memory", "id": item.get("memory_id", "")}],
            },
        })
        ops.append({
            "op": "connect",
            "payload": {"from": qnode_id, "to": nid, "relation": "relates_to"},
        })
    # proof node
    if proof:
        pid = _new_id("node")
        ops.append({
            "op": "create_node",
            "payload": {
                "id": pid,
                "kind": "route_ref",
                "label": f"Proof: {proof.get('route_id', '')}",
                "tags": ["proof", "auto"],
                "refs": [{"type": "route", "id": proof.get("route_id", "")}],
            },
        })
        ops.append({
            "op": "connect",
            "payload": {"from": qnode_id, "to": pid, "relation": "proves"},
        })
    return ops


def _handle_post_board_layout(handler) -> None:
    body = _read_json(handler)
    if not body:
        _json_response(handler, 400, {"ok": False, "error": "Expected JSON body"})
        return
    mode = body.get("mode", "grid")
    scope = body.get("scope")
    eng = get_engine()
    with _engine_lock:
        layout_state(eng.state, mode=mode, scope=scope)
        eng.store.write_state(eng.state)
        eng.store.write_layouts(eng.state)
    _json_response(handler, 200, {"ok": True, "mode": mode})


def _handle_get_event(handler, event_id: str) -> None:
    evt = get_engine().event(event_id)
    if evt is None:
        _json_response(handler, 404, {"ok": False, "error": "Event not found"})
        return
    _json_response(handler, 200, {"ok": True, "event": evt})


def _handle_get_view(handler, view_id: str) -> None:
    state = get_engine().state
    if view_id not in state.views:
        _json_response(handler, 404, {"ok": False, "error": "View not found"})
        return
    v = state.views[view_id]
    filter_tags = v.get("filter_tags")
    node_ids = v.get("node_ids")
    board = build_view(state, filter_tags=filter_tags)
    if node_ids:
        board["nodes"] = {k: n for k, n in board["nodes"].items() if k in node_ids}
        board["edges"] = {k: e for k, e in board["edges"].items() if e["from"] in node_ids and e["to"] in node_ids}
    _json_response(handler, 200, {"ok": True, "view": v, "board": board})


# ---------------------------------------------------------------------------
# Request router
# ---------------------------------------------------------------------------

def _serve_static(handler, path: str) -> None:
    static_dir = Path(__file__).resolve().parent.parent / "static"
    if path == "/":
        path = "/index.html"
    # strip /static/ prefix since static_dir already points to static folder
    rel = path.lstrip("/")
    if rel.startswith("static/"):
        rel = rel[7:]
    file_path = (static_dir / rel).resolve()
    # security: prevent escaping static dir
    if not str(file_path).startswith(str(static_dir.resolve())):
        _json_response(handler, 403, {"ok": False, "error": "Forbidden"})
        return
    if not file_path.exists():
        _json_response(handler, 404, {"ok": False, "error": "Not found"})
        return
    content_type, _ = mimetypes.guess_type(str(file_path))
    content_type = content_type or "application/octet-stream"
    with open(file_path, "rb") as f:
        data = f.read()
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(data)


class BoardHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        # quiet logs
        pass

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        # flatten single-item query lists
        query = {k: v[0] if len(v) == 1 else v for k, v in query.items()}

        if path == "/board/health":
            _handle_health(self)
        elif path == "/board":
            _handle_get_board(self, query)
        elif re.match(r"^/board/event/[^/]+$", path):
            event_id = path.split("/")[-1]
            _handle_get_event(self, event_id)
        elif re.match(r"^/board/view/[^/]+$", path):
            view_id = path.split("/")[-1]
            _handle_get_view(self, view_id)
        elif path == "/" or path.startswith("/static/"):
            _serve_static(self, path)
        else:
            _json_response(self, 404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == "/board/update":
            _handle_post_board_update(self)
        elif path == "/board/from-packet":
            _handle_post_board_from_packet(self)
        elif path == "/board/layout":
            _handle_post_board_layout(self)
        else:
            _json_response(self, 404, {"ok": False, "error": "Not found"})


# ---------------------------------------------------------------------------
# Server entrypoint
# ---------------------------------------------------------------------------

def run_server(host: str = "127.0.0.1", port: int = 9147) -> None:
    server = HTTPServer((host, port), BoardHandler)
    print(f"NeuRecall board service running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="NeuRecall HTTP service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9147)
    parser.add_argument("--data-root", default=None, help="Override NEURECALL_DATA_ROOT")
    args = parser.parse_args()
    if args.data_root:
        os.environ["NEURECALL_DATA_ROOT"] = args.data_root
    run_server(host=args.host, port=args.port)
