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
import unicodedata
from urllib.parse import unquote, urlsplit

import execution_state

TEMPLATES = Path(__file__).resolve().parent.parent / 'assets' / 'templates'
TIERS = ('ultra', 'max', 'mid', 'inbox')
TYPES = ('method', 'survey', 'benchmark', 'dataset', 'theory', 'tool')
RESERVED = {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}
PROJECT_FILES = {
    'AGENTS.md': 'AGENTS.md', 'research_scope.md': 'research_scope.md',
    'handoff.md': 'handoff.md', 'brainstorm.md': 'brainstorm.md', 'dataset.md': 'dataset.md',
    'related_work/README.md': 'related_work.md', 'related_work/reading_guide.md': 'reading_guide.md',
    'related_work/reading_qa.md': 'reading_qa.md',
    'related_work/reading_standard.md': 'reading_standard.md',
    'related_work/reading_workflow.md': 'reading_workflow.md',
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


def name_slug(value: str) -> str:
    """Derive a portable, readable directory component from a public name."""
    value = unicodedata.normalize('NFKC', value).strip()
    if not value:
        raise ValueError('A real paper title or method name is required for a new entry')
    result = re.sub(r'[\W_]+', '-', value.casefold(), flags=re.UNICODE).strip('-')
    # Leave room for filenames below the directory on common filesystems.
    while len(result.encode('utf-8')) > 180:
        result = result[:-1]
    result = result.rstrip('-')
    if not result or result in RESERVED:
        raise ValueError('The title/name must contain a usable, non-reserved directory name')
    return result


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


def reproduction_entries(root: Path) -> list[tuple[Path, dict]]:
    entries = []
    for meta in sorted(contained(root, root / 'reproduce').glob('*/*/metadata.json')):
        meta = contained(root, meta)
        data = read_json(meta)
        if data.get('entry_kind', 'reproduction') == 'reproduction':
            entries.append((meta.parent, data))
    return entries


def find_entry(root: Path, value: str, kind: str) -> tuple[Path, dict] | None:
    """Internal alias first; public title/name or project path also works.

    Legacy ID fields are read-only fallbacks for explicit migration.
    """
    entries = paper_entries(root) if kind == 'papers' else reproduction_entries(root)
    record = execution_state.resolve_identity(root, kind, value)
    if record:
        path = contained(root, root / record['path'])
        hits = [(p, d) for p, d in entries if p == path]
        if not hits:
            raise ValueError('Internal entry mapping is stale; repair the recorded path before continuing')
        return hits[0]
    field = 'title' if kind == 'papers' else 'name'
    legacy = 'paper_id' if kind == 'papers' else 'repo_id'
    requested = Path(value)
    candidate = contained(root, requested if requested.is_absolute() else root / requested)
    hits = [(p, d) for p, d in entries if p == candidate or p.name == value
            or d.get(field) == value or d.get(legacy) == value]
    if len(hits) > 1:
        raise ValueError('Entry reference is ambiguous; use its project-relative path')
    return hits[0] if hits else None


def find_paper(root: Path, paper_id: str) -> tuple[Path, dict] | None:
    return find_entry(root, paper_id, 'papers')


def find_reproduce(root: Path, repo_id: str, category: str | None = None) -> tuple[Path, dict] | None:
    found = find_entry(root, repo_id, 'repositories')
    if found and category is not None and found[1].get('category', found[0].parent.name) != category:
        raise ValueError('The reproduction entry belongs to another category')
    return found


def find_public_name(entries: list[tuple[Path, dict]], name: str, field: str) -> tuple[Path, dict] | None:
    hits = [(path, metadata) for path, metadata in entries if metadata.get(field) == name]
    if len(hits) > 1:
        raise ValueError('Public name is ambiguous; use the existing project-relative path')
    return hits[0] if hits else None


def link_pending_papers(root: Path, paper_id: str, paper: Path) -> None:
    """Resolve forward references without leaking execution aliases to metadata."""
    rel = paper.relative_to(root).as_posix()
    with execution_state.transaction(root) as state:
        for record in state['identities']['repositories'].values():
            pending = record.get('pending_paper_ids', [])
            if paper_id not in pending:
                continue
            repo = contained(root, root / record['path'])
            repo_meta = read_json(repo / 'metadata.json')
            paper_meta = read_json(paper / 'metadata.json')
            if rel not in repo_meta.setdefault('related_paper_paths', []):
                repo_meta['related_paper_paths'].append(rel)
            if record['path'] not in paper_meta.setdefault('related_code_paths', []):
                paper_meta['related_code_paths'].append(record['path'])
            save_json(repo / 'metadata.json', repo_meta)
            save_json(paper / 'metadata.json', paper_meta)
            record['pending_paper_ids'] = [item for item in pending if item != paper_id]
            if not record['pending_paper_ids']:
                record.pop('pending_paper_ids')


def add_paper(root: Path, paper_id: str, tier: str = 'inbox', paper_type: str = 'method',
              title: str = '', year: int | None = None, arxiv: str = '') -> Path:
    slug(paper_id)
    if tier not in TIERS or paper_type not in TYPES:
        raise ValueError('Invalid tier or paper type')
    existing = find_paper(root, paper_id)
    title = ' '.join(title.split())
    if not existing and title:
        existing = find_public_name(paper_entries(root), title, 'title')
    if existing:
        p, d = existing
        if title and d.get('title') and title != d['title']:
            raise ValueError('Existing entry has another title; do not reuse its execution alias')
        if arxiv and d.get('arxiv_id') and d['arxiv_id'] != arxiv:
            raise ValueError('Existing paper has another arXiv version; preserve it rather than overwriting.')
        execution_state.register_identity(root, 'papers', paper_id, p, d.get('title', p.name))
        link_pending_papers(root, paper_id, p)
        print(f'Existing entry retained: {p}')
        return p
    base_id = re.sub(r'v\d+$', '', arxiv)
    if base_id:
        for _, d in paper_entries(root):
            if re.sub(r'v\d+$', '', d.get('arxiv_id', '')) == base_id:
                raise ValueError(f'This arXiv paper already exists as {d.get("title", "an existing entry")}; do not duplicate versions across tiers.')
    folder = 'survey' if paper_type == 'survey' else tier
    directory = name_slug(title)
    path = contained(root, root / 'related_work' / folder / directory)
    if path.exists():
        raise ValueError(f'Unmanaged entry already exists; inspect it first: {path}')
    path.mkdir(parents=True)
    meta = {
        'entry_kind': 'paper', 'title': title,
        'paper_type': paper_type, 'priority': tier, 'priority_reason': '',
        'relevance': 'unassessed', 'topics': [], 'influence_evidence': [],
        'year': year, 'first_public_year': None, 'venue_year': None, 'venue': '', 'publication_status': 'unverified',
        'arxiv_id': arxiv, 'doi': '', 'source_url': '', 'reviewed_at': '',
        'reading_status': 'unread', 'translation_status': 'not_started',
        'translation_scope': 'required_sections', 'reading_acceptance': 'pending',
        'related_code_paths': [], 'created_at': today(),
    }
    save_json(path / 'metadata.json', meta)
    values = {'title': title}
    for name, source in [('note.md', 'paper_note.md'), ('translation_zh.md', 'translation_zh.md'),
                         ('qa.md', 'paper_qa.md'), ('source/README.md', 'source_readme.md')]:
        write_new(path / name, template(source, values))
    manifest_template = TEMPLATES / 'reading_manifest.json'
    if manifest_template.is_file():
        write_new(path / 'reading_manifest.json', template('reading_manifest.json',
                  {'title': json.dumps(title, ensure_ascii=False)[1:-1]}))
    execution_state.register_identity(root, 'papers', paper_id, path, title)
    link_pending_papers(root, paper_id, path)
    reindex(root)
    print(f'Paper entry ready: {path}')
    return path


def cell(value: object) -> str:
    return str(value if value is not None else '').replace('|', '\\|').replace('\n', ' ')


def reindex(root: Path) -> None:
    entries = paper_entries(root)
    entries.sort(key=lambda item: (TIERS.index(item[1].get('priority', 'inbox'))
                                  if item[1].get('priority') in TIERS else 99,
                                  item[1].get('title', item[0].name).casefold()))
    lines = ['# Literature index', '', '<!-- GENERATED from metadata.json; edit metadata, not this file. -->', '',
             '主题关系和阅读顺序见 reading_guide.md；等级不是客观质量排名。', '',
             '| 论文 | 类型 | 等级 | 年份 | 阅读状态 |', '|---|---|---|---|---|']
    for path, d in entries:
        title = cell(d.get('title') or path.name)
        fields = [cell(d.get(k, '')) for k in ('paper_type', 'priority', 'year', 'reading_status')]
        # Full names and any long metadata remain visible below the compact table.
        fields = [value if len(value) <= 24 else value[:23] + '…' for value in fields]
        budget = max(12, 145 - sum(len(value) for value in fields))
        short_title = title if len(title) <= budget else title[:budget - 1] + '…'
        lines.append('| ' + ' | '.join([short_title, *fields]) + ' |')
    for path, d in entries:
        rel = path.relative_to(root / 'related_work').as_posix()
        lines.extend(['', '## ' + cell(d.get('title') or path.name), '',
                      f'[Note]({rel}/note.md) · [Q&A]({rel}/qa.md) · [Metadata]({rel}/metadata.json)', '',
                      '当前等级：' + cell(d.get('priority', 'inbox')),
                      '类型：' + cell(d.get('paper_type', '')) + '；年份：' + cell(d.get('year', '')),
                      '阅读状态：' + cell(d.get('reading_status', ''))])
    target = contained(root, root / 'related_work/index.md')
    if target.is_symlink():
        raise ValueError('Refusing symlink index')
    target.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def retier(root: Path, paper_id: str, tier: str, reason: str | None = None) -> None:
    found = find_paper(root, paper_id)
    if not found:
        raise ValueError(f'Unknown paper: {paper_id}')
    if tier not in TIERS:
        raise ValueError('Invalid tier')
    path, data = found
    folder = 'survey' if data.get('paper_type') == 'survey' else tier
    target = contained(root, root / 'related_work' / folder / path.name)
    if target != path:
        if target.exists():
            raise ValueError('Destination exists; refusing merge/overwrite')
        old_relative = path.relative_to(root).as_posix()
        new_relative = target.relative_to(root).as_posix()
        def relocate(value):
            if isinstance(value, str):
                for before, after in ((old_relative, new_relative), (str(path), str(target))):
                    if value == before or value.startswith(before + '/'):
                        return after + value[len(before):]
                return value
            if isinstance(value, list):
                return [relocate(item) for item in value]
            if isinstance(value, dict):
                return {relocate(key): (item if key in ('history', 'content', 'report_text', 'text') else relocate(item))
                        for key, item in value.items()}
            return value
        # Acquire the journal writer lock before moving anything. All runtime paths,
        # including stamp keys and registered material paths, move together.
        with execution_state.transaction(root) as state:
            updated = relocate(state)
            state.clear()
            state.update(updated)
            target.parent.mkdir(parents=True, exist_ok=True)
            path.rename(target)
            for entry, metadata in paper_entries(root) + reproduction_entries(root):
                changed = False
                for key in ('related_paper_paths', 'related_code_paths', 'project_relative_repo_path'):
                    if key in metadata:
                        replacement = relocate(metadata[key])
                        if replacement != metadata[key]:
                            metadata[key] = replacement
                            changed = True
                if changed:
                    save_json(entry / 'metadata.json', metadata)
    # Preserve old provenance without requiring or appending grading explanations.
    # The optional reason argument remains accepted for older callers.
    data.update(priority=tier, reviewed_at=today())
    save_json(target / 'metadata.json', data)
    reindex(root)
    print('Priority updated; internal state and metadata associations follow the new path. Check manually written tier-specific links.')


def add_reproduce(root: Path, repo_id: str, category: str, repo_url: str = '',
                  paper_ids: list[str] | None = None, name: str = '') -> Path:
    slug(repo_id); slug(category)
    ids = paper_ids or []
    base = contained(root, root / 'reproduce')
    name = ' '.join(name.split())
    if not name and repo_url:
        # Repository names are public provenance; temporary aliases are never a fallback.
        name = unquote(urlsplit(repo_url).path.rstrip('/').rsplit('/', 1)[-1])
        if name.endswith('.git'):
            name = name[:-4]
    existing = find_reproduce(root, repo_id, category)
    if not existing and name:
        existing = find_public_name(reproduction_entries(root), name, 'name')
        if existing and existing[1].get('category', existing[0].parent.name) != category:
            raise ValueError('The named reproduction entry belongs to another category')
    if existing:
        path, data = existing
        if name and data.get('name') and name != data['name']:
            raise ValueError('Existing entry has another name; do not reuse its execution alias')
        if repo_url and data.get('repo_url') and repo_url != data['repo_url']:
            raise ValueError('Existing entry has another repository URL; do not merge different checkouts')
        execution_state.register_identity(root, 'repositories', repo_id, path, data.get('name', path.name))
        print(f'Existing reproduction entry retained: {path}')
        return path
    path = contained(root, base / category / name_slug(name))
    if path.exists():
        raise ValueError(f'Unmanaged entry already exists: {path}')
    associated, pending = [], []
    for ident in ids:
        found = find_paper(root, ident)
        if found:
            if found[0] not in associated:
                associated.append(found[0])
        else:
            slug(ident)
            if ident not in pending:
                pending.append(ident)
    path.mkdir(parents=True)
    (path / 'repo').mkdir()
    save_json(path / 'metadata.json', {
        'entry_kind': 'reproduction', 'name': name, 'category': category,
        'repo_url': repo_url, 'commit': '', 'annotation_branch': '',
        'related_paper_paths': [p.relative_to(root).as_posix() for p in associated],
        'status': 'not_started', 'repo_path': 'repo', 'repo_path_base': 'entry_directory',
        'created_at': today(),
    })
    for filename, source in [('note.md', 'code_note.md'), ('code_map.md', 'code_map.md'),
                         ('reproduction_report.md', 'reproduction_report.md')]:
        write_new(path / filename, template(source, {'name': name}))
    execution_state.register_identity(root, 'repositories', repo_id, path, name)
    if pending:
        with execution_state.transaction(root) as state:
            state['identities']['repositories'][repo_id]['pending_paper_ids'] = pending
        print('Some paper associations are pending; they are held only in the internal execution journal.')
    # A known association is recorded on both sides with public project paths.
    for p in associated:
        data = read_json(p / 'metadata.json')
        relative = path.relative_to(root).as_posix()
        if relative not in data.setdefault('related_code_paths', []):
            data['related_code_paths'].append(relative)
            save_json(p / 'metadata.json', data)
    print(f'Reproduction entry ready (no clone/run performed): {path}')
    return path


def resolve_repo_entry(entry: Path) -> tuple[Path, dict]:
    """Resolve a local checkout below its reproduction entry, never outside it.

    Legacy entries without repo_path use repo/. Explicit paths are relative to
    entry_directory; project-relative metadata is descriptive, not a fallback.
    """
    entry = entry.resolve()
    meta = read_json(contained(entry, entry / 'metadata.json'))
    rel = meta.get('repo_path', 'repo')
    if meta.get('repo_path_base', 'entry_directory') != 'entry_directory':
        raise ValueError('repo_path_base must be entry_directory; convert legacy metadata explicitly')
    if not isinstance(rel, str) or not rel or Path(rel).is_absolute():
        raise ValueError('repo_path must be a nonempty path relative to the reproduction entry')
    repo = contained(entry, entry / rel)
    if repo == entry or not repo.is_dir():
        raise ValueError('repo_path must select an existing checkout below the reproduction entry')
    return repo, meta


def annotation_branch(root: Path, category: str, repo_id: str) -> str:
    """Explicit local action: create/switch a reading branch, never reset, commit or push."""
    slug(category)
    found = find_reproduce(root, repo_id, category)
    if not found:
        raise ValueError(f'Unknown reproduction entry: {repo_id}')
    entry, _ = found
    meta_path = contained(root, entry / 'metadata.json')
    repo, meta = resolve_repo_entry(entry)
    def git(*args: str) -> str:
        result = subprocess.run(['git', '-C', str(repo), *args], text=True, capture_output=True)
        if result.returncode:
            raise ValueError(result.stderr.strip() or 'Git command failed')
        return result.stdout.strip()
    if not repo.is_dir() or Path(git('rev-parse', '--show-toplevel')).resolve() != repo.resolve():
        raise ValueError('repo_path must be the root of the actual model Git repository, not the parent project.')
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
    slug(category)
    found = find_reproduce(root, repo_id, category)
    if not found:
        raise ValueError(f'Unknown reproduction entry: {repo_id}')
    entry, _ = found
    repo, _ = resolve_repo_entry(entry)
    skipped = {'.git', '.venv', '__pycache__', 'node_modules', 'checkpoints', 'weights', 'data', 'datasets', 'logs', 'build', 'dist'}
    output = ['# Code tree', '', '自动生成的结构清单，不是代码语义分析。文件职责与调用链请另填 code_map.md。', '', '```text', repo.relative_to(entry).as_posix() + '/']
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
    p = subs.add_parser('retier'); p.add_argument('project'); p.add_argument('paper_id'); p.add_argument('tier', choices=TIERS); p.add_argument('--reason', help='Legacy argument; no grading-reason history is recorded')
    p = subs.add_parser('add-reproduce'); p.add_argument('project'); p.add_argument('repo_id'); p.add_argument('--category', default='baselines'); p.add_argument('--repo-url', default=''); p.add_argument('--paper-id', action='append', default=[]); p.add_argument('--name', default='')
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
            add_reproduce(root, a.repo_id, a.category, a.repo_url, a.paper_id, a.name)
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
