
"""NeuRecall Importers -- GitHub, Mermaid, Alexandria packet expansion."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from neurecall.engine import BoardEngine, _new_id


def import_github_summary(engine, repo_path_or_url):
    ops = []
    repo_id = _slugify(repo_path_or_url)
    repo_node_id = f'gh_repo_{repo_id}'
    info = _gh_repo_info(repo_path_or_url)
    if info is None and Path(repo_path_or_url).exists():
        info = _git_local_info(repo_path_or_url)
    if info is None:
        info = {
            'name': repo_path_or_url.split('/')[-1] or repo_path_or_url,
            'description': '',
            'url': repo_path_or_url if repo_path_or_url.startswith('http') else '',
        }
    ops.append({
        'op': 'create_node',
        'payload': {
            'id': repo_node_id,
            'kind': 'github_ref',
            'label': info['name'],
            'summary': info.get('description', ''),
            'tags': ['github', 'auto'],
            'refs': [{'type': 'github', 'url': info.get('url', '')}],
        },
    })
    for branch in info.get('branches', []):
        bid = f'gh_branch_{repo_id}_{_slugify(branch)}'
        ops.append({
            'op': 'create_node',
            'payload': {
                'id': bid,
                'kind': 'github_ref',
                'label': branch,
                'tags': ['github', 'branch', 'auto'],
            },
        })
        ops.append({
            'op': 'connect',
            'payload': {'from': repo_node_id, 'to': bid, 'relation': 'contains'},
        })
    for commit in info.get('recent_commits', []):
        cid = f'gh_commit_{commit.get("sha", _new_id("c"))[:8]}'
        ops.append({
            'op': 'create_node',
            'payload': {
                'id': cid,
                'kind': 'github_ref',
                'label': commit.get('message', 'Commit')[:40],
                'summary': commit.get('message', ''),
                'tags': ['github', 'commit', 'auto'],
            },
        })
        ops.append({
            'op': 'connect',
            'payload': {'from': repo_node_id, 'to': cid, 'relation': 'contains'},
        })
    res = engine.commit_multi(ops)
    return {'ok': res['ok'], 'repo_node_id': repo_node_id, 'ops_count': len(ops), **res}


def _gh_repo_info(repo):
    try:
        if '/' not in repo:
            return None
        out = subprocess.run(
            ['gh', 'repo', 'view', repo, '--json', 'name,description,url,defaultBranchRef'],
            capture_output=True, text=True, timeout=15
        )
        if out.returncode != 0:
            return None
        data = json.loads(out.stdout)
        branches = []
        if data.get('defaultBranchRef', {}).get('name'):
            branches.append(data['defaultBranchRef']['name'])
        commits_out = subprocess.run(
            ['gh', 'api', f'repos/{repo}/commits?per_page=5'],
            capture_output=True, text=True, timeout=15
        )
        commits = []
        if commits_out.returncode == 0:
            for c in json.loads(commits_out.stdout):
                commits.append({'sha': c.get('sha', ''), 'message': c.get('commit', {}).get('message', '')})
        return {
            'name': data.get('name', repo),
            'description': data.get('description', ''),
            'url': data.get('url', ''),
            'branches': branches,
            'recent_commits': commits,
        }
    except Exception:
        return None


def _git_local_info(path):
    try:
        cwd = Path(path)
        if not (cwd / '.git').exists():
            return None
        name = subprocess.run(['git', '-C', str(cwd), 'rev-parse', '--show-toplevel'],
                            capture_output=True, text=True, timeout=5)
        branch = subprocess.run(['git', '-C', str(cwd), 'branch', '--show-current'],
                                capture_output=True, text=True, timeout=5)
        log_out = subprocess.run(['git', '-C', str(cwd), 'log', '--oneline', '-n', '5'],
                                 capture_output=True, text=True, timeout=5)
        commits = []
        for line in (log_out.stdout or '').strip().splitlines():
            parts = line.split(' ', 1)
            if len(parts) == 2:
                commits.append({'sha': parts[0], 'message': parts[1]})
        return {
            'name': Path(name.stdout.strip()).name if name.returncode == 0 else path,
            'description': 'Local git repo',
            'url': '',
            'branches': [branch.stdout.strip()] if branch.returncode == 0 else [],
            'recent_commits': commits,
        }
    except Exception:
        return None


def _slugify(s):
    return re.sub(r'[^a-zA-Z0-9_-]+', '_', s).strip('_')


MERMAID_KIND_SHAPES = {
    'idea': lambda l: f'([{l}])',
    'task': lambda l: f'[{l}]',
    'memory_ref': lambda l: f'{{{{{l}}}}}',
    'route_ref': lambda l: f'([{l}])',
}


def export_mermaid(engine):
    state = engine.state
    lines = ['flowchart LR']
    node_map = {}
    alias_idx = 0
    for nid, node in state.nodes.items():
        if node.get('deleted'):
            continue
        alias = f'n{alias_idx}'
        alias_idx += 1
        node_map[nid] = alias
        label = node.get('label', nid)
        shape_fn = MERMAID_KIND_SHAPES.get(node.get('kind', 'idea'), MERMAID_KIND_SHAPES['idea'])
        lines.append(f'    {alias}{shape_fn(label)}')
    for eid, edge in state.edges.items():
        fa = node_map.get(edge['from'])
        ta = node_map.get(edge['to'])
        if fa and ta:
            rel = edge.get('relation', '')
            if rel:
                lines.append(f'    {fa} -- {rel} --> {ta}')
            else:
                lines.append(f'    {fa} --> {ta}')
    return '\n'.join(lines)


def import_mermaid(engine, mermaid_text):
    ops = []
    alias_to_id = {}
    node_pattern = re.compile(
        r'^\s*(\w+)\s*([\(\[\{][\{\(\[]?)([^\)\]\}]+)[\)\]\}][\}\)\]]?\s*$'
    )
    edge_pattern = re.compile(
        r'^\s*(\w+)\s*--?>?\s*(?:([^-]+)\s*-->)?\s*(\w+)\s*$'
    )
    for line in mermaid_text.splitlines():
        line = line.strip()
        if not line or line.startswith('flowchart') or line.startswith('graph'):
            continue
        m = node_pattern.match(line)
        if m:
            alias, open_br, label = m.groups()
            kind = 'idea'
            if open_br == '([':
                kind = 'idea'
            elif open_br == '[':
                kind = 'task'
            elif '{{' in open_br:
                kind = 'route_ref'
            elif '{' in open_br:
                kind = 'memory_ref'
            nid = _new_id('node')
            alias_to_id[alias] = nid
            ops.append({
                'op': 'create_node',
                'payload': {'id': nid, 'kind': kind, 'label': label.strip()},
            })
            continue
        m = edge_pattern.match(line)
        if m:
            fa = m.group(1)
            rel = (m.group(2) or '').strip()
            ta = m.group(3)
            from_id = alias_to_id.get(fa)
            to_id = alias_to_id.get(ta)
            if from_id and to_id:
                ops.append({
                    'op': 'connect',
                    'payload': {'from': from_id, 'to': to_id, 'relation': rel or 'relates_to'},
                })
    if ops:
        res = engine.commit_multi(ops)
        return {'ok': res['ok'], 'ops_count': len(ops), **res}
    return {'ok': True, 'ops_count': 0}


def expand_from_packet(engine, packet, proof=None):
    from neurecall.http_service import _packet_to_ops
    ops = _packet_to_ops(packet, proof)
    if not ops:
        return {'ok': True, 'ops_count': 0}
    res = engine.commit_multi(ops)
    return {'ok': res['ok'], 'ops_count': len(ops), **res}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='NeuRecall importers')
    parser.add_argument('--data-root', default=None)
    parser.add_argument('--github', help='owner/repo or local path')
    parser.add_argument('--mermaid-in', help='Path to mermaid file to import')
    parser.add_argument('--mermaid-out', help='Path to write mermaid export')
    args = parser.parse_args()
    if args.data_root:
        os.environ['NEURECALL_DATA_ROOT'] = args.data_root
    eng = BoardEngine()
    if args.github:
        r = import_github_summary(eng, args.github)
        print(json.dumps(r, indent=2))
    if args.mermaid_in:
        text = Path(args.mermaid_in).read_text(encoding='utf-8')
        r = import_mermaid(eng, text)
        print(json.dumps(r, indent=2))
    if args.mermaid_out:
        out = export_mermaid(eng)
        Path(args.mermaid_out).write_text(out, encoding='utf-8')
        print(f'Mermaid exported to {args.mermaid_out}')
