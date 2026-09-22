#!/usr/bin/env python3
"""Local research-workflow helpers; Python 3.10+, standard library only.
Modified/new implementation based on skJack/research-workflow, 2026-09-22.
No model calls, repository cloning, dependency installation, or training.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

TEMPLATES = Path(__file__).resolve().parent.parent / 'assets' / 'templates'
TIERS = ('ultra', 'max', 'mid', 'inbox')
TYPES = ('method', 'survey', 'benchmark', 'dataset', 'theory', 'tool')
RESERVED = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}
PROJECT_FILES = {
    'AGENTS.md': 'AGENTS.md', 'research_scope.md': 'research_scope.md',
    'handoff.md': 'handoff.md', 'brainstorm.md': 'brainstorm.md', 'dataset.md': 'dataset.md',
    'related_work/README.md': 'related_work.md', 'related_work/reading_guide.md': 'reading_guide.md',
    'related_work/reading_qa.md': 'reading_qa.md',
    'related_work/code_annotation_guide.md': 'code_annotation_guide.md', 'reproduce/README.md': 'reproduce.md',
    'experiment/evaluation.md': 'evaluation.md', 'experiment/results.md': 'results.md',
    'method/README.md': 'method.md', 'writing/README.md': 'writing.md',
}
IGNORE_BLOCK = '''# BEGIN research-workflow managed ignores
# Raw artifacts remain local; keep metadata and human notes in version control.
/datasets/
/related_work/**/paper.pdf
/related_work/**/paper_annotated.pdf
/related_work/**/source/
/related_work/**/translation_zh.md
/reproduce/**/repo/
/experiment/**/checkpoints/
*.pt
*.pth
*.ckpt
*.safetensors
*.orig
*.part
.env
.env.*
!.env.example
__pycache__/
.venv/
.DS_Store
# END research-workflow managed ignores
'''


def today() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def slug(value: str) -> str:
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,100}', value) or value.lower() in RESERVED:
        raise ValueError(f'Use a lowercase English/digit slug with _ or - (not a reserved name): {value!r}')
    return value


def contained(root: Path, path: Path) -> Path:
    """Refuse symlink/path traversal outside the user-selected project."""
    root = root.resolve()
    path = path.resolve()
    if path != root and root not in path.parents:
        raise ValueError(f'Path leaves the project: {path}')
    return path


def write_new(path: Path, text: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8', newline='\n') as f:
            f.write(text)
        return True
    except FileExistsError:
        if not path.is_file():
            raise ValueError(f'Expected a file, found a directory: {path}')
        return False


def template(name: str, values: dict[str, str] | None = None) -> str:
    text = (TEMPLATES / name).read_text(encoding='utf-8')
    for key, val in (values or {}).items():
        text = text.replace('{{' + key + '}}', val)
    return text


def read_json(path: Path) -> dict:
    obj = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(obj, dict):
        raise ValueError(f'Expected a JSON object: {path}')
    return obj


def save_json(path: Path, data: dict) -> None:
    # Only machine metadata is replaced; user Markdown files are never overwritten.
    text = json.dumps(data, ensure_ascii=False, indent=2) + '\n'
    tmp = path.with_name(path.name + '.part')
    if tmp.is_symlink() or path.is_symlink():
        raise ValueError('Refusing symlink metadata files')
    tmp.write_text(text, encoding='utf-8')
    os.replace(tmp, path)


def project_path(path: str | Path) -> Path:
    root = Path(path).expanduser().resolve()
    if not (root / 'research_scope.md').is_file():
        raise ValueError('Initialize the project first with: workflow.py init <project>')
    return root


def init_project(path: str | Path, profile: str = 'generic') -> Path:
    root = Path(path).expanduser().resolve()
    legacy = [name for name in ('复现', 'DataSet', '方法') if (root / name).exists()]
    if legacy:
        raise ValueError('Legacy directories found: ' + ', '.join(legacy) + '. See docs/migration.md before initializing.')
    root.mkdir(parents=True, exist_ok=True)
    dirs = ['related_work/' + t for t in (*TIERS, 'survey')]
    dirs += ['reproduce', 'method', 'experiment', 'datasets', 'writing']
    cats = ('policy_models', 'agent_frameworks', 'evaluation') if profile == 'embodied' else ('baselines',)
    dirs += ['reproduce/' + c for c in cats]
    for rel in dirs:
        contained(root, root / rel).mkdir(parents=True, exist_ok=True)
    for rel, source in PROJECT_FILES.items():
        write_new(contained(root, root / rel), template(source, {'project_name': root.name, '项目名': root.name}))
    write_new(contained(root, root / 'related_work/references.bib'), '% Add verified bibliography entries; do not invent metadata.\n')
    ignore = contained(root, root / '.gitignore')
    previous = ignore.read_text(encoding='utf-8') if ignore.exists() else ''
    if '# BEGIN research-workflow managed ignores' not in previous:
        with ignore.open('a', encoding='utf-8', newline='\n') as f:
            f.write(('\n' if previous and not previous.endswith('\n') else '') + IGNORE_BLOCK)
    if '# BEGIN research-workflow managed ignores' in previous and '/related_work/**/paper_annotated.pdf' not in previous:
        with ignore.open('a', encoding='utf-8', newline='\n') as f:
            f.write('\n# Annotated reading copy (R2)\n/related_work/**/paper_annotated.pdf\n')
    index = contained(root, root / 'related_work/index.md')
    if not index.exists():
        reindex(root)
    print(f'Project ready: {root}')
    return root


def paper_entries(root: Path) -> list[tuple[Path, dict]]:
    base = contained(root, root / 'related_work')
    entries = []
    for tier in (*TIERS, 'survey'):
        directory = contained(root, base / tier)
        if not directory.exists():
            continue
        for child in sorted(directory.iterdir()):
            child = contained(root, child)
            if child.is_dir() and (child / 'metadata.json').is_file():
                data = read_json(contained(root, child / 'metadata.json'))
                if data.get('entry_kind') == 'paper':
                    entries.append((child, data))
    return entries


def find_paper(root: Path, paper_id: str) -> tuple[Path, dict] | None:
    hits = [(p, d) for p, d in paper_entries(root) if d.get('paper_id') == paper_id]
    if len(hits) > 1:
        raise ValueError(f'Duplicate paper_id: {paper_id}')
    return hits[0] if hits else None


def add_paper(root: Path, paper_id: str, tier: str = 'inbox', paper_type: str = 'method',
              title: str = '', year: int | None = None, arxiv: str = '') -> Path:
    slug(paper_id)
    if tier not in TIERS or paper_type not in TYPES:
        raise ValueError('Invalid tier or paper type')
    existing = find_paper(root, paper_id)
    if existing:
        p, d = existing
        if arxiv and d.get('arxiv_id') and d['arxiv_id'] != arxiv:
            raise ValueError('Existing paper has another arXiv version; preserve it rather than overwriting.')
        print(f'Existing entry retained: {p}')
        return p
    base_id = re.sub(r'v\d+$', '', arxiv)
    if base_id:
        for _, d in paper_entries(root):
            if re.sub(r'v\d+$', '', d.get('arxiv_id', '')) == base_id:
                raise ValueError(f'This arXiv paper already exists as {d["paper_id"]}; do not duplicate versions across tiers.')
    folder = 'survey' if paper_type == 'survey' else tier
    path = contained(root, root / 'related_work' / folder / paper_id)
    if path.exists():
        raise ValueError(f'Unmanaged entry already exists; inspect it first: {path}')
    path.mkdir(parents=True)
    meta = {
        'entry_kind': 'paper', 'paper_id': paper_id, 'title': title or paper_id,
        'paper_type': paper_type, 'priority': tier, 'priority_reason': '',
        'relevance': 'unassessed', 'topics': [], 'influence_evidence': [],
        'year': year, 'first_public_year': None, 'venue_year': None, 'venue': '', 'publication_status': 'unverified',
        'arxiv_id': arxiv, 'doi': '', 'source_url': '', 'reviewed_at': '',
        'reading_status': 'unread', 'translation_status': 'not_started',
        'translation_scope': 'full', 'related_repo_ids': [], 'created_at': today(),
    }
    save_json(path / 'metadata.json', meta)
    values = {'paper_id': paper_id, 'title': meta['title']}
    for name, source in [('note.md', 'paper_note.md'), ('translation_zh.md', 'translation_zh.md'),
                         ('qa.md', 'paper_qa.md'), ('source/README.md', 'source_readme.md')]:
        write_new(path / name, template(source, values))
    reindex(root)
    print(f'Paper entry ready: {path}')
    return path


def cell(value: object) -> str:
    return str(value if value is not None else '').replace('|', '\\|').replace('\n', ' ')


def reindex(root: Path) -> None:
    entries = paper_entries(root)
    seen = set()
    for _, d in entries:
        ident = slug(d['paper_id'])
        if ident in seen:
            raise ValueError(f'Duplicate paper_id: {ident}')
        seen.add(ident)
    entries.sort(key=lambda item: (TIERS.index(item[1].get('priority', 'inbox'))
                                  if item[1].get('priority') in TIERS else 99, item[1]['paper_id']))
    lines = ['# Literature index', '', '<!-- GENERATED from metadata.json; edit metadata, not this file. -->', '',
             '主题关系和阅读顺序见 reading_guide.md；等级不是客观质量排名。', '',
             '| paper_id | title | type | priority | year | reading_status |', '|---|---|---|---|---|---|']
    for path, d in entries:
        lines.append('| ' + ' | '.join(cell(d.get(k, '')) for k in
                     ('paper_id', 'title', 'paper_type', 'priority', 'year', 'reading_status')) + ' |')
    for path, d in entries:
        rel = path.relative_to(root / 'related_work').as_posix()
        lines.extend(['', '## ' + d['paper_id'], '',
                      f'[Note]({rel}/note.md) · [Q&A]({rel}/qa.md) · [Metadata]({rel}/metadata.json)', '',
                      '分级理由：' + (cell(d.get('priority_reason')) or '待人工审核。')])
    target = contained(root, root / 'related_work/index.md')
    if target.is_symlink():
        raise ValueError('Refusing symlink index')
    target.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def retier(root: Path, paper_id: str, tier: str, reason: str) -> None:
    found = find_paper(root, slug(paper_id))
    if not found:
        raise ValueError(f'Unknown paper: {paper_id}')
    if tier not in TIERS:
        raise ValueError('Invalid tier')
    path, data = found
    folder = 'survey' if data.get('paper_type') == 'survey' else tier
    target = contained(root, root / 'related_work' / folder / paper_id)
    if target != path:
        if target.exists():
            raise ValueError('Destination exists; refusing merge/overwrite')
        target.parent.mkdir(parents=True, exist_ok=True)
        path.rename(target)
    history = data.setdefault('priority_history', [])
    history.append({'from': data.get('priority'), 'to': tier, 'reason': reason, 'at': today()})
    data.update(priority=tier, priority_reason=reason, reviewed_at=today())
    save_json(target / 'metadata.json', data)
    reindex(root)
    print('Priority updated; check any manually written tier-specific links. Stable index anchors are unchanged.')


def add_reproduce(root: Path, repo_id: str, category: str, repo_url: str = '', paper_ids: list[str] | None = None) -> Path:
    slug(repo_id); slug(category)
    ids = paper_ids or []
    for ident in ids:
        slug(ident)
    base = contained(root, root / 'reproduce')
    for other in base.glob('*/' + repo_id + '/metadata.json'):
        other = contained(root, other)
        if read_json(other).get('repo_id') == repo_id:
            print(f'Existing reproduction entry retained: {other.parent}')
            return other.parent
    path = contained(root, base / category / repo_id)
    if path.exists():
        raise ValueError(f'Unmanaged entry already exists: {path}')
    path.mkdir(parents=True)
    (path / 'repo').mkdir()
    save_json(path / 'metadata.json', {
        'entry_kind': 'reproduction', 'repo_id': repo_id, 'category': category,
        'repo_url': repo_url, 'commit': '', 'annotation_branch': '', 'paper_ids': ids,
        'status': 'not_started', 'created_at': today(),
    })
    for name, source in [('note.md', 'code_note.md'), ('code_map.md', 'code_map.md'),
                         ('reproduction_report.md', 'reproduction_report.md')]:
        write_new(path / name, template(source, {'repo_id': repo_id}))
    # A known association is recorded on both sides; unknown ids remain a visible TODO.
    for ident in ids:
        found = find_paper(root, ident)
        if found:
            p, data = found
            if repo_id not in data.setdefault('related_repo_ids', []):
                data['related_repo_ids'].append(repo_id)
                save_json(p / 'metadata.json', data)
        else:
            print(f'Warning: paper_id {ident!r} is not yet in the literature collection.')
    print(f'Reproduction entry ready (no clone/run performed): {path}')
    return path


def annotation_branch(root: Path, category: str, repo_id: str) -> str:
    """Explicit local action: create/switch a reading branch, never reset, commit or push."""
    slug(category); slug(repo_id)
    entry = contained(root, root / 'reproduce' / category / repo_id)
    repo = contained(root, entry / 'repo')
    meta_path = contained(root, entry / 'metadata.json')
    meta = read_json(meta_path)
    def git(*args: str) -> str:
        result = subprocess.run(['git', '-C', str(repo), *args], text=True, capture_output=True)
        if result.returncode:
            raise ValueError(result.stderr.strip() or 'Git command failed')
        return result.stdout.strip()
    if not repo.is_dir() or Path(git('rev-parse', '--show-toplevel')).resolve() != repo.resolve():
        raise ValueError('repo/ must be the root of the actual model Git repository, not the parent project.')
    name = 'reading/annotated'
    existing = subprocess.run(['git', '-C', str(repo), 'show-ref', '--verify', '--quiet', 'refs/heads/' + name])
    if existing.returncode == 0:
        raise ValueError('reading/annotated already exists; preserve it and inspect/switch manually. No reset performed.')
    if existing.returncode != 1:
        raise ValueError('Could not determine whether the annotation branch already exists')
    if git('status', '--porcelain'):
        raise ValueError('Repository has local changes; preserve them before creating the annotation branch.')
    base = git('rev-parse', 'HEAD')
    git('switch', '-c', name)
    meta.update(commit=base, annotation_branch=name)
    save_json(meta_path, meta)
    print(f'Annotation branch ready: {name}; base {base[:12]}. No comments, commit or push created.')
    return name


def code_tree(root: Path, category: str, repo_id: str, max_depth: int = 4, max_entries: int = 300) -> None:
    slug(category); slug(repo_id)
    entry = contained(root, root / 'reproduce' / category / repo_id)
    repo = contained(root, entry / 'repo')
    if not repo.is_dir():
        raise ValueError('No repository directory found')
    skipped = {'.git', '.venv', '__pycache__', 'node_modules', 'checkpoints', 'weights', 'data', 'datasets', 'logs', 'build', 'dist'}
    output = ['# Code tree', '', '自动生成的结构清单，不是代码语义分析。文件职责与调用链请另填 code_map.md。', '', '```text', 'repo/']
    count = 0
    def walk(folder: Path, depth: int) -> None:
        nonlocal count
        if depth >= max_depth:
            return
        for p in sorted(folder.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if p.name in skipped:
                continue
            if count >= max_entries:
                return
            count += 1
            output.append('  ' * (depth + 1) + p.name + (' [symlink; not followed]' if p.is_symlink() else '/' if p.is_dir() else ''))
            if p.is_dir() and not p.is_symlink():
                walk(p, depth + 1)
    walk(repo, 0)
    output += ['```', '', f'限制：最大深度 {max_depth}；最多 {max_entries} 项；未遍历符号链接。',
               '排除目录：' + ', '.join(sorted(skipped)) + '。',
               '达到深度/数量上限时，清单不完整；这不是“全库已读”的证明。']
    target = contained(root, entry / 'code_tree.md')
    target.write_text('\n'.join(output) + '\n', encoding='utf-8')
    print(f'Tree generated: {target}')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    p = subs.add_parser('init'); p.add_argument('project'); p.add_argument('--profile', choices=('generic', 'embodied'), default='generic')
    p = subs.add_parser('add-paper'); p.add_argument('project'); p.add_argument('paper_id'); p.add_argument('--tier', choices=TIERS, default='inbox')
    p.add_argument('--type', dest='paper_type', choices=TYPES, default='method'); p.add_argument('--title', default=''); p.add_argument('--year', type=int); p.add_argument('--arxiv', default='')
    p = subs.add_parser('reindex'); p.add_argument('project')
    p = subs.add_parser('retier'); p.add_argument('project'); p.add_argument('paper_id'); p.add_argument('tier', choices=TIERS); p.add_argument('--reason', required=True)
    p = subs.add_parser('add-reproduce'); p.add_argument('project'); p.add_argument('repo_id'); p.add_argument('--category', default='baselines'); p.add_argument('--repo-url', default=''); p.add_argument('--paper-id', action='append', default=[])
    p = subs.add_parser('code-tree'); p.add_argument('project'); p.add_argument('category'); p.add_argument('repo_id'); p.add_argument('--max-depth', type=int, default=4); p.add_argument('--max-entries', type=int, default=300)
    p = subs.add_parser('annotation-branch'); p.add_argument('project'); p.add_argument('category'); p.add_argument('repo_id')
    a = parser.parse_args()
    try:
        if a.command == 'init':
            init_project(a.project, a.profile)
            return 0
        root = project_path(a.project)
        if a.command == 'add-paper':
            add_paper(root, a.paper_id, a.tier, a.paper_type, a.title, a.year, a.arxiv)
        elif a.command == 'reindex':
            reindex(root)
        elif a.command == 'retier':
            retier(root, a.paper_id, a.tier, a.reason)
        elif a.command == 'add-reproduce':
            add_reproduce(root, a.repo_id, a.category, a.repo_url, a.paper_id)
        elif a.command == 'annotation-branch':
            annotation_branch(root, a.category, a.repo_id)
        elif a.command == 'code-tree':
            if a.max_depth < 1 or a.max_entries < 1:
                raise ValueError('Tree limits must be positive')
            code_tree(root, a.category, a.repo_id, a.max_depth, a.max_entries)
        return 0
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
