"""Tests for neurecall.http_service — lightweight integration over stdlib server."""

import json
import shutil
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neurecall.http_service import run_server, set_engine, get_engine, BoardHandler
from neurecall.engine import BoardEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_engine():
    tmp = tempfile.mkdtemp(prefix="neurecall_http_")
    eng = BoardEngine(root=tmp)
    set_engine(eng)
    return eng, tmp


def _cleanup(tmp):
    shutil.rmtree(tmp, ignore_errors=True)


def _request(method: str, url: str, data: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, method=method)
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


# ---------------------------------------------------------------------------
# Fixture: start server on ephemeral port
# ---------------------------------------------------------------------------

def _start_server() -> tuple[str, int]:
    from http.server import HTTPServer
    import socket

    # find free port
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    _, port = sock.getsockname()
    sock.close()

    server = HTTPServer(("127.0.0.1", port), BoardHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.2)
    return "http://127.0.0.1:{}".format(port), port


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_health() -> None:
    eng, tmp = _fresh_engine()
    base, _ = _start_server()
    status, data = _request("GET", f"{base}/board/health")
    assert status == 200
    assert data["ok"] is True
    assert "version" in data
    _cleanup(tmp)


def test_get_board_empty() -> None:
    eng, tmp = _fresh_engine()
    base, _ = _start_server()
    status, data = _request("GET", f"{base}/board")
    assert status == 200
    assert data["board"]["node_count"] == 0
    _cleanup(tmp)


def test_post_update_create_node() -> None:
    eng, tmp = _fresh_engine()
    base, _ = _start_server()
    op = {"op": "create_node", "payload": {"label": "HTTP Test", "kind": "idea"}}
    status, data = _request("POST", f"{base}/board/update", op)
    assert status == 200
    assert data["ok"] is True
    assert "event_id" in data
    # board should now have 1 node
    status2, data2 = _request("GET", f"{base}/board")
    assert data2["board"]["node_count"] == 1
    _cleanup(tmp)


def test_post_update_multi() -> None:
    eng, tmp = _fresh_engine()
    base, _ = _start_server()
    ops = [
        {"op": "create_node", "payload": {"label": "A", "id": "node_a"}},
        {"op": "create_node", "payload": {"label": "B", "id": "node_b"}},
        {"op": "connect", "payload": {"from": "node_a", "to": "node_b"}},
    ]
    status, data = _request("POST", f"{base}/board/update", ops)
    assert status == 200
    assert data["ok"] is True
    assert len(data["event_ids"]) == 3
    _cleanup(tmp)


def test_post_layout() -> None:
    eng, tmp = _fresh_engine()
    base, _ = _start_server()
    # seed nodes
    for i in range(3):
        _request("POST", f"{base}/board/update", {"op": "create_node", "payload": {"label": str(i)}})
    status, data = _request("POST", f"{base}/board/layout", {"mode": "grid"})
    assert status == 200
    assert data["ok"] is True
    _cleanup(tmp)


def test_get_event() -> None:
    eng, tmp = _fresh_engine()
    base, _ = _start_server()
    op = {"op": "create_node", "payload": {"label": "Evt"}}
    _, res = _request("POST", f"{base}/board/update", op)
    eid = res["event_id"]
    status, data = _request("GET", f"{base}/board/event/{eid}")
    assert status == 200
    assert data["event"]["event_id"] == eid
    _cleanup(tmp)


def test_from_packet() -> None:
    eng, tmp = _fresh_engine()
    base, _ = _start_server()
    packet = {
        "query": "NeuRecall architecture",
        "evidence": [
            {"title": "PRD", "memory_id": "mem_prd"},
            {"title": "Sketch", "memory_id": "mem_sketch"},
        ],
    }
    proof = {"route_id": "route_123"}
    status, data = _request("POST", f"{base}/board/from-packet", {"packet": packet, "proof": proof})
    assert status == 200
    assert data["ok"] is True
    # verify board has nodes
    _, board_data = _request("GET", f"{base}/board")
    assert board_data["board"]["node_count"] >= 3  # query + 2 evidence + proof
    _cleanup(tmp)


def test_view_crud() -> None:
    eng, tmp = _fresh_engine()
    base, _ = _start_server()
    # create view via update (create_view op)
    _request("POST", f"{base}/board/update", {"op": "create_node", "payload": {"label": "A", "id": "node_a", "tags": ["mvp"]}})
    _request("POST", f"{base}/board/update", {"op": "create_node", "payload": {"label": "B", "id": "node_b", "tags": ["later"]}})
    _request("POST", f"{base}/board/update", {"op": "create_view", "payload": {"id": "view_mvp", "label": "MVP Only", "filter_tags": ["mvp"]}})
    status, data = _request("GET", f"{base}/board/view/view_mvp")
    assert status == 200
    assert "node_a" in data["board"]["nodes"]
    assert "node_b" not in data["board"]["nodes"]
    _cleanup(tmp)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import traceback

    failures = 0
    tests = [
        test_health,
        test_get_board_empty,
        test_post_update_create_node,
        test_post_update_multi,
        test_post_layout,
        test_get_event,
        test_from_packet,
        test_view_crud,
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
