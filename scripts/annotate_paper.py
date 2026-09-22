#!/usr/bin/env python3
"""Write verified paper-code mappings as standard PDF highlight annotations.
R2, 2026-09-22. Optional dependency: PyMuPDF. No Zotero access or code execution.
The mapping is provided by a reader who inspected both the PDF and actual code.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile

PALETTE = {
    'YELLOW': '#FFD400', 'BLUE': '#2EA8E5', 'ORANGE': '#F19837',
    'GREEN': '#5FB236', 'PURPLE': '#A28AE5',
}


def nonblank(data: dict, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Missing/non-text field: {key}')
    return value.strip()


def annotate(pdf: Path, repo: Path, mapping_file: Path, output: Path) -> int:
    """Create a new reading copy; never overwrite an input or existing output."""
    try:
        import pymupdf as fitz
    except ImportError as exc:
        raise ValueError('Optional PDF helper requires PyMuPDF; install requirements-pdf.txt first.') from exc
    pdf, repo, mapping_file = pdf.resolve(), repo.resolve(), mapping_file.resolve()
    if output.is_symlink() or output.exists():
        raise ValueError('Output already exists; preserve it rather than overwriting personal annotations.')
    output = output.resolve()
    if output in (pdf, mapping_file) or not pdf.is_file() or not repo.is_dir():
        raise ValueError('Use a real input PDF and repository directory, and a distinct output path.')
    data = json.loads(mapping_file.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Mapping must be a JSON object')
    paper_id = nonblank(data, 'paper_id')
    pdf_version, code_version = nonblank(data, 'pdf_version'), nonblank(data, 'code_version')
    mappings = data.get('mappings')
    if not isinstance(mappings, list) or not mappings:
        raise ValueError('No mappings supplied: an empty template is not an annotated paper.')
    with fitz.open(pdf) as doc:
        if not doc.is_pdf or doc.needs_pass or not len(doc):
            raise ValueError('Expected an accessible, nonempty PDF')
        if not (doc.permissions & fitz.PDF_PERM_ANNOTATE):
            raise ValueError('PDF permissions do not allow annotations')
        original_text = [page.get_text() for page in doc]
        planned = []
        ids = set()
        for item in mappings:
            if not isinstance(item, dict) or item.get('verified') is not True:
                raise ValueError('Inspect each mapping, then explicitly mark verified=true; no guessed mappings.')
            ident = nonblank(item, 'id')
            if not re.fullmatch(re.escape(paper_id) + r':M[0-9]{2,}', ident) or ident in ids:
                raise ValueError(f'Use a unique {paper_id}:M01-style mapping id')
            ids.add(ident)
            color = nonblank(item, 'color').upper()
            if color not in PALETTE:
                raise ValueError(f'Unsupported module color: {color}')
            rel = nonblank(item, 'code_file')
            code_path = (repo / rel).resolve()
            if repo not in code_path.parents or not code_path.is_file():
                raise ValueError(f'Code file must exist inside the selected repository: {rel}')
            lines = item.get('code_lines')
            if not isinstance(lines, list) or len(lines) != 2 or any(type(x) is not int for x in lines):
                raise ValueError('code_lines must be inclusive [start, end], using 1-based integers')
            source = code_path.read_text(encoding='utf-8').splitlines()
            start, end = lines
            if not 1 <= start <= end <= len(source) or end-start+1 > 40:
                raise ValueError('Invalid code range or snippet longer than 40 lines; use a shorter real block')
            symbol = nonblank(item, 'symbol')
            location = nonblank(item, 'paper_location')
            explanation = nonblank(item, 'explanation')
            snippet = '\n'.join(source[start-1:end])
            content = (f'[P2C {ident} | {color}]\n论文：{pdf_version}；{location}\n'
                       f'代码版本：{code_version}\n文件：{rel}:{start}-{end}；符号：{symbol}\n\n'
                       f'对应说明：{explanation}\n\n代码（原样摘录）：\n{snippet}')
            anchors = item.get('anchors')
            if not isinstance(anchors, list) or not anchors:
                raise ValueError('Each mapping needs at least one PDF anchor')
            for anchor in anchors:
                if not isinstance(anchor, dict) or type(anchor.get('page')) is not int or not 1 <= anchor['page'] <= len(doc):
                    raise ValueError('PDF pages are 1-based; supply an existing page')
                page = doc[anchor['page']-1]
                has_text = 'text' in anchor
                if has_text == ('rects' in anchor):
                    raise ValueError('An anchor needs exactly one of text or rects')
                if has_text:
                    text = nonblank(anchor, 'text')
                    quads = page.search_for(text, quads=True)
                    # A repeated phrase and a wrapped match can both yield several quads.
                    # Refuse to guess: use explicit visually verified rectangles instead.
                    if len(quads) != 1:
                        raise ValueError(f'{ident}: text has {len(quads)} quadrilaterals; use precise rects for ambiguous/wrapped text')
                else:
                    rects = anchor['rects']
                    if not isinstance(rects, list) or not rects:
                        raise ValueError('rects must be a nonempty list of [x0,y0,x1,y1]')
                    bounds = page.rect * page.derotation_matrix
                    quads = []
                    for values in rects:
                        if not isinstance(values, list) or len(values) != 4 or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
                            raise ValueError('Invalid rectangle coordinates')
                        rect = fitz.Rect(values)
                        if rect.is_empty or rect.is_infinite or not bounds.contains(rect):
                            raise ValueError('Rectangle is empty or outside the unrotated PDF page')
                        quads.append(rect.quad)
                planned.append((anchor['page']-1, quads, color, content, ident))
        grouped = {}
        for page_no, quads, color, content, ident in planned:
            key = (ident, page_no)
            if key in grouped:
                grouped[key][1].extend(quads)
            else:
                grouped[key] = [page_no, list(quads), color, content, ident]
        count = 0
        for page_no, quads, color, content, ident in grouped.values():
            page = doc[page_no]
            # Retain all previous annotations; require a clean source for these new IDs.
            if any(f'[P2C {ident} |' in a.info.get('content', '') for a in (page.annots() or [])):
                raise ValueError(f'{ident}: source already contains this mapping; do not duplicate existing notes')
            mark = page.add_highlight_annot(quads)
            h = PALETTE[color].lstrip('#')
            mark.set_colors(stroke=tuple(int(h[i:i+2], 16)/255 for i in (0, 2, 4)))
            mark.set_info(title='Paper-code reading notes', subject=ident, content=content)
            mark.set_opacity(0.32)
            mark.update()
            count += 1
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.annotated-', suffix='.pdf', dir=output.parent)
        os.close(fd)
        temp = Path(name)
        try:
            doc.save(temp, garbage=3, deflate=True)
            with fitz.open(temp) as check:
                if [page.get_text() for page in check] != original_text:
                    raise ValueError('PDF text changed unexpectedly; no output written')
            # Exclusive creation guards against concurrent overwrite.
            with output.open('xb') as dst, temp.open('rb') as src:
                while block := src.read(1024*1024):
                    dst.write(block)
        finally:
            temp.unlink(missing_ok=True)
    return count


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--pdf', type=Path, required=True)
    p.add_argument('--repo', type=Path, required=True)
    p.add_argument('--map', dest='mapping', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    try:
        count = annotate(a.pdf, a.repo, a.mapping, a.output)
        print(f'Created {a.output} with {count} embedded highlights. Visually inspect all marked pages before use.')
        return 0
    except Exception as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
