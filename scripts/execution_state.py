#!/usr/bin/env python3
"""Shared local journal: the only persistent home for private execution IDs.

Public paper/repository artifacts are referenced by project-relative paths.
No network, hashing, Markdown editing or automatic agent execution is performed.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path

SCHEMA_VERSION = 2


def contained(root: Path, path: Path) -> Path:
    root, path = root.resolve(), path.resolve()
    if path != root and root not in path.parents:
        raise ValueError(f'Path leaves the project: {path}')
    return path


def state_path(project: Path) -> Path:
    project = Path(project).expanduser().resolve()
    raw = project / '.workflow/execution_state.json'
    if raw.is_symlink():
        raise ValueError('Refusing symlink execution journal')
    return contained(project, raw)


def empty_state() -> dict:
    return {'schema_version': SCHEMA_VERSION, 'active_run': None, 'runs': {},
            'papers': {}, 'identities': {'papers': {}, 'repositories': {}}}


def load(project: Path) -> dict:
    path = state_path(project)
    if not path.exists():
        return empty_state()
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or data.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('Unsupported execution journal schema; migrate legacy runs explicitly')
    for key in ('runs', 'papers', 'identities'):
        if not isinstance(data.get(key), dict):
            raise ValueError(f'Execution journal requires an object: {key}')
    for key in ('papers', 'repositories'):
        if not isinstance(data['identities'].get(key), dict):
            raise ValueError(f'Execution identity registry requires an object: {key}')
    return data


@contextmanager
def transaction(project: Path):
    project = Path(project).expanduser().resolve()
    if not project.is_dir():
        raise ValueError('Project directory must already exist')
    target = state_path(project)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = target.parent / 'execution_state.lock'
    temporary = target.parent / 'execution_state.json.part'
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError('Another writer holds execution_state.lock; only the main agent may serialize journal updates') from exc
    try:
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(str(os.getpid()))
        data = load(project)
        yield data
        if temporary.is_symlink() or target.is_symlink():
            raise ValueError('Refusing symlink execution journal files')
        with temporary.open('w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        os.replace(temporary, target)
    finally:
        if temporary.is_file() and not temporary.is_symlink():
            temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def relative_path(project: Path, path: Path | str) -> str:
    root = Path(project).expanduser().resolve()
    value = Path(path)
    return contained(root, value if value.is_absolute() else root / value).relative_to(root).as_posix()


def get_paper(project: Path, paper_dir: Path | str) -> dict:
    return copy.deepcopy(load(project)['papers'].get(relative_path(project, paper_dir), {}))


def update_paper(project: Path, paper_dir: Path | str, updates: dict) -> dict:
    key = relative_path(project, paper_dir)
    with transaction(project) as data:
        item = data['papers'].setdefault(key, {})
        item.update(copy.deepcopy(updates))
        return copy.deepcopy(item)


def register_identity(project: Path, kind: str, ident: str, path: Path | str, title: str = '') -> dict:
    if kind not in ('papers', 'repositories') or not isinstance(ident, str) or not ident:
        raise ValueError('Use papers/repositories and a nonempty internal identity')
    record = {'path': relative_path(project, path), 'title': title}
    with transaction(project) as data:
        previous = data['identities'][kind].get(ident)
        if previous and previous['path'] != record['path']:
            raise ValueError('Internal identity already refers to another path; relocate it explicitly')
        record = {**(previous or {}), **record}
        data['identities'][kind][ident] = record
    return copy.deepcopy(record)


def resolve_identity(project: Path, kind: str, ident: str) -> dict | None:
    if kind not in ('papers', 'repositories'):
        raise ValueError('Identity kind must be papers or repositories')
    return copy.deepcopy(load(project)['identities'][kind].get(ident))
