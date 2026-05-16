/** NeuRecall Board Renderer — SVG-based, vanilla JS. */

const API_BASE = window.location.origin;

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

const state = {
  board: null,
  selected: new Set(),
  mode: 'select', // select | connect | pan
  pan: { x: 0, y: 0 },
  zoom: 1,
  dragging: null,
  dragOffset: { x: 0, y: 0 },
  connectFrom: null,
  filterText: '',
  filterTags: new Set(),
};

// ---------------------------------------------------------------------------
// DOM refs
// ---------------------------------------------------------------------------

const svg = document.getElementById('board-svg');
const mainGroup = document.getElementById('main-group');
const groupsGroup = document.getElementById('groups-group');
const edgesGroup = document.getElementById('edges-group');
const nodesGroup = document.getElementById('nodes-group');
const sidebar = document.getElementById('sidebar');
const statusbar = document.getElementById('statusbar');

// ---------------------------------------------------------------------------
// API helpers
// ---------------------------------------------------------------------------

async function api(method, path, body) {
  const opts = { method, headers: {} };
  if (body) {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(`${API_BASE}${path}`, opts);
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, data };
}

async function loadBoard() {
  const tags = Array.from(state.filterTags).join(',');
  const qs = tags ? `?tags=${encodeURIComponent(tags)}` : '';
  const { data } = await api('GET', `/board${qs}`);
  if (data.ok) {
    state.board = data.board;
    render();
    updateStatus();
  }
}

async function commitOp(op) {
  const res = await api('POST', '/board/update', op);
  if (!res.ok) {
    console.error('Board update failed', res.data);
    alert('Update failed: ' + (res.data.error || JSON.stringify(res.data.errors)));
    return null;
  }
  await loadBoard();
  return res.data;
}

async function commitOps(ops) {
  const res = await api('POST', '/board/update', ops);
  if (!res.ok) {
    console.error('Batch update failed', res.data);
    alert('Batch update failed: ' + (res.data.error || JSON.stringify(res.data.errors)));
    return null;
  }
  await loadBoard();
  return res.data;
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function render() {
  if (!state.board) return;
  renderGroups();
  renderEdges();
  renderNodes();
  applyTransform();
}

function renderGroups() {
  groupsGroup.innerHTML = '';
  for (const [gid, group] of Object.entries(state.board.groups || {})) {
    const visible = group.visible_node_ids || group.node_ids || [];
    if (visible.length === 0) continue;

    // Compute bounding box from member layouts
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    for (const nid of visible) {
      const pos = state.board.layouts[nid];
      if (!pos) continue;
      minX = Math.min(minX, pos.x);
      minY = Math.min(minY, pos.y);
      maxX = Math.max(maxX, pos.x);
      maxY = Math.max(maxY, pos.y);
    }
    if (!isFinite(minX)) continue;

    const padding = 24;
    const x = minX - NODE_W / 2 - padding;
    const y = minY - NODE_H / 2 - padding;
    const w = maxX - minX + NODE_W + padding * 2;
    const h = maxY - minY + NODE_H + padding * 2;

    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('data-group-id', gid);
    g.style.cursor = 'pointer';

    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    rect.setAttribute('x', x);
    rect.setAttribute('y', y);
    rect.setAttribute('width', w);
    rect.setAttribute('height', h);
    rect.setAttribute('class', 'group-rect' + (group.collapsed ? ' collapsed' : ''));
    rect.setAttribute('rx', 10);
    rect.setAttribute('ry', 10);
    g.appendChild(rect);

    const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    label.setAttribute('x', x + 10);
    label.setAttribute('y', y + 18);
    label.setAttribute('class', 'group-label');
    label.textContent = (group.label || 'Group') + (group.collapsed ? ' ▶' : ' ▼');
    g.appendChild(label);

    const count = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    count.setAttribute('x', x + 10);
    count.setAttribute('y', y + h - 8);
    count.setAttribute('class', 'group-count');
    count.textContent = `${visible.length} node${visible.length !== 1 ? 's' : ''}`;
    g.appendChild(count);

    g.addEventListener('click', (e) => {
      e.stopPropagation();
      if (group.collapsed) {
        commitOp({ op: 'expand_subspace', payload: { group_id: gid } });
      } else {
        commitOp({ op: 'collapse_subspace', payload: { group_id: gid } });
      }
    });
    g.addEventListener('dblclick', (e) => {
      e.stopPropagation();
      if (confirm('Ungroup this subspace?')) {
        commitOp({ op: 'ungroup_nodes', payload: { group_id: gid } });
      }
    });

    groupsGroup.appendChild(g);
  }
}

function renderEdges() {
  edgesGroup.innerHTML = '';
  // Build set of nodes in collapsed groups
  const hiddenByGroup = new Set();
  for (const group of Object.values(state.board.groups || {})) {
    if (group.collapsed) {
      (group.visible_node_ids || group.node_ids || []).forEach(n => hiddenByGroup.add(n));
    }
  }
  for (const [eid, edge] of Object.entries(state.board.edges || {})) {
    if (hiddenByGroup.has(edge.from) || hiddenByGroup.has(edge.to)) continue;
    const from = state.board.layouts[edge.from];
    const to = state.board.layouts[edge.to];
    if (!from || !to) continue;
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', from.x);
    line.setAttribute('y1', from.y);
    line.setAttribute('x2', to.x);
    line.setAttribute('y2', to.y);
    line.setAttribute('class', 'edge-line');
    line.setAttribute('data-id', eid);
    edgesGroup.appendChild(line);
  }
}

const NODE_W = 140;
const NODE_H = 70;

function renderNodes() {
  nodesGroup.innerHTML = '';
  // Build set of nodes in collapsed groups
  const hiddenByGroup = new Set();
  for (const group of Object.values(state.board.groups || {})) {
    if (group.collapsed) {
      (group.visible_node_ids || group.node_ids || []).forEach(n => hiddenByGroup.add(n));
    }
  }
  for (const [nid, node] of Object.entries(state.board.nodes || {})) {
    const pos = state.board.layouts[nid] || { x: 0, y: 0 };
    const isHidden = hiddenByGroup.has(nid);
    const g = document.createElementNS('http://www.w3.org/2000/svg', 'g');
    g.setAttribute('transform', `translate(${pos.x - NODE_W / 2}, ${pos.y - NODE_H / 2})`);
    g.setAttribute('data-id', nid);
    g.style.cursor = state.mode === 'connect' ? 'crosshair' : 'pointer';
    if (isHidden) {
      g.style.display = 'none';
    }

    const rect = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
    rect.setAttribute('width', NODE_W);
    rect.setAttribute('height', NODE_H);
    rect.setAttribute('class', 'node-rect' + (state.selected.has(nid) ? ' selected' : '') + (node.deleted ? ' deleted' : ''));
    rect.setAttribute('rx', 6);
    rect.setAttribute('ry', 6);
    g.appendChild(rect);

    const kind = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    kind.setAttribute('x', 8);
    kind.setAttribute('y', 16);
    kind.setAttribute('class', 'node-kind');
    kind.textContent = node.kind || 'node';
    g.appendChild(kind);

    const label = document.createElementNS('http://www.w3.org/2000/svg', 'text');
    label.setAttribute('x', NODE_W / 2);
    label.setAttribute('y', NODE_H / 2 + 4);
    label.setAttribute('text-anchor', 'middle');
    label.setAttribute('class', 'node-label');
    label.textContent = truncate(node.label || nid, 18);
    g.appendChild(label);

    // Tags dots
    const tags = node.tags || [];
    tags.slice(0, 4).forEach((tag, i) => {
      const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      dot.setAttribute('cx', 10 + i * 10);
      dot.setAttribute('cy', NODE_H - 10);
      dot.setAttribute('r', 3);
      dot.setAttribute('fill', stringColor(tag));
      g.appendChild(dot);
    });

    g.addEventListener('mousedown', (e) => onNodeMouseDown(e, nid));
    nodesGroup.appendChild(g);
  }
}

function applyTransform() {
  mainGroup.setAttribute('transform', `translate(${state.pan.x}, ${state.pan.y}) scale(${state.zoom})`);
}

function truncate(str, n) {
  return str.length > n ? str.slice(0, n - 1) + '…' : str;
}

function stringColor(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) h = str.charCodeAt(i) + ((h << 5) - h);
  return `hsl(${Math.abs(h) % 360}, 60%, 50%)`;
}

function updateStatus() {
  const b = state.board;
  if (!b) return;
  statusbar.textContent = `Nodes: ${b.node_count} | Edges: ${b.edge_count} | Groups: ${b.group_count} | Zoom: ${Math.round(state.zoom * 100)}%`;
}

// ---------------------------------------------------------------------------
// Interactions
// ---------------------------------------------------------------------------

function onNodeMouseDown(e, nid) {
  e.stopPropagation();
  if (state.mode === 'connect') {
    if (!state.connectFrom) {
      state.connectFrom = nid;
      highlightNode(nid, true);
    } else if (state.connectFrom !== nid) {
      commitOp({ op: 'connect', payload: { from: state.connectFrom, to: nid } });
      highlightNode(state.connectFrom, false);
      state.connectFrom = null;
    }
    return;
  }

  if (e.shiftKey) {
    toggleSelection(nid);
  } else {
    state.selected = new Set([nid]);
  }
  state.dragging = nid;
  const pos = state.board.layouts[nid] || { x: 0, y: 0 };
  const pt = toBoardPoint(e.clientX, e.clientY);
  state.dragOffset = { x: pt.x - pos.x, y: pt.y - pos.y };
  render();
  showDetails(nid);
}

function highlightNode(nid, on) {
  const g = nodesGroup.querySelector(`[data-id="${nid}"] .node-rect`);
  if (g) g.classList.toggle('selected', on);
}

function toggleSelection(nid) {
  if (state.selected.has(nid)) state.selected.delete(nid);
  else state.selected.add(nid);
}

function toBoardPoint(cx, cy) {
  const rect = svg.getBoundingClientRect();
  return {
    x: (cx - rect.left - state.pan.x) / state.zoom,
    y: (cy - rect.top - state.pan.y) / state.zoom,
  };
}

// Pan & drag on SVG
let isPanning = false;
let panStart = { x: 0, y: 0 };

svg.addEventListener('mousedown', (e) => {
  if (e.target === svg || e.target === mainGroup || e.target.id === 'canvas-wrap') {
    if (state.mode === 'pan' || e.button === 1 || (e.button === 0 && e.altKey)) {
      isPanning = true;
      panStart = { x: e.clientX - state.pan.x, y: e.clientY - state.pan.y };
      svg.style.cursor = 'grabbing';
    } else if (state.mode === 'select') {
      state.selected.clear();
      render();
      hideDetails();
    }
  }
});

window.addEventListener('mousemove', (e) => {
  if (isPanning) {
    state.pan.x = e.clientX - panStart.x;
    state.pan.y = e.clientY - panStart.y;
    applyTransform();
    return;
  }
  if (state.dragging) {
    const pt = toBoardPoint(e.clientX, e.clientY);
    const nx = pt.x - state.dragOffset.x;
    const ny = pt.y - state.dragOffset.y;
    // optimistic local update
    state.board.layouts[state.dragging] = { x: nx, y: ny };
    render();
  }
});

window.addEventListener('mouseup', async (e) => {
  if (isPanning) {
    isPanning = false;
    svg.style.cursor = state.mode === 'connect' ? 'crosshair' : 'grab';
    return;
  }
  if (state.dragging) {
    const pos = state.board.layouts[state.dragging];
    await commitOp({ op: 'move_node', payload: { node_id: state.dragging, x: pos.x, y: pos.y } });
    state.dragging = null;
  }
});

// Zoom
svg.addEventListener('wheel', (e) => {
  e.preventDefault();
  const delta = e.deltaY > 0 ? 0.9 : 1.1;
  const oldZoom = state.zoom;
  state.zoom = Math.min(Math.max(state.zoom * delta, 0.2), 4);
  // zoom toward mouse
  const rect = svg.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;
  state.pan.x = mx - (mx - state.pan.x) * (state.zoom / oldZoom);
  state.pan.y = my - (my - state.pan.y) * (state.zoom / oldZoom);
  applyTransform();
  updateStatus();
});

// ---------------------------------------------------------------------------
// Toolbar modes
// ---------------------------------------------------------------------------

function setMode(mode) {
  state.mode = mode;
  state.connectFrom = null;
  document.querySelectorAll('#toolbar button').forEach(b => b.classList.remove('active'));
  document.getElementById(`btn-${mode}`).classList.add('active');
  svg.classList.toggle('connecting', mode === 'connect');
}

document.getElementById('btn-select').addEventListener('click', () => setMode('select'));
document.getElementById('btn-connect').addEventListener('click', () => setMode('connect'));
document.getElementById('btn-pan').addEventListener('click', () => setMode('pan'));
document.getElementById('btn-create').addEventListener('click', () => createNodeAtCenter());
document.getElementById('btn-group').addEventListener('click', () => groupSelected());
document.getElementById('btn-layout').addEventListener('click', () => autoLayout('grid'));
document.getElementById('btn-radial').addEventListener('click', () => autoLayout('radial'));
document.getElementById('btn-hierarchy').addEventListener('click', () => autoLayout('hierarchy'));
document.getElementById('btn-force').addEventListener('click', () => autoLayout('force'));
document.getElementById('btn-concentric').addEventListener('click', () => autoLayout('concentric'));
document.getElementById('btn-spiral').addEventListener('click', () => autoLayout('spiral'));
document.getElementById('btn-refresh').addEventListener('click', () => loadBoard());

// ---------------------------------------------------------------------------
// Actions
// ---------------------------------------------------------------------------

async function createNodeAtCenter() {
  const pt = toBoardPoint(window.innerWidth / 2, window.innerHeight / 2);
  const label = prompt('Node label:', 'New idea');
  if (!label) return;
  await commitOp({
    op: 'create_node',
    payload: { label, kind: 'idea', x: pt.x, y: pt.y },
  });
}

async function groupSelected() {
  if (state.selected.size < 2) return;
  const label = prompt('Group label:', 'Group');
  if (label === null) return;
  await commitOp({
    op: 'group_nodes',
    payload: { node_ids: Array.from(state.selected), label },
  });
}

async function autoLayout(mode = 'grid') {
  await api('POST', '/board/layout', { mode });
  await loadBoard();
}

// ---------------------------------------------------------------------------
// Sidebar details
// ---------------------------------------------------------------------------

function showDetails(nid) {
  const node = state.board.nodes[nid];
  if (!node) return;
  sidebar.classList.remove('hidden');
  document.getElementById('detail-id').textContent = nid;
  document.getElementById('detail-label').value = node.label || '';
  document.getElementById('detail-summary').value = node.summary || '';
  document.getElementById('detail-kind').value = node.kind || 'idea';

  const tagList = document.getElementById('detail-tags');
  tagList.innerHTML = '';
  const allTags = collectAllTags();
  allTags.forEach(tag => {
    const span = document.createElement('span');
    span.className = 'tag' + ((node.tags || []).includes(tag) ? ' active' : '');
    span.textContent = tag;
    span.onclick = async () => {
      const has = (node.tags || []).includes(tag);
      await commitOp({ op: has ? 'untag_node' : 'tag_node', payload: { node_id: nid, tag } });
      showDetails(nid);
    };
    tagList.appendChild(span);
  });

  document.getElementById('detail-save').onclick = async () => {
    await commitOp({
      op: 'update_node',
      payload: {
        node_id: nid,
        label: document.getElementById('detail-label').value,
        summary: document.getElementById('detail-summary').value,
        kind: document.getElementById('detail-kind').value,
      },
    });
  };

  document.getElementById('detail-delete').onclick = async () => {
    if (!confirm('Delete this node?')) return;
    await commitOp({ op: 'delete_node', payload: { node_id: nid } });
    hideDetails();
  };
}

function hideDetails() {
  sidebar.classList.add('hidden');
}

function collectAllTags() {
  const set = new Set();
  for (const n of Object.values(state.board.nodes || {})) {
    (n.tags || []).forEach(t => set.add(t));
  }
  return Array.from(set).sort();
}

// ---------------------------------------------------------------------------
// Filters
// ---------------------------------------------------------------------------

const filterInput = document.getElementById('filter-input');
filterInput.addEventListener('input', (e) => {
  state.filterText = e.target.value.toLowerCase();
  applyFilters();
});

function applyFilters() {
  const nodes = nodesGroup.querySelectorAll('g[data-id]');
  nodes.forEach(g => {
    const nid = g.getAttribute('data-id');
    const node = state.board.nodes[nid];
    const matchesText = !state.filterText || (node.label || '').toLowerCase().includes(state.filterText);
    const matchesTags = state.filterTags.size === 0 || (node.tags || []).some(t => state.filterTags.has(t));
    g.style.opacity = (matchesText && matchesTags) ? '1' : '0.15';
  });
}

function renderFilterChips() {
  const bar = document.getElementById('filterbar');
  // remove old chips except input
  bar.querySelectorAll('.chip').forEach(c => c.remove());
  const allTags = collectAllTags();
  allTags.forEach(tag => {
    const chip = document.createElement('span');
    chip.className = 'chip' + (state.filterTags.has(tag) ? ' active' : '');
    chip.textContent = tag;
    chip.onclick = () => {
      if (state.filterTags.has(tag)) state.filterTags.delete(tag);
      else state.filterTags.add(tag);
      renderFilterChips();
      loadBoard();
    };
    bar.appendChild(chip);
  });
}

// ---------------------------------------------------------------------------
// Keyboard shortcuts
// ---------------------------------------------------------------------------

window.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  switch (e.key) {
    case 'v': setMode('select'); break;
    case 'c': setMode('connect'); break;
    case 'h': setMode('pan'); break;
    case 'n': createNodeAtCenter(); break;
    case 'g': groupSelected(); break;
    case '1': autoLayout('grid'); break;
    case '2': autoLayout('radial'); break;
    case '3': autoLayout('hierarchy'); break;
    case '4': autoLayout('force'); break;
    case '5': autoLayout('concentric'); break;
    case '6': autoLayout('spiral'); break;
    case 'r': loadBoard(); break;
    case 'Delete':
    case 'Backspace':
      if (state.selected.size === 1) {
        const nid = Array.from(state.selected)[0];
        commitOp({ op: 'delete_node', payload: { node_id: nid } });
        hideDetails();
      }
      break;
    case 'Escape':
      state.selected.clear();
      state.connectFrom = null;
      hideDetails();
      render();
      break;
  }
});

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

loadBoard().then(() => renderFilterChips());
