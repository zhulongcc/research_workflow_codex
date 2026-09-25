#!/usr/bin/env python3
"""Validate reading artifacts and render a local, offline HTML reading copy.

A passing structural check does not prove translation accuracy or paper/code
semantics. Review provenance and unresolved gaps are reported separately.
No paper text, Markdown notes, Git state, or manifest is changed by this helper.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote, unquote, urlsplit

from workflow import contained, read_json, resolve_repo_entry
from paper_references import citation, mapping_key

CATEGORIES = {
    'input_preprocessing': 'YELLOW', 'core_model': 'BLUE', 'training_loss': 'ORANGE',
    'inference_optimization': 'GREEN', 'evaluation': 'PURPLE',
}
COLORS = {'YELLOW': (255, 212, 0), 'BLUE': (46, 168, 229), 'ORANGE': (241, 152, 55),
          'GREEN': (95, 178, 54), 'PURPLE': (162, 138, 229)}
DOCS = ('note.md', 'translation_zh.md', 'qa.md')
NOTE_SECTIONS = ('材料', 'Highlights', '一句话总结', '背景与动机', '方法详解', '实验与消融', '局限与启发')
PLACEHOLDER = re.compile(r'\{\{[^}]*\}\}|\b(?:TODO|TBD|FIXME|REPLACE_WITH_[A-Z_]+)\b|待填写|待补充|待翻译|尚未开始翻译', re.I)
NOTICE = '程序只核验结构、文件与证据一致性；不证明翻译准确、映射语义正确或实验已复现。'


def markdown():
    try:
        from markdown_it import MarkdownIt
    except ImportError as exc:
        raise ValueError('Missing markdown-it-py; install requirements-reading.txt to check/render Markdown.') from exc
    return MarkdownIt('commonmark', {'html': True, 'breaks': False}).enable(['table', 'strikethrough'])


def visible(tokens) -> str:
    """Unicode code points of displayed text; links count labels, never URLs."""
    result, previous_preserved = [], False
    for token in tokens or []:
        value, preserved = '', token.type == 'code_inline'
        if token.type == 'text':
            value = re.sub(r'[\t\n\r\f ]+', ' ', token.content)
        elif token.type == 'code_inline':
            value = token.content
        elif token.type in ('softbreak', 'hardbreak'):
            value = ' '
        elif token.type == 'image':
            value = visible(token.children) if token.children else token.content
        elif token.type == 'html_inline':
            # A line break is one visible separator, not a new logical table row.
            value = ' ' if re.fullmatch(r'<br\s*/?>', token.content, re.I) else ''
        elif token.children:
            value = visible(token.children)
        if value:
            if not preserved and not previous_preserved and result and result[-1].endswith(' ') and value.startswith(' '):
                value = value[1:]
            result.append(value)
            previous_preserved = preserved
    return ''.join(result)


def table_rows(tokens) -> list[dict]:
    rows, current = [], None
    for token in tokens:
        if token.type == 'tr_open':
            current = {'line': (token.map or [0])[0] + 1, 'text': ''}
        elif token.type == 'inline' and current is not None:
            current['text'] += visible(token.children)
        elif token.type == 'tr_close' and current is not None:
            current['characters'] = len(current['text'])
            rows.append(current)
            current = None
    return rows


def sections(tokens) -> list[dict]:
    result = []
    for i, token in enumerate(tokens):
        if token.type != 'heading_open':
            continue
        level = int(token.tag[1:])
        title = visible(tokens[i + 1].children)
        end = len(tokens)
        for j in range(i + 3, len(tokens)):
            if tokens[j].type == 'heading_open' and int(tokens[j].tag[1:]) <= level:
                end = j
                break
        body = '\n'.join(visible(t.children) if t.type == 'inline' else t.content
                         for t in tokens[i + 3:end] if t.type in ('inline', 'fence', 'code_block'))
        result.append({'title': title, 'body': body, 'level': level,
                       'tokens': tokens[i + 3:end], 'line': (token.map or [0])[0] + 1})
    return result


def issue(report, code, message, path=None, line=None, kind='error'):
    item = {'code': code, 'message': message}
    if path is not None:
        item['path'] = str(path)
    if line is not None:
        item['line'] = line
    report['gaps' if kind == 'gap' else 'errors'].append(item)


def safe_file(base: Path, value, label: str) -> Path:
    if not isinstance(value, str) or not value or Path(value).is_absolute():
        raise ValueError(f'{label} must be a nonempty relative path')
    path = contained(base, base / value)
    if not path.is_file():
        raise ValueError(f'{label} file does not exist: {path}')
    return path


def inspect_document(path: Path, parser, report) -> tuple[str, list]:
    text = path.read_text(encoding='utf-8')
    tokens = parser.parse(text)
    if len(''.join(visible(t.children) for t in tokens if t.type == 'inline').strip()) < 40:
        issue(report, 'empty_document', 'Document has insufficient substantive content.', path)
    for match in PLACEHOLDER.finditer(text):
        issue(report, 'placeholder', f'Residual placeholder: {match.group(0)}', path, text[:match.start()].count('\n') + 1)
    # Raw HTML tables and unsupported inline HTML can hide visible content from
    # Markdown table parsing. Require portable Markdown, except safe anchors/br.
    for token in tokens:
        candidates = [token] + list(token.children or [])
        for child in candidates:
            if child.type not in ('html_inline', 'html_block'):
                continue
            raw = child.content.strip()
            if not (re.fullmatch(r'<!--.*?-->', raw, re.S)
                    or re.fullmatch(r'<br\s*/?>', raw, re.I)
                    or re.fullmatch(r'<a\s+id=[\"\'][a-zA-Z0-9_-]+[\"\']\s*>(?:\s*</a>)?|</a>', raw)):
                issue(report, 'unsupported_html', 'Use Markdown formatting; raw HTML is limited to comments, br, and named anchors.', path, (token.map or [0])[0] + 1)
    rows = table_rows(tokens)
    for row in rows:
        if row['characters'] > 160:
            issue(report, 'table_row_length', f"Visible row has {row['characters']} characters (maximum 160); move explanations outside the table.", path, row['line'])
    report['documents'][path.name] = {'characters': len(text), 'table_rows': len(rows),
        'max_table_row_characters': max((r['characters'] for r in rows), default=0)}
    return text, tokens


def prose_without_links(tokens):
    """Navigation labels and question tables do not establish a written answer."""
    output, table_depth, heading_depth = [], 0, 0
    for token in tokens:
        if token.type == 'table_open':
            table_depth += 1
        elif token.type == 'table_close':
            table_depth -= 1
        elif token.type == 'heading_open':
            heading_depth += 1
        elif token.type == 'heading_close':
            heading_depth -= 1
        elif not table_depth and not heading_depth and token.type == 'inline':
            children, link_depth = [], 0
            for child in token.children or []:
                if child.type == 'link_open':
                    link_depth += 1
                elif child.type == 'link_close':
                    link_depth -= 1
                elif not link_depth and child.type != 'image':
                    children.append(child)
            output.append(visible(children))
    return '\n'.join(output).strip()


def check_sections(docs, report):
    note = sections(docs.get('note.md', ('', []))[1])
    ordered = []
    for name in NOTE_SECTIONS:
        matches = [s for s in note if s['level'] == 2 and s['title'].casefold() == name.casefold()]
        minimum = 1 if name == '材料' else 8 if name == '一句话总结' else 20
        content = (matches[0]['body'] if name == '材料' else prose_without_links(matches[0]['tokens'])) if matches else ''
        if len(matches) != 1 or len(content.strip()) < minimum:
            issue(report, 'note_section', f'note.md needs one substantive level-2 {name} section.')
        if matches:
            ordered.append(matches[0]['line'])
    if ordered != sorted(ordered):
        issue(report, 'note_section_order', 'note.md sections must follow the detailed-reading order: ' + ' → '.join(NOTE_SECTIONS))
    if any(re.match(r'^Q[1-6]\b', s['title'], re.I) for s in note):
        issue(report, 'question_location', 'Move the complete six-question answers and evidence from note.md into qa.md.')
    qa = sections(docs.get('qa.md', ('', []))[1])
    source_label = r'(?:依据|证据(?:来源)?|原文位置|原文定位|来源|evidence|sources?|references?)'
    evidence_marker = re.compile(rf'(?im)^\s*{source_label}(?:\s*[:：]\s*|\s*\n)')
    inline_evidence = re.compile(rf'[\[［【]\s*{source_label}\s*[:：]\s*([^\]］】\n]+)[\]］】]', re.I)
    for number in range(1, 7):
        matches = [s for s in qa if re.match(rf'^Q{number}\b', s['title'], re.I)]
        question = matches[0] if len(matches) == 1 else None
        if not question or len(re.sub(rf'^Q{number}\s*[-—:：]?\s*', '', question['title'], flags=re.I)) < 4:
            issue(report, 'six_question', f'qa.md needs one Q{number} heading containing the complete question.')
        if not question:
            issue(report, 'six_answer', f'qa.md needs a substantive Q{number} answer; a question table or link is insufficient.')
            continue
        question_tokens, table_depth = [], 0
        for index, token in enumerate(question['tokens']):
            # A nested next-question heading is still the end of this answer;
            # otherwise an empty Q1 could borrow all of Q2's text and evidence.
            if (token.type == 'heading_open' and index + 1 < len(question['tokens'])
                    and re.match(r'^Q[1-6]\b', visible(question['tokens'][index + 1].children), re.I)):
                break
            if token.type == 'table_open':
                table_depth += 1
            elif token.type == 'table_close':
                table_depth -= 1
            elif not table_depth:
                question_tokens.append(token)
        # Table cells such as a column headed “证据” are not source paragraphs.
        source_body = '\n'.join(visible(token.children) for token in question_tokens if token.type == 'inline')
        marker = evidence_marker.search(source_body)
        # Retain source-link labels as evidence, but never count navigation as an answer.
        sources = [match.group(1) for match in inline_evidence.finditer(source_body)]
        if marker:
            sources.append(source_body[marker.end():])
        for token in question_tokens:
            linked, label = False, []
            for child in token.children or []:
                if child.type == 'link_open':
                    linked, label = True, []
                elif child.type == 'link_close':
                    value = visible(label)
                    match = evidence_marker.match(value)
                    if match:
                        sources.append(value[match.end():])
                    linked = False
                elif linked:
                    label.append(child)
        if not any(len(source.strip()) >= 4 for source in sources):
            issue(report, 'six_evidence', f'qa.md Q{number} needs a source reference or evidence statement.')
        answer_tokens = []
        for token in question_tokens:
            if token.type == 'inline' and evidence_marker.match(visible(token.children) + '\n'):
                break
            answer_tokens.append(token)
        answer = prose_without_links(answer_tokens)
        prose_marker = evidence_marker.search(answer)
        if prose_marker:
            answer = answer[:prose_marker.start()]
        answer = inline_evidence.sub('', answer)
        answer = re.sub(r'(?im)^\s*(?:答案|回答|answer)\s*[:：]?\s*', '', answer).strip()
        if len(answer) < 20:
            issue(report, 'six_answer', f'qa.md Q{number} needs a substantive answer after excluding citations; references, links and tables alone do not count.')
    for filename, (_, tokens) in docs.items():
        for token in tokens:
            for child in token.children or []:
                if child.type != 'link_open':
                    continue
                target = urlsplit(child.attrGet('href') or '')
                if (not target.scheme and not target.netloc
                        and (unquote(target.path) in ('note.md', './note.md') or filename == 'note.md' and not target.path)
                        and re.fullmatch(r'q[1-6]|精读六问', unquote(target.fragment), re.I)):
                    issue(report, 'old_question_link', f'{filename} still links to six-question answers in note.md; link to qa.md instead.')
    trans = sections(docs.get('translation_zh.md', ('', []))[1])
    for name, pattern in [('摘要', r'摘要|abstract'), ('Highlights', r'highlights'), ('方法', r'方法|method'),
                          ('不足', r'不足|局限|limitation'), ('未来展望', r'未来|展望|future')]:
        matches = [s for s in trans if re.search(pattern, s['title'], re.I)]
        if not any(len(s['body'].strip()) >= 20 for s in matches):
            issue(report, 'translation_section', f'translation_zh.md needs a substantive {name} section.')
    limits = [s for s in trans if re.search(r'不足|局限|limitation', s['title'], re.I)]
    future = [s for s in trans if re.search(r'未来|展望|future', s['title'], re.I)]
    if limits and future and limits[0] is future[0]:
        issue(report, 'translation_sections_combined', '不足 and 未来展望 must have separate sections.')


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, text=True)
    if proc.returncode:
        raise ValueError(proc.stderr.strip() or 'Git inspection failed')
    return proc.stdout.rstrip('\n')


def comment_only_changes(baseline: list[str], current: list[str], suffix: str = '') -> bool:
    """Allow explanatory comment/blank-line edits, without marker conventions."""
    def comment(line):
        text = line.strip()
        if text.startswith('#!') or re.search(r'coding\s*[:=]', text):
            return False
        return not text or text.startswith(('#', '//')) or (text.startswith('/*') and text.endswith('*/'))
    for op, a, b, c, d in difflib.SequenceMatcher(a=baseline, b=current, autojunk=False).get_opcodes():
        if op != 'equal' and not all(comment(line) for line in baseline[a:b] + current[c:d]):
            return False
    if suffix == '.py':
        try:
            return ast.dump(ast.parse('\n'.join(baseline))) == ast.dump(ast.parse('\n'.join(current)))
        except SyntaxError:
            return False
    return True


def inspect_pdf(source: Path, annotated: Path, mappings: list, source_snippets: dict, report, paper_title: str):
    try:
        import pymupdf as fitz
    except ImportError:
        issue(report, 'pdf_dependency', 'PDF evidence not verified: install optional requirements-pdf.txt (PyMuPDF).', kind='gap')
        report['evidence']['pdf'] = 'unverified_missing_dependency'
        return
    try:
        with fitz.open(source) as original, fitz.open(annotated) as marked:
            if not original.is_pdf or not marked.is_pdf or not len(original):
                raise ValueError('Expected accessible, nonempty PDFs')
            if len(original) != len(marked) or [p.get_text() for p in original] != [p.get_text() for p in marked]:
                issue(report, 'pdf_body_changed', 'Annotated PDF must preserve the source PDF page count and text.')
            annotations = []
            for page in marked:
                for ann in page.annots() or []:
                    vertices = ann.vertices or []
                    boxes = [fitz.Quad(vertices[i:i + 4]).rect for i in range(0, len(vertices) - 3, 4)]
                    annotations.append((page.number + 1, ann.info.get('content', ''), ann.colors.get('stroke'), ann.type[0], boxes))
            for mapping in mappings:
                ident = mapping['paper_location']
                expected = citation(paper_title, mapping)
                found = [(page, body, color, typ, boxes) for page, body, color, typ, boxes in annotations if expected in body]
                if not found:
                    issue(report, 'pdf_mapping_missing', f'{ident}: no matching PDF annotation.')
                snippet = source_snippets.get(mapping_key(mapping), '')
                for page, body, color, typ, boxes in found:
                    if snippet and snippet not in body:
                        issue(report, 'pdf_snippet_mismatch', f'{ident}: PDF annotation does not contain the current verbatim code snippet.')
                    if mapping.get('code_file') not in body or mapping.get('paper_location') not in body:
                        issue(report, 'pdf_annotation_evidence', f'{ident}: PDF annotation lacks source path or paper location.')
                    rgb = COLORS.get(mapping.get('color'), ())
                    if typ != fitz.PDF_ANNOT_HIGHLIGHT or not color or len(color) != 3 or any(abs(x - y / 255) > .02 for x, y in zip(color, rgb)):
                        issue(report, 'pdf_annotation_color', f'{ident}: PDF must use its declared highlight color.')
                for anchor in mapping.get('anchors', []):
                    page_no = anchor.get('page')
                    if type(page_no) is not int or not 1 <= page_no <= len(original):
                        issue(report, 'pdf_page', f'{ident}: invalid anchor page.')
                        continue
                    if not any(page == page_no for page, *_ in found):
                        issue(report, 'pdf_anchor_missing', f'{ident}: no annotation on declared page {page_no}.')
                    page = original[page_no - 1]
                    expected_boxes = []
                    if 'text' in anchor:
                        if not isinstance(anchor['text'], str) or not anchor['text'].strip() or not page.search_for(anchor['text']):
                            issue(report, 'pdf_anchor_text', f'{ident}: anchor text is not found on declared page {page_no}.')
                        else:
                            expected_boxes = page.search_for(anchor['text'])
                    elif 'rects' in anchor:
                        rects = anchor['rects']
                        if not isinstance(rects, list) or not rects:
                            issue(report, 'pdf_anchor_rects', f'{ident}: empty anchor rectangles.')
                        else:
                            for rect in rects:
                                try:
                                    box = fitz.Rect(rect)
                                    if box.is_empty or box.is_infinite or not page.rect.contains(box):
                                        raise ValueError('outside page')
                                    expected_boxes.append(box)
                                except Exception:
                                    issue(report, 'pdf_anchor_rects', f'{ident}: invalid anchor rectangle.')
                    else:
                        issue(report, 'pdf_anchor', f'{ident}: anchor must supply text or rects.')
                    actual_boxes = [box for found_page, _, _, _, boxes in found if found_page == page_no for box in boxes]
                    if expected_boxes and not all(any((box & actual).get_area() >= .8 * box.get_area() for actual in actual_boxes) for box in expected_boxes):
                        issue(report, 'pdf_anchor_position', f'{ident}: embedded highlights do not cover the declared paper anchors.')
            report['evidence']['pdf'] = 'inspected'
    except Exception as exc:
        issue(report, 'pdf_read', str(exc))


def check_code(paper: Path, repo_entry: Path | None, manifest: dict, source: Path | None, parser, report):
    code = manifest.get('code')
    if not isinstance(code, dict):
        issue(report, 'code_manifest', 'Manifest needs a code object.')
        return
    if code.get('status') not in ('complete', 'differences_verified'):
        issue(report, 'code_incomplete', f"Paper/code review status: {code.get('status', 'missing')}", kind='gap')
    if code.get('status') == 'differences_verified' and not report['verified_differences']:
        issue(report, 'difference_evidence', 'differences_verified requires grounded verified_differences entries.')
    if repo_entry is None:
        issue(report, 'repo_entry_missing', 'Open-source work requires --repo-entry pointing to the reproduction entry.', kind='gap')
        return
    try:
        repo, metadata = resolve_repo_entry(repo_entry)
        if Path(git(repo, 'rev-parse', '--show-toplevel')).resolve() != repo:
            raise ValueError('repo_path is not an actual Git checkout root')
        head = git(repo, 'rev-parse', 'HEAD')
        map_path = safe_file(paper, code.get('mapping_file'), 'mapping_file')
        mapping_data = read_json(map_path)
        if mapping_data.get('paper_title') != manifest.get('paper_title'):
            issue(report, 'mapping_paper_title', 'Mapping and manifest paper titles differ.')
        if 'paper_id' in mapping_data:
            issue(report, 'temporary_identifier', 'Move temporary paper identifiers into execution_state.json.')
        version = mapping_data.get('code_version', '')
        if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', version) or head != version:
            issue(report, 'code_version', 'Mapping code_version must be the full actual checkout HEAD commit.')
        if metadata.get('commit') != version:
            issue(report, 'metadata_code_version', 'Reproduction metadata.commit and mapping code_version differ.')
        if mapping_data.get('pdf_version') != manifest.get('source', {}).get('version'):
            issue(report, 'mapping_pdf_version', 'Mapping pdf_version and manifest source.version differ.')
        mappings = mapping_data.get('mappings')
        if not isinstance(mappings, list) or not mappings:
            raise ValueError('Mapping JSON needs a nonempty mappings list')
        code_map = safe_file(repo_entry, 'code_map.md', 'code_map')
        code_map_text, _ = inspect_document(code_map, parser, report)
    except (ValueError, OSError, TypeError) as exc:
        issue(report, 'code_inputs', str(exc))
        return
    locations, snippets, valid_mappings, inspected_files = {}, {}, [], set()
    for item in mappings:
        if not isinstance(item, dict):
            issue(report, 'mapping_schema', 'Each mapping must be an object.')
            continue
        ident = item.get('paper_location', '')
        if 'id' in item:
            issue(report, 'temporary_identifier', 'Use descriptive paper/code positions, not mapping IDs.')
            continue
        try:
            key = mapping_key(item)
            if key in snippets:
                raise ValueError('Duplicate paper/code position')
            if item.get('verified') is not True:
                issue(report, 'mapping_unreviewed', f'{ident}: mapping has not been reviewed.', kind='gap')
            for field in ('paper_location', 'symbol', 'explanation'):
                if not isinstance(item.get(field), str) or len(item[field].strip()) < 2 or PLACEHOLDER.search(item[field]):
                    raise ValueError(f'{ident}: invalid {field}')
            if item.get('color') not in CATEGORIES.values():
                raise ValueError(f'{ident}: unknown module color')
            path = safe_file(repo, item.get('code_file'), 'code_file')
            lines = path.read_text(encoding='utf-8').splitlines()
            if path not in inspected_files:
                baseline = git(repo, 'show', version + ':' + path.relative_to(repo).as_posix()).splitlines()
                if not comment_only_changes(baseline, lines, path.suffix):
                    issue(report, 'code_behavior_changed', f'{item["code_file"]}: non-comment changes differ from the selected Git version.')
                inspected_files.add(path)
            span = item.get('code_lines')
            if not isinstance(span, list) or len(span) != 2 or any(type(n) is not int for n in span):
                raise ValueError(f'{ident}: code_lines must be [start, end] integers')
            start, end = span
            if not 1 <= start <= end <= len(lines) or end - start >= 40:
                raise ValueError(f'{ident}: nonexistent code lines or snippet exceeds 40 lines')
            snippet = '\n'.join(lines[start - 1:end])
            if not snippet.strip() or all(not s.strip() or s.lstrip().startswith(('#', '//', '/*', '*')) for s in lines[start - 1:end]):
                raise ValueError(f'{ident}: selected snippet must contain real code, not just comments')
            nearby = '\n'.join(line for line in lines[max(0, start - 16):start - 1]
                               if line.lstrip().startswith(('#', '//', '/*')))
            if manifest['paper_title'] not in nearby or ident not in nearby:
                issue(report, 'code_reference', f'{ident}: a nearby explanatory comment must cite the paper title and location.')
            if re.search(r'\bP2C\b|:M\d{2,}\b', '\n'.join(lines)):
                issue(report, 'temporary_identifier', f'{item["code_file"]}: remove temporary annotation markers.')
            if ident not in code_map_text or item['code_file'] not in code_map_text:
                issue(report, 'code_map_location', f'{ident}: code_map.md must contain the paper location and source path.')
            if not isinstance(item.get('anchors'), list) or not item['anchors'] or not all(isinstance(a, dict) for a in item['anchors']):
                raise ValueError(f'{ident}: supply nonempty PDF anchors')
            snippets[key] = snippet
            locations.setdefault(ident, []).append(item)
            valid_mappings.append(item)
        except (ValueError, OSError, TypeError) as exc:
            issue(report, 'mapping_evidence', str(exc))
    if re.search(r'\bP2C\b|:M\d{2,}\b', code_map_text):
        issue(report, 'temporary_identifier', 'code_map.md still contains temporary annotation markers.')
    coverage = code.get('coverage', {})
    for category, color in CATEGORIES.items():
        item = coverage.get(category, {}) if isinstance(coverage, dict) else {}
        if not isinstance(item, dict):
            item = {}
        state = item.get('status')
        reason = item.get('reason', '')
        selected = item.get('locations', [])
        if state in ('covered', 'difference'):
            if not isinstance(selected, list) or not selected or any(not any(m.get('color') == color for m in locations.get(loc, [])) for loc in selected):
                issue(report, 'coverage_locations', f'{category}: covered requires actual paper locations of color {color}.')
            if state == 'difference' and (code.get('status') != 'differences_verified' or not isinstance(reason, str) or len(reason.strip()) < 12):
                issue(report, 'coverage_difference', f'{category}: difference requires verified code status and a concrete explanation.')
        elif state == 'not_applicable':
            if not isinstance(reason, str) or len(reason.strip()) < 12:
                issue(report, 'coverage_reason', f'{category}: not_applicable needs an explicit paper-grounded explanation.')
        else:
            issue(report, 'coverage_gap', f'{category}: {reason or "missing or incomplete coverage"}', kind='gap')
    try:
        annotated = safe_file(paper, code.get('annotated_pdf'), 'annotated_pdf')
        if source is not None:
            inspect_pdf(source, annotated, valid_mappings, snippets, report, manifest['paper_title'])
    except (ValueError, OSError) as exc:
        issue(report, 'annotated_pdf_missing', str(exc), kind='gap')
    report['evidence']['code'] = {'repo': str(repo), 'head': head, 'mapping_count': len(valid_mappings)}


def check(paper: Path, repo_entry: Path | None = None) -> dict:
    paper = paper.resolve()
    report = {'schema_version': 1, 'paper_directory': str(paper), 'status': 'incomplete',
              'reading_status': 'partial', 'correspondence_status': 'unresolved', 'workflow_ready': False,
              'structural_checks': 'failed', 'notice': NOTICE, 'documents': {}, 'evidence': {},
              'errors': [], 'gaps': [], 'verified_differences': []}
    try:
        parser = markdown()
    except ValueError as exc:
        issue(report, 'dependency', str(exc), kind='gap')
        return report
    docs = {}
    for filename in DOCS:
        try:
            path = safe_file(paper, filename, filename)
            docs[filename] = inspect_document(path, parser, report)
        except (ValueError, OSError) as exc:
            issue(report, 'document_missing', str(exc))
    check_sections(docs, report)
    try:
        manifest = read_json(safe_file(paper, 'reading_manifest.json', 'manifest'))
        metadata = read_json(safe_file(paper, 'metadata.json', 'metadata'))
    except (ValueError, OSError) as exc:
        issue(report, 'manifest_inputs', str(exc))
        return report
    if manifest.get('schema_version') != 1:
        issue(report, 'manifest_version', 'Expected reading manifest schema_version=1.')
    if not isinstance(manifest.get('paper_title'), str) or not manifest['paper_title'].strip() or manifest['paper_title'] != metadata.get('title'):
        issue(report, 'paper_title', 'Manifest paper_title must match the actual paper title in metadata.')
    for data in (manifest, metadata):
        if any(key in data for key in ('paper_id', 'run_id', 'repo_id', 'related_repo_ids', 'paper_ids')):
            issue(report, 'temporary_identifier', 'Temporary identifiers belong only in execution_state.json.')
    if manifest.get('status') != 'complete':
        issue(report, 'reading_incomplete', f"Declared reading status: {manifest.get('status', 'missing')}", kind='gap')
    source, info = None, manifest.get('source', {})
    if not isinstance(info, dict):
        info = {}
    try:
        source = safe_file(paper, info.get('pdf'), 'source.pdf')
        try:
            import pymupdf as fitz
        except ImportError:
            issue(report, 'pdf_dependency', 'Source PDF validation requires requirements-pdf.txt (PyMuPDF).', kind='gap')
        else:
            with fitz.open(source) as original:
                if not original.is_pdf or not len(original) or original.needs_pass:
                    raise ValueError('Source must be an accessible, nonempty PDF.')
                report['evidence']['source_pdf_pages'] = len(original)
    except Exception as exc:
        issue(report, 'source_pdf', str(exc))
    version = info.get('version', '')
    if not isinstance(version, str) or not version.strip():
        issue(report, 'source_version', 'Record an explicit selected PDF version.')
    else:
        selected = metadata.get('selected_pdf_version') or metadata.get('arxiv_id')
        if selected and selected not in version:
            issue(report, 'metadata_pdf_version', 'Manifest source.version does not contain the metadata-selected PDF version.')
    review = manifest.get('review', {})
    if isinstance(review, dict) and any(key in review for key in ('reviewer', 'reviewer_id', 'author_ids')):
        issue(report, 'temporary_identifier', 'Reviewer identity belongs in the single internal execution record.')
    if not isinstance(review, dict) or review.get('status') != 'verified':
        issue(report, 'review_pending', 'Source/translation/mapping semantic review remains pending.', kind='gap')
    else:
        for field in ('reviewed_at', 'evidence'):
            if not isinstance(review.get(field), str) or not review[field].strip() or PLACEHOLDER.search(review[field]):
                issue(report, 'review_provenance', f'Review needs meaningful {field}.')
        report['evidence']['semantic_review'] = review
    declared = manifest.get('gaps')
    if not isinstance(declared, list):
        issue(report, 'manifest_gaps', 'Manifest must contain gaps as a list (empty only when none remain).')
    else:
        for gap in declared:
            issue(report, 'declared_gap', str(gap), kind='gap')
    differences = manifest.get('verified_differences', [])
    if not isinstance(differences, list):
        issue(report, 'difference_schema', 'verified_differences must be a list; keep unresolved matters in gaps.')
    else:
        roots = [paper] + ([repo_entry.resolve()] if repo_entry else [])
        for item in differences:
            try:
                if not isinstance(item, dict) or not isinstance(item.get('description'), str) or len(item['description'].strip()) < 12 or PLACEHOLDER.search(item['description']):
                    raise ValueError('Each verified difference needs a concrete description.')
                refs = item.get('evidence')
                refs = [refs] if isinstance(refs, str) else refs
                if not isinstance(refs, list) or not refs:
                    raise ValueError('Each verified difference needs local source evidence.')
                for ref in refs:
                    if not isinstance(ref, str) or not ref.strip():
                        raise ValueError('Invalid difference evidence path.')
                    parsed = urlsplit(ref)
                    target = (paper / unquote(parsed.path)).resolve()
                    if parsed.scheme or parsed.netloc or not parsed.path or not target.is_file() or not any(target == base or base in target.parents for base in roots):
                        raise ValueError('Difference evidence must resolve inside the paper or reproduction entry: ' + ref)
                report['verified_differences'].append(item)
            except (ValueError, OSError) as exc:
                issue(report, 'difference_evidence', str(exc))
    open_source = manifest.get('open_source')
    if report['verified_differences'] and (open_source is not True or manifest.get('code', {}).get('status') != 'differences_verified'):
        issue(report, 'difference_status', 'Verified paper/code differences require open_source=true and code.status=differences_verified.')
    if type(open_source) is not bool:
        issue(report, 'open_source_unknown', 'Determine whether the paper has open-source implementation.', kind='gap')
    elif open_source:
        check_code(paper, repo_entry.resolve() if repo_entry else None, manifest, source, parser, report)
    elif repo_entry or metadata.get('related_code_paths') or metadata.get('code_url'):
        issue(report, 'open_source_conflict', 'Known code association conflicts with open_source=false.')
    elif manifest.get('code', {}).get('status') != 'not_applicable':
        issue(report, 'code_status', 'Non-open-source work must explicitly record code.status=not_applicable.')
    report['structural_checks'] = 'passed' if not report['errors'] else 'failed'
    report['workflow_ready'] = not report['errors'] and not report['gaps']
    report['reading_status'] = 'complete' if report['workflow_ready'] else ('blocked' if manifest.get('status') == 'blocked' else 'partial')
    report['correspondence_status'] = ('not_applicable' if open_source is False else
        ('unresolved' if not report['workflow_ready'] else ('differences_verified' if report['verified_differences'] else 'consistent')))
    # Legacy overall status remains conservative; workflow consumers use the two
    # explicit dimensions so an evidenced implementation difference is not a blocker.
    report['status'] = 'partial' if report['workflow_ready'] and report['verified_differences'] else report['reading_status']
    return report


def check_public_artifacts(project: Path, internal_state: dict, extra_paths=()) -> dict:
    """Check managed reading outputs; original sources and unrelated tasks are excluded."""
    from workflow import paper_entries
    project = Path(project).resolve()
    result = {'errors': [], 'gaps': [], 'checked_files': 0}
    temporary, readable = set(), set()
    def normalized(value):
        return re.sub(r'[^\w]', '', value).casefold()
    identities = internal_state.get('identities', {})
    for entries in identities.values():
        for ident, data in entries.items():
            title = data.get('title', '')
            if title and normalized(ident) in normalized(title):
                readable.add(ident)
            else:
                temporary.add(ident)
    for run_id, run in internal_state.get('runs', {}).items():
        temporary.add(run_id)
        for key, candidate in run.get('candidates', {}).items():
            if normalized(key) in normalized(candidate.get('title', '')):
                readable.add(key)
            else:
                temporary.add(key)
        for task in run.get('tasks', {}).values():
            temporary.update(task.get('author_ids', []))
            if task.get('review'):
                temporary.add(task['review'].get('reviewer_id', ''))
    temporary -= readable | {''}
    fields = {'run_id', 'paper_id', 'repo_id', 'paper_ids', 'related_repo_ids',
              'reviewer_id', 'author_ids', 'mapping_ids', 'agent_id', 'task_id', 'thread_id'}
    paths = {contained(project, Path(value) if Path(value).is_absolute() else project / value) for value in extra_paths}
    entries = [path for path, _ in paper_entries(project)]
    for run in internal_state.get('runs', {}).values():
        for task in run.get('tasks', {}).values():
            for field in ('paper_dir', 'repo_entry'):
                if task.get(field):
                    entry = contained(project, project / task[field])
                    if entry.is_dir() and entry not in entries:
                        entries.append(entry)
    reproduce = project / 'reproduce'
    if reproduce.is_dir():
        entries.extend(p.parent for p in reproduce.glob('*/*/metadata.json'))
    for entry in entries:
        paths.update(p for p in entry.iterdir() if p.is_file())
    mapped_sources = set()
    for paper, metadata in paper_entries(project):
        manifest_path = paper / 'reading_manifest.json'
        if not manifest_path.is_file():
            continue
        try:
            manifest = read_json(manifest_path)
            map_name = manifest.get('code', {}).get('mapping_file')
            if not map_name:
                continue
            mappings = read_json(safe_file(paper, map_name, 'mapping_file')).get('mappings', [])
            for value in metadata.get('related_code_paths', []):
                repo, _ = resolve_repo_entry(contained(project, project / value))
                for mapping in mappings:
                    path = safe_file(repo, mapping.get('code_file'), 'code_file')
                    paths.add(path)
                    mapped_sources.add(path)
        except (ValueError, OSError, TypeError) as exc:
            issue(result, 'source_identifier_check', str(exc), paper)
    for folder in ('related_work', 'reproduce'):
        paths.update((project / folder).glob('*.md'))
        paths.update((project / folder).glob('*.json'))
    def inspect_text(path, text):
        for value in temporary:
            if re.search(r'(?<![\w-])' + re.escape(value) + r'(?![\w-])', text):
                issue(result, 'temporary_identifier', 'A runtime identifier remains outside the internal record.', path)
                break
        if re.search(r'\[(?:END )?P2C\b|[\w-]+:M\d{2,}\b', text):
            issue(result, 'temporary_annotation', 'Replace annotation identifiers with paper/code positions.', path)
    def inspect_json(value, path):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in fields:
                    issue(result, 'temporary_identifier_field', 'Runtime identity field remains: ' + key, path)
                inspect_json(child, path)
        elif isinstance(value, list):
            for child in value:
                inspect_json(child, path)
    for path in sorted(paths):
        inspect_text(path, str(path.relative_to(project)))
        if path in mapped_sources or path.suffix.lower() in ('.md', '.json', '.html', '.txt', '.csv'):
            text = path.read_text(encoding='utf-8')
            inspect_text(path, text)
            if path.suffix == '.json':
                try:
                    inspect_json(json.loads(text), path)
                except ValueError:
                    issue(result, 'invalid_json', 'Cannot verify malformed metadata.', path)
            result['checked_files'] += 1
        elif path.name == 'paper_annotated.pdf':
            try:
                import pymupdf as fitz
                with fitz.open(path) as doc:
                    inspect_text(path, '\n'.join(str(v) for v in doc.metadata.values()))
                    for page in doc:
                        for ann in page.annots() or []:
                            inspect_text(path, '\n'.join(str(v) for v in ann.info.values()))
                result['checked_files'] += 1
            except Exception as exc:
                issue(result, 'pdf_identifier_check', str(exc), path)
    return result


CSS = '''
:root{color-scheme:light;--ink:#182435;--muted:#536172;--rule:#273a50;--paper:#fff;--accent:#245d84}
*{box-sizing:border-box}body{margin:0;background:#eef1f4;color:var(--ink);font:16px/1.8 system-ui,-apple-system,"Noto Sans CJK SC",sans-serif}
main{max-width:1060px;margin:0 auto;padding:42px 48px 90px;background:var(--paper)}
h1,h2,h3,h4{line-height:1.4;scroll-margin-top:24px}h1{font-size:2rem}h2{margin-top:2.3em;font-size:1.6rem}h3{font-size:1.2rem}
p,li,td,th,a{overflow-wrap:anywhere}a{color:var(--accent)}.notice{padding:14px 18px;background:#eef4f8;border-left:4px solid var(--accent)}
nav ul{padding-left:22px}.document{border-top:1px solid #d5dde5;margin-top:48px;padding-top:14px}
.table-wrap{max-width:100%;overflow-x:auto;margin:1.5em 0}table{width:100%;border-collapse:collapse;border-top:2px solid var(--rule);border-bottom:2px solid var(--rule);table-layout:fixed;font-size:.94rem}
thead{border-bottom:1px solid var(--rule)}th,td{padding:10px 12px;text-align:left;vertical-align:top;border:0}th{font-weight:650}tbody tr{border:0}
blockquote{margin:1.3em 0;padding:4px 18px;border-left:3px solid #bccbd8;color:#34475a;background:#f7f9fb}
pre{max-width:100%;overflow-x:auto;white-space:pre;padding:16px;background:#f0f3f6;border-radius:5px;font-size:.86rem;line-height:1.55}code{font-family:ui-monospace,Consolas,monospace}p code,li code,td code{white-space:break-spaces;overflow-wrap:anywhere;background:#f0f3f6;padding:1px 4px}
img{max-width:100%;height:auto}.status{font-weight:700}.source-link{font-size:.85rem;color:var(--muted)}
@media(max-width:680px){main{padding:22px 18px 50px}th,td{padding:7px 6px}h1{font-size:1.65rem}}
@media print{body{background:white}main{max-width:none;padding:0}nav{display:none}pre{white-space:pre-wrap}a{color:inherit}h2,h3{break-after:avoid}tr{break-inside:avoid}}
'''


def anchor_name(title: str) -> str:
    number = re.match(r'Q([1-6])\b', title, re.I)
    if number:
        return 'q' + number.group(1)
    return re.sub(r'[^\w\u4e00-\u9fff-]+', '-', title.lower()).strip('-') or 'section'


def render(paper: Path, repo_entry: Path | None = None, output: Path | None = None, diagnostic: bool = False) -> tuple[Path, dict]:
    parser, paper = markdown(), paper.resolve()
    output_target = (output or paper / 'reading.html').resolve()
    def local_url(path: Path, fragment: str = '') -> str:
        result = quote(os.path.relpath(path, output_target.parent).replace(os.sep, '/'), safe='/')
        return result + ('#' + quote(fragment, safe='') if fragment else '')
    report = check(paper, repo_entry)
    documents = [(paper / name, Path(name).stem) for name in DOCS if (paper / name).is_file()]
    if repo_entry and (repo_entry / 'code_map.md').is_file():
        documents.append((repo_entry.resolve() / 'code_map.md', 'code_map'))
    prepared, anchors = [], {}
    for path, key in documents:
        tokens = parser.parse(path.read_text(encoding='utf-8'))
        used = set()
        for i, token in enumerate(tokens):
            if token.type == 'heading_open':
                title = visible(tokens[i + 1].children)
                base = anchor_name(title)
                suffix, number = base, 2
                while suffix in used:
                    suffix, number = f'{base}-{number}', number + 1
                used.add(suffix)
                token.attrSet('id', key + '-' + suffix)
                anchors[(path.resolve(), base)] = key + '-' + suffix
        prepared.append((path, key, tokens))
    def safe_html(tokens, idx, options, env):
        raw = tokens[idx].content.strip()
        if re.fullmatch(r'<br\s*/?>', raw, re.I):
            return '<br>'
        # Named anchors are fulfilled by matching headings; everything else is
        # escaped/omitted, including scripts, remote styles, and HTML images.
        if re.fullmatch(r'<a\s+id=[\"\'][a-zA-Z0-9_-]+[\"\']\s*>(?:\s*</a>)?|</a>', raw) or raw.startswith('<!--'):
            return ''
        return html.escape(raw)
    parser.renderer.rules['html_inline'] = safe_html
    parser.renderer.rules['html_block'] = safe_html
    def image_as_link(tokens, idx, options, env):
        label = visible(tokens[idx].children) or tokens[idx].content or '图像'
        return '<span class="source-link">[图像：' + html.escape(label) + '；请打开 Markdown 原文查看]</span>'
    parser.renderer.rules['image'] = image_as_link
    navigation, rendered = [], []
    for path, key, tokens in prepared:
        for token in tokens:
            for child in token.children or []:
                if child.type != 'link_open':
                    continue
                href = child.attrGet('href') or ''
                parts = urlsplit(href)
                if not parts.scheme and not parts.netloc:
                    target = (path.parent / unquote(parts.path)).resolve() if parts.path else path.resolve()
                    if parts.fragment and (target, unquote(parts.fragment)) in anchors:
                        child.attrSet('href', '#' + anchors[(target, unquote(parts.fragment))])
                    elif any(target == other.resolve() for other, _ in documents) and not parts.fragment:
                        target_key = next(k for other, k in documents if other.resolve() == target)
                        child.attrSet('href', '#doc-' + target_key)
                    else:
                        child.attrSet('href', local_url(target, unquote(parts.fragment)))
                elif parts.scheme not in ('http', 'https', 'mailto'):
                    child.attrSet('href', '#')
        body = parser.renderer.render(tokens, parser.options, {})
        body = body.replace('<table>', '<div class="table-wrap"><table>').replace('</table>', '</table></div>')
        navigation.append(f'<li><a href="#doc-{key}">{html.escape(path.name)}</a></li>')
        rendered.append(f'<section class="document" id="doc-{key}"><p class="source-link"><a href="{html.escape(local_url(path))}">源文件：{html.escape(path.name)}</a></p>{body}</section>')
    title = paper.name + ' — 论文阅读'
    status = html.escape(report['status'])
    errors = ''.join('<li>' + html.escape(item['message']) + '</li>' for item in report['errors'] + report['gaps'])
    details = f'<details><summary>验收缺口（{len(report["errors"]) + len(report["gaps"])}）</summary><ul>{errors}</ul></details>' if errors else ''
    audit = f'<aside class="notice"><span class="status">验收状态：{status}</span><br>{NOTICE}{details}</aside>' if diagnostic else ''
    page = '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' + f'<title>{html.escape(title)}</title><style>{CSS}</style></head><body><main><h1>{html.escape(title)}</h1>{audit}<nav aria-label="文档目录"><ul>{"".join(navigation)}</ul></nav>{"".join(rendered)}</main></body></html>\n'
    target = output or paper / 'reading.html'
    if target.is_symlink() or target.resolve() in {p.resolve() for p, _ in documents}:
        raise ValueError('Refusing to overwrite a source document or symlink.')
    if target.exists() and '<!-- GENERATED BY reading_artifacts.py -->' not in target.read_text(encoding='utf-8'):
        raise ValueError('Existing HTML is not a generated reading copy; choose another --output to preserve it.')
    target.write_text('<!-- GENERATED BY reading_artifacts.py -->\n' + page, encoding='utf-8')
    return target, report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('check', 'render'):
        cmd = sub.add_parser(name)
        cmd.add_argument('paper_directory', type=Path)
        cmd.add_argument('--repo-entry', type=Path)
        cmd.add_argument('--json', action='store_true', help='Emit machine-readable report on stdout')
        if name == 'render':
            cmd.add_argument('--output', type=Path)
            cmd.add_argument('--diagnostic', action='store_true', help='Include internal checks in a diagnostic copy; final reader output omits them')
    args = parser.parse_args()
    try:
        if args.command == 'render':
            target, report = render(args.paper_directory, args.repo_entry, args.output, args.diagnostic)
            report['html_output'] = str(target.resolve())
        else:
            report = check(args.paper_directory, args.repo_entry)
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"Reading: {report['reading_status']}; correspondence: {report['correspondence_status']}; structural checks: {report['structural_checks']}")
            print(NOTICE)
            for kind in ('errors', 'gaps'):
                for item in report[kind]:
                    where = f" ({item['path']}:{item.get('line', '')})" if 'path' in item else ''
                    print(f"[{kind}/{item['code']}] {item['message']}{where}")
            if report.get('html_output'):
                print('HTML generated: ' + report['html_output'])
        return 0 if report['workflow_ready'] else 1
    except (ValueError, OSError, TypeError, KeyError) as exc:
        if args.json:
            print(json.dumps({'status': 'blocked', 'errors': [{'code': 'input_error', 'message': str(exc)}]}, ensure_ascii=False))
        else:
            print(f'Error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
