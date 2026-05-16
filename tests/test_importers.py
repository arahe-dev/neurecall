"""Tests for neurecall.importers — Mermaid, packet expansion, GitHub stubs."""

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neurecall.engine import BoardEngine
from neurecall.importers import import_mermaid, export_mermaid, expand_from_packet


def _fresh_engine():
    tmp = tempfile.mkdtemp(prefix="neurecall_imp_")
    return BoardEngine(root=tmp), tmp


def _cleanup(tmp):
    shutil.rmtree(tmp, ignore_errors=True)


def test_import_mermaid_basic() -> None:
    eng, tmp = _fresh_engine()
    text = """
flowchart LR
    a([Idea A])
    b([Idea B])
    a --> b
"""
    res = import_mermaid(eng, text)
    assert res["ok"] is True
    assert res["ops_count"] == 3
    assert len(eng.state.nodes) == 2
    assert len(eng.state.edges) == 1
    _cleanup(tmp)


def test_import_mermaid_with_relations() -> None:
    eng, tmp = _fresh_engine()
    text = """
flowchart LR
    a([Node A])
    b([Node B])
    a -- uses --> b
"""
    res = import_mermaid(eng, text)
    assert res["ok"] is True
    edge = list(eng.state.edges.values())[0]
    assert edge["relation"] == "uses"
    _cleanup(tmp)


def test_export_mermaid_roundtrip() -> None:
    eng, tmp = _fresh_engine()
    eng.commit({"op": "create_node", "payload": {"id": "n1", "label": "A", "kind": "idea"}})
    eng.commit({"op": "create_node", "payload": {"id": "n2", "label": "B", "kind": "task"}})
    eng.commit({"op": "connect", "payload": {"from": "n1", "to": "n2", "relation": "depends_on"}})
    out = export_mermaid(eng)
    assert "flowchart LR" in out
    assert "A" in out
    assert "B" in out
    assert "depends_on" in out
    _cleanup(tmp)


def test_expand_from_packet() -> None:
    eng, tmp = _fresh_engine()
    packet = {
        "query": "NeuRecall design",
        "evidence": [
            {"title": "PRD", "memory_id": "mem_prd"},
            {"title": "Sketch", "memory_id": "mem_sketch"},
        ],
    }
    proof = {"route_id": "route_42"}
    res = expand_from_packet(eng, packet, proof)
    assert res["ok"] is True
    assert res["ops_count"] >= 4  # query + 2 evidence + proof + edges
    _cleanup(tmp)


if __name__ == "__main__":
    import traceback
    failures = 0
    tests = [
        test_import_mermaid_basic,
        test_import_mermaid_with_relations,
        test_export_mermaid_roundtrip,
        test_expand_from_packet,
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
