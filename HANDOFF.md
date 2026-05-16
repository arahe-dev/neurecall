# NeuRecall — Handoff Document

**Date:** 2026-05-16  
**Branch:** `master`  
**Commit:** `8ff4b86`  
**Data Root:** `C:\ivy-data\alexandria\engine\graph\` (default) or env `NEURECALL_DATA_ROOT`

---

## What Exists

### MVP 0 — Headless Engine ✅
- **Deterministic graph reducer** (`neurecall/engine.py`, 850+ lines)
- 16 board operations with validation, soft-delete by default
- Append-only `graph_events.jsonl` + materialized `graph_state.json`
- Batch commit with all-or-nothing validation
- Deterministic replay: same event log → same state

### MVP 1 — HTTP Hooks ✅
- **stdlib HTTP server** (`neurecall/http_service.py`), no external deps
- CORS enabled, serves static files
- Endpoints:
  - `GET /board/health`
  - `GET /board?tags=...`
  - `POST /board/update` (single or batch ops)
  - `POST /board/from-packet` (D-ACCA expansion)
  - `POST /board/layout` (6 layout modes)
  - `GET /board/event/{id}`
  - `GET /board/view/{id}`

### MVP 2 — SVG Renderer ✅
- **Vanilla JS** + SVG, no canvas library
- Pan, zoom, drag-to-move, connect mode
- Group rectangles (dashed blue) with collapse/expand/ungroup
- Tag filter chips, text search filter
- Detail sidebar (label, kind, summary, tags, save, delete)
- Keyboard shortcuts: `v` select, `c` connect, `h` pan, `n` new, `g` group, `r` refresh, `Del` delete, `Esc` clear

### MVP 3 — MCP Server ✅
- **stdio JSON-RPC** (`neurecall/mcp_server.py`)
- 6 tools exposed:
  - `neurecall_get_board`
  - `neurecall_update_board`
  - `neurecall_attach_memory`
  - `neurecall_expand_from_query`
  - `neurecall_layout`
  - `neurecall_get_event`

### MVP 4 — Importers ✅
- GitHub repo summary (via `gh` CLI or local git path)
- Mermaid flowchart import/export
- Alexandria D-ACCA packet expansion

### 5 Layout Modes ✅
| Mode | Shortcut | Description |
|------|----------|-------------|
| Grid | `1` / ⌘ | Dense rows & columns |
| Radial | `2` / ◎ | Hub & spoke by BFS depth |
| Hierarchy | `3` / ▤ | Top-down tree levels |
| Force-Directed | `4` / ✦ | **Best for large graphs** — physics sim |
| Concentric Groups | `5` / ◉ | Each group on its own ring |
| Spiral | `6` / 🌀 | Archimedean spiral sweep |

### Tests ✅
- 32 tests across `test_engine.py` (20), `test_http.py` (8), `test_importers.py` (4)
- All passing via `python scripts/run_tests.py`

---

## How to Run

```bash
# Start server (API + UI)
python scripts/run_server.py --port 9147

# Open browser
open http://127.0.0.1:9147

# Run tests
python scripts/run_tests.py

# Override data root
set NEURECALL_DATA_ROOT=C:\path\to\graph
python scripts/run_server.py
```

---

## Board Demo State

A populated demo board exists at a temp directory (check `server.log` for path). It contains:
- **94 nodes** (74 Alexandria memories + 20 filler ideas/tasks)
- **144 edges** (group chains, tag-based stars, cross-group bridges, filler-to-memory links)
- **6 groups** (Doc Memory, Workflow Trace, Benchmark Artifact, Safety Policy, Runbook, Backlog/Ideas)
- **0 orphans** — every node connected

To recreate from scratch:
```bash
python scripts/populate_board.py   # creates temp board with Alexandria memories
python scripts/connect_nodes.py    # adds edges
```

---

## Alexandria Integration

- Alexandria MCP is **live** at `127.0.0.1:8790/mcp`
- D-ACCA hook service at `127.0.0.1:8767`
- 80 memories indexed (includes 2 NeuRecall memories: PRD + build summary)
- Board can expand D-ACCA packets via `/board/from-packet`
- Memory refs stored as compact pointers (`{"type": "memory", "id": "..."}`) not full text

---

## Known Issues / Next Work

### P0 — None

### P1 — Visual Polish
- **Edge hairball**: 144 edges on 94 nodes creates visual clutter. Consider:
  - Edge bundling (group-level edges)
  - Curved SVG paths instead of straight lines
  - Edge opacity by weight or fade distant edges
- **No curved edges**: Currently straight `line` elements; bezier paths would look cleaner
- **Group label overlap**: Large groups overflow their dashed rectangles when expanded

### P2 — Missing Features
- **No tldraw adapter** — PRD mentions optional adapter; not built yet
- **No undo/redo** — event log supports it, no UI exposure
- **No real-time collaboration** — out of MVP scope
- **GitHub import only works with `gh` CLI** — no OAuth/web fallback
- **No edge labels rendered** — edges have `relation` field but SVG doesn't show it

### P3 — Nice to Have
- Minimap overview
- Snap-to-grid toggle
- Export board as PNG/SVG from UI
- Search across memory text (not just node labels)
- Animated layout transitions

---

## Architecture Decisions

| Decision | Rationale |
|----------|-----------|
| stdlib only, no Flask/FastAPI | Matches Alexandria style, zero deps |
| SVG not Canvas | Renderer independence, DOM inspectable |
| Event log as source of truth | Deterministic replay, audit trail |
| Soft delete default | Safety, reversible |
| Memory refs not full text | Keeps board compact, Alexandria owns corpus |
| Groups separate from nodes | Collapse/expand without data loss |

---

## File Map

```
neurecall/
  engine.py          — Graph reducer, 16 ops, 5 layout algorithms
  http_service.py    — HTTP API + static file server
  mcp_server.py      — stdio JSON-RPC MCP server
  importers.py       — GitHub, Mermaid, Alexandria packet
static/
  index.html         — Board UI shell
  board.js           — SVG renderer, interactions, 6 layout buttons
  styles.css         — Calm minimal styling
tests/
  test_engine.py     — 20 engine tests
  test_http.py       — 8 integration tests
  test_importers.py  — 4 importer tests
scripts/
  run_server.py      — Convenience launcher
  run_tests.py       — Test runner
  populate_board.py  — Demo board generator (Alexandria memories + fillers)
  connect_nodes.py   — Edge generator for demo board
```

---

## Contacts / Context

- **Owner:** IVY / Alexandria local systems lab
- **Related:** Alexandria D-ACCA engine, route proofs, context admission
- **Repo:** https://github.com/arahe-dev/neurecall
- **PRD:** `C:/ivy/docs/NEURECALL_CUSTOM_BOARD_ENGINE_PRD_2026-05-16.md`

---

*Last updated by Pi agent during MVP build session. Next session should start with visual polish (curved edges, edge bundling) or the tldraw adapter exploration.*
