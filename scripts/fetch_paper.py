#!/usr/bin/env python3
"""Select-latest-then-pin arXiv downloader (no file fingerprints). Standard library only, Python 3.10+.
Modified replacement for upstream fetch-paper.sh, 2026-09-22.
Raw sources remain unchanged; upstream-style cleaning applies only to a reading copy.
No model calls, translation, or command execution from papers.
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tarfile
import tempfile
import time
import xml.etree.ElementTree as ET
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

import workflow as wf

MAX_DOWNLOAD = 128 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024
MAX_MEMBERS = 10000
LAST_REQUEST = 0.0
ARXIV_METADATA: dict[str, dict[str, str]] = {}


def pinned_id(value: str) -> str:
    if not re.fullmatch(r'(?:\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7})v[1-9]\d*', value):
        raise ValueError('Use an explicit arXiv version, e.g. 2301.11305v1. Bare ids and URLs are not accepted.')
    return value


def base_id(value: str) -> str:
    value = re.sub(r'v[1-9]\d*$', '', value)
    if not re.fullmatch(r'(?:\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7})', value):
        raise ValueError('Use an arXiv id, optionally with vN; URLs are not accepted.')
    return value


def official_metadata(value: str) -> dict[str, str]:
    """Read the official title and selected version before creating an entry."""
    global LAST_REQUEST
    requested_base = base_id(value)
    explicit = bool(re.search(r'v[1-9]\d*$', value))
    if explicit:
        pinned_id(value)
    time.sleep(max(0.0, 3.0 - (time.monotonic() - LAST_REQUEST)))
    LAST_REQUEST = time.monotonic()
    url = 'https://export.arxiv.org/api/query?id_list=' + quote(value, safe='/')
    req = Request(url, headers={'User-Agent': 'research-workflow-local/2.1 (academic research)'})
    with urlopen(req, timeout=60) as response:
        payload = response.read(2 * 1024 * 1024 + 1)
    if len(payload) > 2 * 1024 * 1024:
        raise ValueError('Unexpectedly large arXiv metadata response')
    try:
        atom = ET.fromstring(payload)
        entry = atom.find('{http://www.w3.org/2005/Atom}entry')
        entry_id = entry.findtext('{http://www.w3.org/2005/Atom}id', '') if entry is not None else ''
        candidate = entry_id.split('/abs/', 1)[1] if '/abs/' in entry_id else ''
        pinned_id(candidate)
    except (ET.ParseError, ValueError) as exc:
        raise ValueError('Could not confirm latest arXiv version; supply a verified vN explicitly.') from exc
    if base_id(candidate) != requested_base or (explicit and candidate != value):
        raise ValueError('arXiv metadata returned a different paper')
    title = ' '.join(entry.findtext('{http://www.w3.org/2005/Atom}title', '').split())
    if not title:
        raise ValueError('arXiv metadata did not provide a paper title; supply a verified --title explicitly.')
    record = {'arxiv_id': candidate, 'title': title}
    ARXIV_METADATA[candidate] = record
    return record.copy()


def resolve_latest(value: str) -> str:
    """Resolve once via official Atom metadata; never guess a version on failure."""
    return official_metadata(base_id(value))['arxiv_id']


def select_id(root: Path, value: str, paper_id: str) -> str:
    base_id(value)
    if re.search(r'v[1-9]\d*$', value):
        return pinned_id(value)
    found = wf.find_paper(root, paper_id)
    if found and found[1].get('arxiv_id'):
        saved = pinned_id(found[1]['arxiv_id'])
        if base_id(saved) != base_id(value):
            raise ValueError('Selected paper id belongs to a different arXiv record')
        print(f'Keeping selected version {saved}; no automatic update.')
        return saved
    selected = resolve_latest(value)
    print(f'Confirmed latest available version for this import: {selected}')
    return selected


def valid_pdf(path: Path) -> bool:
    with path.open('rb') as f:
        return b'%PDF-' in f.read(1024)


def download(url: str, target: Path, pdf: bool = False) -> dict:
    """Download to a fresh temporary file, then validate and atomically move."""
    global LAST_REQUEST
    if target.exists() or target.is_symlink():
        raise ValueError(f'Refusing to overwrite: {target}')
    target.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        fd, temporary = tempfile.mkstemp(prefix='.download-', suffix='.part', dir=target.parent)
        import os
        os.close(fd)
        temp = Path(temporary)
        try:
            time.sleep(max(0.0, 3.0 - (time.monotonic() - LAST_REQUEST)))
            LAST_REQUEST = time.monotonic()
            req = Request(url, headers={'User-Agent': 'research-workflow-local/2.0 (academic research)'})
            with urlopen(req, timeout=60) as response, temp.open('wb') as output:
                ctype = response.headers.get('Content-Type', '')
                total = 0
                for block in iter(lambda: response.read(1024 * 1024), b''):
                    total += len(block)
                    if total > MAX_DOWNLOAD:
                        raise ValueError('Download exceeds the configured size limit')
                    output.write(block)
            if temp.stat().st_size == 0:
                raise ValueError('Empty response')
            head = temp.read_bytes()[:512] if temp.stat().st_size < 512 else b''
            if not head:
                with temp.open('rb') as f:
                    head = f.read(512)
            if 'text/html' in ctype.lower() or head.lstrip().lower().startswith((b'<!doctype html', b'<html')):
                raise ValueError('Received HTML instead of a paper artifact')
            if pdf and not valid_pdf(temp):
                raise ValueError('Response does not contain a PDF header')
            temp.replace(target)
            return {'url': url, 'downloaded_at': wf.today(),
                    'bytes': target.stat().st_size, 'content_type': ctype}
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            if isinstance(exc, HTTPError) and exc.code not in (408, 429, 500, 502, 503, 504):
                raise
            if attempt == 2:
                raise
            time.sleep(3.0 * (attempt + 1))
        finally:
            temp.unlink(missing_ok=True)
    raise RuntimeError('Unreachable download state')


def member_path(root: Path, name: str) -> Path:
    """Reject absolute paths, traversal, Windows paths and ambiguous separators."""
    p = PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or '\\' in name or ':' in name:
        raise ValueError(f'Unsafe archive path: {name!r}')
    for part in p.parts:
        if part not in ('.', '') and (part.endswith((' ', '.')) or part.split('.')[0].lower() in wf.RESERVED):
            raise ValueError(f'Nonportable archive path: {name!r}')
    return wf.contained(root, root.joinpath(*p.parts))


def extract_source(archive: Path, target: Path) -> str:
    """Extract regular files/directories only; never execute or edit source content."""
    if target.exists() or target.is_symlink():
        raise ValueError('Source target already exists; refusing overwrite')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.extract-', dir=target.parent) as tmp:
        root = Path(tmp)
        try:
            tf = tarfile.open(archive, 'r:*')
        except tarfile.ReadError:
            tf = None
        if tf is not None:
            with tf:
                total = 0
                count = 0
                destinations = set()
                for member in tf:
                    count += 1
                    total += max(member.size, 0)
                    if count > MAX_MEMBERS or total > MAX_EXPANDED:
                        raise ValueError('Archive exceeds extraction limits')
                    if not (member.isdir() or member.isfile()) or getattr(member, 'sparse', None):
                        raise ValueError('Archive contains links, special files or sparse entries')
                    dest = member_path(root, member.name)
                    # Case-fold to avoid Windows collisions even while checking on Linux.
                    normalized = dest.relative_to(root).as_posix().casefold()
                    if member.isfile() and normalized in destinations:
                        raise ValueError('Archive has duplicate or case-colliding file paths')
                    destinations.add(normalized)
                    if member.isdir():
                        dest.mkdir(parents=True, exist_ok=True)
                    else:
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        src = tf.extractfile(member)
                        if src is None:
                            raise ValueError('Cannot read a regular archive member')
                        copied = 0
                        with src, dest.open('xb') as output:
                            for block in iter(lambda: src.read(1024 * 1024), b''):
                                copied += len(block)
                                if copied > member.size or copied > MAX_EXPANDED:
                                    raise ValueError('Extracted member exceeds declared limits')
                                output.write(block)
            kind = 'tar_archive'
        else:
            with archive.open('rb') as f:
                magic = f.read(2)
            opener = gzip.open if magic == b'\x1f\x8b' else open
            with opener(archive, 'rb') as f:
                content = f.read(MAX_EXPANDED + 1)
            if len(content) > MAX_EXPANDED:
                raise ValueError('Single-file source exceeds extraction limits')
            # Some legacy submissions are not UTF-8; preserve bytes, do not decode/rewrite.
            if b'\x00' in content or b'%PDF-' in content[:1024] or not any(token in content for token in (b'\\documentclass', b'\\begin{document}', b'\\documentstyle')):
                raise ValueError('Not a recognized TeX source; original archive remains preserved')
            (root / 'main.tex').write_bytes(content)
            kind = 'single_tex'
        # Source is small enough to pass the bounds; move only after complete validation.
        root.rename(target)
    return kind


def verify_existing(path: Path, info: dict | None, pdf: bool = False, expected_url: str = '') -> None:
    if not info or (expected_url and info.get('url') != expected_url):
        raise ValueError(f'Existing file has no matching version/source record; confirm its origin: {path}')
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f'Existing artifact is empty or not a regular file: {path}')
    if pdf and not valid_pdf(path):
        raise ValueError('Existing PDF failed header validation')


def prepare_reading_copy(source: Path, raw: Path) -> None:
    """Keep the upstream source/*.tex, *.orig and .stale layout, without touching raw/."""
    # A generated README and downloaded artifacts already live in source/. Authors' copies
    # of reserved top-level names remain accessible in raw/ rather than being overwritten.
    reserved = {'raw', 'source.archive', 'README.md', '.stale'}
    with tempfile.TemporaryDirectory(prefix='research-reading-') as tmp:
        staged = Path(tmp) / 'reading'
        staged.mkdir()
        for child in raw.iterdir():
            if child.name in reserved:
                continue
            target = staged / child.name
            if child.is_dir():
                shutil.copytree(child, target)
            else:
                shutil.copy2(child, target)
        old_names = {'history', 'old', 'backup', 'draft', 'previous', 'archive', 'submitted'}
        for path in sorted(staged.rglob('*'), key=lambda p: len(p.parts)):
            rel = path.relative_to(staged)
            if '.stale' in rel.parts or not path.is_dir() or path.name.lower() not in old_names:
                continue
            target = staged / '.stale' / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            path.rename(target)
        for tex in staged.rglob('*.tex'):
            if '.stale' in tex.relative_to(staged).parts:
                continue
            original = tex.read_bytes()
            tex.with_name(tex.name + '.orig').write_bytes(original)
            # Same comment-stripping convention as upstream; .orig and raw/ remain the
            # source of truth when macros/verbatim make a cleaned excerpt ambiguous.
            tex.write_bytes(re.sub(rb'(?<!\\)%[^\r\n]*', b'', original))
        entries = list(staged.iterdir())
        if any((source / p.name).exists() or (source / p.name).is_symlink() for p in entries):
            raise ValueError('Existing source reading files are retained; inspect before updating.')
        for path in entries:
            shutil.move(str(path), str(source / path.name))


def fetch(root: Path, ident: str, paper_id: str, tier: str, paper_type: str = 'method', title: str = '') -> Path:
    pinned_id(ident)
    existing = wf.find_paper(root, paper_id)
    if not existing and not title.strip():
        title = (ARXIV_METADATA.get(ident) or official_metadata(ident))['title']
    entry = wf.add_paper(root, paper_id, tier=tier, paper_type=paper_type, title=title, arxiv=ident)
    meta_path = wf.contained(root, entry / 'metadata.json')
    meta = wf.read_json(meta_path)
    if meta.get('arxiv_id') not in ('', ident):
        raise ValueError('Version mismatch; never mix a new source version with an existing PDF')
    meta['arxiv_id'] = ident
    meta['source_url'] = 'https://arxiv.org/abs/' + ident
    wf.save_json(meta_path, meta)
    artifacts = meta.setdefault('artifacts', {})
    encoded = quote(ident, safe='/')
    pdf = wf.contained(root, entry / 'paper.pdf')
    if pdf.exists():
        verify_existing(pdf, artifacts.get('pdf'), pdf=True, expected_url='https://arxiv.org/pdf/' + encoded)
    else:
        artifacts['pdf'] = download('https://arxiv.org/pdf/' + encoded, pdf, pdf=True)
        wf.save_json(meta_path, meta)
    archive = wf.contained(root, entry / 'source/source.archive')
    raw = wf.contained(root, entry / 'source/raw')
    try:
        if archive.exists():
            verify_existing(archive, artifacts.get('source'), expected_url='https://arxiv.org/e-print/' + encoded)
        else:
            artifacts['source'] = download('https://arxiv.org/e-print/' + encoded, archive)
            wf.save_json(meta_path, meta)
        if raw.exists():
            if meta.get('source_status') != 'extracted':
                raise ValueError('Unmanaged source/raw directory exists; inspect it manually')
        else:
            meta['source_format'] = extract_source(archive, raw)
        if meta.get('source_reading_status') != 'prepared':
            prepare_reading_copy(archive.parent, raw)
            meta['source_reading_status'] = 'prepared'
        meta['source_status'] = 'extracted'
        meta.pop('source_error', None)
    except (HTTPError, URLError, TimeoutError, OSError, ValueError, tarfile.TarError, EOFError) as exc:
        # PDF-only or unavailable source is a valid partial outcome, not a completed source download.
        meta['source_status'] = 'unavailable_or_unverified'
        meta['source_error'] = str(exc)
        print(f'Warning: PDF is available, source is not verified: {exc}', file=sys.stderr)
    wf.save_json(meta_path, meta)
    wf.reindex(root)
    print(f'PDF ready (version/source/header checked): {pdf}\nSource status: {meta.get("source_status")}')
    return entry


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('arxiv_id'); p.add_argument('paper_id', nargs='?')
    p.add_argument('--project', default='.'); p.add_argument('--tier', choices=wf.TIERS, default='inbox')
    p.add_argument('--type', dest='paper_type', choices=wf.TYPES, default='method')
    p.add_argument('--title', default='', help='Verified paper title; otherwise read official arXiv metadata')
    a = p.parse_args()
    try:
        root = wf.project_path(a.project)
        paper_id = a.paper_id or re.sub(r'[^a-z0-9_-]', '-', base_id(a.arxiv_id).lower())
        wf.slug(paper_id)
        ident = select_id(root, a.arxiv_id, paper_id)
        fetch(root, ident, paper_id, a.tier, a.paper_type, a.title)
        return 0
    except (ValueError, OSError, KeyError, json.JSONDecodeError, HTTPError, URLError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
