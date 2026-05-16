# NeuRecall — Lightweight Context Board and Custom Engine

A local-first context board for humans and AI agents. Visually arrange ideas, memories, files, GitHub history, route proofs, and agent work into a structured board that both humans and agents can read and update safely.

## Philosophy

The board is not a drawing. The board is a visual projection of a context graph.

- **Deterministic engine**: append-only `graph_events.jsonl` + materialized `graph_state.json`
- **Renderer independence**: SVG renderer today, optional tldraw adapter tomorrow
- **Agent-safe**: AI mutates the board through validated ops, not raw JSON
- **Alexandria-native**: memory refs, route refs, and D-ACCA packet expansion built in

## Quick Start

```bash
# 1. Start the HTTP service (serves API + UI)
python scripts/run_server.py --port 9147

# 2. Open the board
open http://127.0.0.1:9147/

# 3. Or use the API
curl -s http://127.0.0.1:9147/board/health
```

## Architecture

```
Alexandria D-ACCA engine
  dataset/corpus/corpus_items.jsonl
  routes/route_*.json
        |
        v
NeuRecall board engine
  graph/graph_events.jsonl   ← source of truth
  graph/graph_state.json     ← materialized view
  graph/layouts.json
  graph/board_views.jsonl
        |
        +--> local HTTP hooks  (MVP 1)
        +--> MCP tools         (MVP 3)
        +--> SVG renderer      (MVP 2)
        +--> importers         (MVP 4)
```

## Data Model

### Node
```json
{
  "id": "node_neurecall_engine",
  "kind": "idea",
  "label": "NeuRecall custom board engine",
  "summary": "...",
  "tags": ["neurecall", "engine"],
  "refs": [{"type": "memory", "id": "..."}],
  "provenance": {"created_by": "human", "created_at": "..."}
}
```

### Edge
```json
{
  "id": "edge_...",
  "from": "node_a",
  "to": "node_b",
  "relation": "uses",
  "label": "uses route proofs",
  "tags": ["architecture"]
}
```

## Board Operations

| Op | Description |
|---|---|
| `create_node` | Add a node |
| `update_node` | Edit label, summary, kind, tags, refs |
| `delete_node` | Soft delete (hard optional) |
| `connect` | Create an edge |
| `disconnect` | Remove an edge |
| `move_node` | Set layout coordinates |
| `group_nodes` | Create a group/subspace |
| `ungroup_nodes` | Remove a group |
| `collapse_subspace` / `expand_subspace` | Toggle group visibility |
| `tag_node` / `untag_node` | Manage tags |
| `attach_memory` / `attach_route` | Link Alexandria context |
| `create_view` / `set_view_filter` | Named views |
| `layout` | Batch coordinate update |
| `import_github_summary` | Import repo metadata |

## HTTP API

```text
GET  /board/health
GET  /board?tags=mvp,architecture
POST /board/update
POST /board/from-packet
POST /board/layout
GET  /board/event/{event_id}
GET  /board/view/{view_id}
```

## MCP Tools

```text
neurecall_get_board
neurecall_update_board
neurecall_attach_memory
neurecall_expand_from_query
neurecall_layout
neurecall_get_event
```

Run the MCP server over stdio:
```bash
python neurecall/mcp_server.py
```

## UI Shortcuts

| Key | Action |
|---|---|
| `v` | Select mode |
| `c` | Connect mode |
| `h` | Pan mode |
| `n` | New node |
| `g` | Group selected |
| `l` | Auto layout |
| `r` | Refresh |
| `Delete` | Delete selected |
| `Esc` | Clear selection |

## Importers

```bash
# GitHub (requires gh CLI or local git repo)
python -m neurecall.importers --github owner/repo

# Mermaid
python -m neurecall.importers --mermaid-in diagram.mmd --mermaid-out board.mmd
```

## Testing

```bash
python scripts/run_tests.py
```

## Storage

Default data root: `C:\ivy-data\alexandria\engine\graph\`

Override with environment variable:
```bash
set NEURECALL_DATA_ROOT=C:\path\to\graph
```

## License

MIT — built for local-first context systems.
