#!/usr/bin/env python3
"""Local three-round literature ledger. Records work; never launches agents/jobs.

The main agent calls this serially after doing the work. Independent agent IDs
are provenance supplied by that caller, not identities authenticated by Python.
No Markdown is overwritten. File size/mtime and Git versions detect stale review;
no hashes are stored. Acceptance still requires actual reading artifacts.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import datetime as dt
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

import reading_artifacts as reading
import execution_state
from workflow import contained, read_json, resolve_repo_entry, slug

LIMITS = {'ultra': 40, 'max': 30, 'mid': 20, 'inbox': 10}
FORMAL = ('materials', 'classified', 'drafted', 'mapped', 'self_checked', 'reviewed')
SCREENING = ('screening', 'screened')
TYPES = ('method', 'survey', 'benchmark', 'dataset', 'theory', 'tool')


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')


def nonblank(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{name} must be nonempty text')
    return value.strip()


def doi(value):
    if not value:
        return ''
    value = unquote(str(value)).strip().lower()
    value = re.sub(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', '', value)
    if not re.fullmatch(r'10\.\d{4,9}/\S+', value):
        raise ValueError('Invalid DOI')
    return value


def arxiv(value):
    if not value:
        return ''
    value = str(value).strip().lower()
    value = re.sub(r'^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/', '', value)
    value = re.sub(r'\.pdf$', '', value)
    value = re.sub(r'v[1-9]\d*$', '', value)
    if not re.fullmatch(r'(?:\d{4}\.\d{4,5}|[a-z][a-z.-]*/\d{7})', value):
        raise ValueError('Invalid arXiv ID')
    return value


def event(state, action, paper_id=None, **fields):
    state['events'].append({'seq': len(state['events']) + 1, 'at': now(),
                            'round': state['current_round'], 'action': action,
                            **({'paper_id': paper_id} if paper_id else {}), **fields})
    state['updated_at'] = now()


class Run:
    def __init__(self, project: Path, run_id: str):
        self.project = Path(project).expanduser().resolve()
        slug(run_id)
        self.path = execution_state.state_path(self.project)
        self.directory = self.path.parent
        self.run_id = run_id

    def reject_legacy_runs(self):
        legacy = self.directory / 'literature_runs'
        if legacy.is_dir() and any(legacy.rglob('state.json')):
            raise ValueError('Legacy literature run files remain. Merge their states and reports into execution_state.json, preserving rounds and quota charges, then remove the old run directories before continuing; do not initialize a fresh run to reset quotas')

    def init(self):
        self.reject_legacy_runs()
        if not self.project.is_dir():
            raise ValueError('Project directory must already exist')
        state = {'schema_version': 1, 'run_id': self.run_id, 'project': str(self.project),
                 'status': 'active', 'current_round': 1, 'max_rounds': 3,
                 'limits': dict(LIMITS), 'used': {k: 0 for k in LIMITS}, 'charges': [],
                 'candidates': {}, 'tasks': {}, 'rounds': [{'number': 1, 'status': 'open', 'task_ids': []}],
                 'events': [], 'termination': None, 'created_at': now()}
        event(state, 'initialized')
        with execution_state.transaction(self.project) as journal:
            if self.run_id in journal['runs']:
                raise ValueError('Run already exists in the execution journal')
            active = journal.get('active_run')
            if active and journal['runs'].get(active, {}).get('status') == 'active':
                raise ValueError('Finalize the current active run before initializing another run')
            journal['runs'][self.run_id] = state
            journal['active_run'] = self.run_id
        return state

    def load(self):
        self.reject_legacy_runs()
        journal = execution_state.load(self.project)
        if self.run_id not in journal['runs']:
            raise ValueError('Initialize this run in the execution journal first')
        return self.validate_record(journal['runs'][self.run_id])

    def validate_record(self, state):
        if state.get('schema_version') != 1 or state.get('project') != str(self.project) or state.get('run_id') != self.run_id:
            raise ValueError('State schema, project or run ID mismatch')
        if state.get('limits') != LIMITS or state.get('max_rounds') != 3:
            raise ValueError('Run limits must remain ultra40/max30/mid20/inbox10 and three rounds')
        round_number = state.get('current_round')
        if type(round_number) is not int or not 1 <= round_number <= 3:
            raise ValueError('Current round must be between 1 and 3')
        if [item.get('number') for item in state.get('rounds', [])] != list(range(1, round_number + 1)):
            raise ValueError('Round history must be consecutive and agree with current_round')
        # Validate the actual charges, not a caller-edited used counter.
        used, lanes = {k: 0 for k in LIMITS}, set()
        for item in state['charges']:
            key = (item['paper_id'], item['lane'])
            if key in lanes or item['priority'] not in LIMITS or item['lane'] not in ('screening', 'formal'):
                raise ValueError('Duplicate or invalid quota charge')
            if (item['priority'] == 'inbox') != (item['lane'] == 'screening'):
                raise ValueError('Quota charge lane mismatch')
            lanes.add(key)
            used[item['priority']] += 1
        if used != state['used'] or any(used[k] > LIMITS[k] for k in LIMITS):
            raise ValueError('Quota counters disagree with the ledger or exceed limits')
        for key, task in state['tasks'].items():
            initial = task.get('initial_formal_priority')
            lane = 'formal' if initial is not None else 'screening'
            charge = next((item for item in state['charges'] if item['paper_id'] == key and item['lane'] == lane), None)
            if not charge or charge['priority'] != (initial if initial is not None else 'inbox'):
                raise ValueError('Assigned task is missing its original quota charge')
        return state

    @contextmanager
    def transaction(self):
        self.reject_legacy_runs()
        with execution_state.transaction(self.project) as journal:
            if self.run_id not in journal['runs']:
                raise ValueError('Initialize this run in the execution journal first')
            state = self.validate_record(journal['runs'][self.run_id])
            if state['status'] != 'active':
                raise ValueError('This run is finalized; state is read-only')
            self._journal = journal
            try:
                yield state
            finally:
                del self._journal

    def file(self, value, name='artifact', minimum=1):
        value = nonblank(value, name)
        if Path(value).is_absolute():
            raise ValueError(f'{name} must be relative to the project')
        path = contained(self.project, self.project / value)
        if not path.is_file() or path.stat().st_size < minimum:
            raise ValueError(f'{name} is missing or empty: {value}')
        return path

    def folder(self, value, name):
        value = nonblank(value, name)
        if Path(value).is_absolute():
            raise ValueError(f'{name} must be relative to the project')
        path = contained(self.project, self.project / value)
        if not path.is_dir():
            raise ValueError(f'{name} directory does not exist: {value}')
        return path

    def report_text(self, value, name='stage report'):
        if isinstance(value, dict) and isinstance(value.get('content'), str):
            text = value['content'].strip()
        elif isinstance(value, dict) and isinstance(value.get('text'), str):
            text = value['text'].strip()
        elif isinstance(value, dict) and isinstance(value.get('file'), str):
            path = Path(value['file']).expanduser().resolve()
            if path == self.project or self.project in path.parents:
                raise ValueError(f'{name} input must be outside the project; execution reports persist only inside execution_state.json')
            text = path.read_text(encoding='utf-8').strip()
        elif isinstance(value, str):
            raise ValueError(f'{name}: use an inline text object or an external file object, not a project report path')
        else:
            raise ValueError(f'{name} requires inline text or an external report file')
        if len(text) < 20 or reading.PLACEHOLDER.search(text):
            raise ValueError(f'{name} must contain actual findings, not an empty template')
        return text

    def embed_report(self, evidence, field):
        aliases = {'latex_search_report': 'latex_search'}
        prefix = aliases.get(field, field)
        value = evidence.get(field)
        if value is None and prefix + '_text' in evidence:
            value = {'text': evidence.pop(prefix + '_text')}
        if value is None and prefix + '_file' in evidence:
            value = {'file': evidence.pop(prefix + '_file')}
        evidence[field] = {'content': self.report_text(value, field), 'imported_at': now()}
        # External input paths are intentionally not persisted in the journal.
        evidence.pop(prefix + '_text', None)
        evidence.pop(prefix + '_file', None)

    def relative(self, path):
        return contained(self.project, path).relative_to(self.project).as_posix()

    def identify(self, state, ident):
        for key, candidate in state['candidates'].items():
            if ident == key or ident in candidate['aliases']:
                return key
        raise ValueError(f'Unknown candidate: {ident}')

    def charge(self, state, key, priority):
        lane = 'screening' if priority == 'inbox' else 'formal'
        previous = next((x for x in state['charges'] if x['paper_id'] == key and x['lane'] == lane), None)
        if previous:
            return previous['priority']
        # A formal task subsequently downgraded to screening retains its initial
        # formal charge; it does not consume a fresh screening slot.
        if lane == 'screening' and any(x['paper_id'] == key and x['lane'] == 'formal' for x in state['charges']):
            return next(x['priority'] for x in state['charges'] if x['paper_id'] == key and x['lane'] == 'formal')
        if state['used'][priority] >= LIMITS[priority]:
            raise ValueError(f'Quota exhausted: {priority} ({LIMITS[priority]})')
        state['charges'].append({'paper_id': key, 'lane': lane, 'priority': priority, 'round': state['current_round'], 'at': now()})
        state['used'][priority] += 1
        event(state, 'quota_charged', key, lane=lane, charged_priority=priority)
        return priority

    def add_candidate(self, data):
        with self.transaction() as state:
            if state['rounds'][-1]['status'] != 'open':
                raise ValueError('Round is closed; finalize or start the next eligible round')
            key = slug(nonblank(data.get('paper_id'), 'paper_id'))
            priority = data.get('priority')
            if priority not in LIMITS or data.get('paper_type', 'method') not in TYPES:
                raise ValueError('Invalid priority or paper_type')
            title = nonblank(data.get('title'), 'title')
            source = nonblank(data.get('source'), 'source')
            need = nonblank(data.get('need'), 'need')
            normalized = {'doi': doi(data.get('doi')), 'arxiv': arxiv(data.get('arxiv_id'))}
            discovered = data.get('discovered_from')
            available = 1
            if discovered is not None:
                if not isinstance(discovered, dict):
                    raise ValueError('discovered_from must name paper_id and original-paper location')
                parent = self.identify(state, nonblank(discovered.get('paper_id'), 'discovered_from.paper_id'))
                location = nonblank(discovered.get('location'), 'discovered_from.location')
                task = state['tasks'].get(parent)
                if not task or task['priority'] == 'inbox':
                    raise ValueError('Only an assigned formal paper may supply expansion candidates; inbox cannot expand')
                if task['phase'] not in ('classified', 'drafted', 'mapped', 'self_checked', 'reviewed'):
                    raise ValueError('Expansion requires the parent paper full quick-read/classification first')
                discovered = {'paper_id': parent, 'location': location}
                available = state['current_round'] + 1
            elif state['current_round'] != 1:
                if data.get('origin') != 'backlog':
                    raise ValueError('After round 1, newly discovered candidates require discovered_from; pre-existing backlog must explicitly use origin=backlog')
                available = state['current_round']
            matches = []
            for existing, candidate in state['candidates'].items():
                same_key = key == existing or key in candidate['aliases']
                identity_match = any(normalized[k] and normalized[k] in candidate['identities'][k] for k in normalized)
                if same_key or identity_match:
                    matches.append(existing)
            if len(matches) > 1:
                raise ValueError('Candidate bridges multiple existing identities; resolve the duplicate records before proceeding')
            if matches:
                existing = matches[0]
                candidate = state['candidates'][existing]
                for kind, value in normalized.items():
                    if value and candidate['identities'][kind] and value not in candidate['identities'][kind]:
                        raise ValueError(f'Conflicting {kind} for an existing paper identity')
                if key != existing and key not in candidate['aliases']:
                    candidate['aliases'].append(key)
                for kind, value in normalized.items():
                    if value and value not in candidate['identities'][kind]:
                        candidate['identities'][kind].append(value)
                candidate['sources'].append({'source': source, 'need': need, 'discovered_from': discovered})
                event(state, 'candidate_deduplicated', existing, submitted_paper_id=key)
                return existing
            state['candidates'][key] = {'paper_id': key, 'aliases': [], 'title': title,
                'paper_type': data.get('paper_type', 'method'), 'priority': priority,
                'identities': {k: [v] if v else [] for k, v in normalized.items()},
                'sources': [{'source': source, 'need': need, 'discovered_from': discovered}],
                'available_round': available, 'status': 'queued'}
            event(state, 'candidate_added', key, available_round=available)
            return key

    def assign_author(self, state, key, task, agent):
        agent = nonblank(agent, 'author agent ID')
        if any(agent in other.get('author_ids', []) for ident, other in state['tasks'].items() if ident != key):
            raise ValueError('One paper per author subagent: this agent already owns another paper')
        if task.get('review') and task['review']['reviewer_id'] == agent:
            raise ValueError('A reviewer may not become the author without first invalidating that review')
        task['author_id'] = agent
        if agent not in task['author_ids']:
            task['author_ids'].append(agent)

    def dispatch(self, ident, agent, priority=None):
        with self.transaction() as state:
            if state['rounds'][-1]['status'] != 'open':
                raise ValueError('Cannot dispatch into a closed round')
            key = self.identify(state, ident)
            candidate = state['candidates'][key]
            if candidate['available_round'] > state['current_round']:
                raise ValueError('Newly discovered papers must wait until the next round')
            selected = priority or candidate['priority']
            if selected not in LIMITS:
                raise ValueError('Invalid priority')
            previous = state['tasks'].get(key)
            if candidate['status'] in ('blocked', 'deferred', 'rejected'):
                raise ValueError('Candidate is blocked or deliberately deferred/rejected; resume it before dispatch')
            if previous and not (previous['phase'] == 'screened' and selected != 'inbox'):
                raise ValueError('Paper already assigned; use advance, block/resume or revise, not redispatch')
            charged = self.charge(state, key, selected)
            task = {'paper_id': key, 'priority': selected, 'initial_formal_priority': charged if selected != 'inbox' else None,
                    'author_id': '', 'author_ids': previous['author_ids'][:] if previous else [],
                    'round': state['current_round'], 'status': 'active', 'phase': 'dispatched',
                    'revision': previous['revision'] + 1 if previous else 1, 'evidence': {}, 'review': None,
                    'paper_dir': None, 'repo_entry': None, 'open_source': None, 'blocked': None}
            self.assign_author(state, key, task, agent)
            state['tasks'][key] = task
            candidate.update(status='assigned', priority=selected)
            if key not in state['rounds'][-1]['task_ids']:
                state['rounds'][-1]['task_ids'].append(key)
            event(state, 'dispatched', key, author_id=agent, priority=selected, revision=task['revision'])
            return copy.deepcopy(task)

    def artifact_paths(self, task):
        paths = set()
        for evidence in task['evidence'].values():
            for field in ('report', 'review_report', 'latex_search_report'):
                if evidence.get(field):
                    self.report_text(evidence[field], field)
        if task.get('paper_dir'):
            paper = self.folder(task['paper_dir'], 'paper_dir')
            for name in ('metadata.json', 'note.md', 'translation_zh.md', 'qa.md', 'reading_manifest.json', 'paper.pdf', 'reading.html'):
                if (paper / name).exists():
                    paths.add(self.file(self.relative(paper / name)))
            if (paper / 'reading_manifest.json').is_file():
                manifest = read_json(paper / 'reading_manifest.json')
                code = manifest.get('code') or {}
                for field in ('mapping_file', 'annotated_pdf'):
                    if code.get(field) and (paper / code[field]).is_file():
                        paths.add(self.file(self.relative(paper / code[field])))
                if task.get('repo_entry') and code.get('mapping_file') and (paper / code['mapping_file']).is_file():
                    repo, _ = resolve_repo_entry(self.folder(task['repo_entry'], 'repo_entry'))
                    for mapping in read_json(paper / code['mapping_file']).get('mappings', []):
                        paths.add(self.file(self.relative(contained(repo, repo / mapping['code_file']))))
        if task.get('repo_entry'):
            entry = self.folder(task['repo_entry'], 'repo_entry')
            for name in ('metadata.json', 'code_map.md'):
                if (entry / name).exists():
                    paths.add(self.file(self.relative(entry / name)))
        for value in task['evidence'].get('materials', {}).get('latex_files', []):
            paths.add(self.file(value, 'latex_file'))
        return sorted(paths)

    def stamps(self, task):
        return {self.relative(path): {'size': path.stat().st_size, 'mtime_ns': path.stat().st_mtime_ns}
                for path in self.artifact_paths(task)}

    def public_identity(self, metadata, paper):
        return {'title': nonblank(metadata.get('title'), 'paper title'),
                'doi': doi(metadata.get('doi')), 'arxiv': arxiv(metadata.get('arxiv_id')),
                'source_url': metadata.get('source_url', ''), 'path': self.relative(paper)}

    def verify_registered_materials(self, task):
        registered = task['evidence'].get('materials')
        if not registered:
            raise ValueError('No accepted materials evidence is registered')
        paper = self.folder(task['paper_dir'], 'paper_dir')
        metadata = read_json(self.file(self.relative(paper / 'metadata.json'), 'paper metadata'))
        selected = metadata.get('selected_pdf_version') or metadata.get('arxiv_id')
        if self.public_identity(metadata, paper) != registered['identity'] or selected != registered['pdf_version']:
            raise ValueError('Paper identity or PDF version changed after materials; explicitly revise --from-stage materials')
        if registered['latex_files'] and metadata.get('selected_source_version', registered['latex_version']) != registered['latex_version']:
            raise ValueError('LaTeX version changed after materials; explicitly revise --from-stage materials')
        for value, stamp in registered['source_stamps'].items():
            path = self.file(value, 'registered paper source')
            if {'size': path.stat().st_size, 'mtime_ns': path.stat().st_mtime_ns} != stamp:
                raise ValueError('Paper source changed after materials; explicitly revise --from-stage materials')
        if (paper / 'reading_manifest.json').is_file():
            manifest = read_json(paper / 'reading_manifest.json')
            version = re.sub(r'^arxiv:\s*', '', str(manifest.get('source', {}).get('version', '')), flags=re.I)
            if manifest.get('paper_title') != registered['identity']['title'] or version != registered['pdf_version']:
                raise ValueError('Reading manifest identity/version differs from accepted materials')
            if manifest.get('open_source') is not task['open_source']:
                raise ValueError('Code availability changed after materials; explicitly revise --from-stage materials')
        if task['open_source']:
            repo, meta = resolve_repo_entry(self.folder(task['repo_entry'], 'repo_entry'))
            if meta.get('commit') != registered['repo_commit'] or reading.git(repo, 'rev-parse', 'HEAD') != registered['repo_commit']:
                raise ValueError('Selected source-code commit changed after materials; explicitly revise --from-stage materials')

    def check_artifacts(self, task, ready=False):
        if not task.get('paper_dir'):
            raise ValueError('Paper materials have not been registered')
        paper = self.folder(task['paper_dir'], 'paper_dir')
        entry = self.folder(task['repo_entry'], 'repo_entry') if task.get('repo_entry') else None
        self.verify_registered_materials(task)
        report = reading.check(paper, entry)
        if report.get('structural_checks') != 'passed':
            codes = ', '.join(x.get('code', '?') for x in report.get('errors', []))
            raise ValueError('Reading artifacts failed structural checks: ' + codes)
        if ready and report.get('workflow_ready') is not True:
            raise ValueError('Reading artifacts are not workflow_ready; unresolved evidence gaps must be fixed or explicitly blocked')
        if ready:
            page = self.file(self.relative(paper / 'reading.html'), 'HTML reading copy', 40)
            if '<!-- GENERATED BY reading_artifacts.py -->' not in page.read_text(encoding='utf-8'):
                raise ValueError('reading.html is not an identified generated reading copy')
            sources = [paper / name for name in reading.DOCS]
            if entry and (entry / 'code_map.md').is_file():
                sources.append(entry / 'code_map.md')
            if page.stat().st_mtime_ns < max(path.stat().st_mtime_ns for path in sources):
                raise ValueError('reading.html is stale relative to the Markdown reading artifacts; render again before independent review')
        return {k: report.get(k) for k in ('workflow_ready', 'reading_status', 'correspondence_status', 'status', 'structural_checks')}

    def advance(self, ident, stage, evidence):
        with self.transaction() as state:
            key = self.identify(state, ident)
            task = state['tasks'].get(key)
            if not task or task['status'] != 'active':
                raise ValueError('Task must be assigned and active; resume blocked tasks first')
            stages = SCREENING if task['priority'] == 'inbox' else FORMAL
            expected = stages[0] if task['phase'] == 'dispatched' else stages[stages.index(task['phase']) + 1]
            if stage != expected:
                raise ValueError(f'Expected stage {expected}, not {stage}')
            if not isinstance(evidence, dict):
                raise ValueError('Stage evidence must be a JSON object')
            evidence = copy.deepcopy(evidence)
            report_field = 'review_report' if stage in ('reviewed', 'screened') else 'report'
            self.embed_report(evidence, report_field)
            if stage == 'materials':
                paper = self.folder(evidence.get('paper_dir'), 'paper_dir')
                metadata = read_json(self.file(self.relative(paper / 'metadata.json'), 'paper metadata'))
                identity = self.public_identity(metadata, paper)
                candidate = state['candidates'][key]
                if ' '.join(identity['title'].split()).casefold() != ' '.join(candidate['title'].split()).casefold():
                    raise ValueError('Paper metadata title differs from candidate')
                if any(candidate['identities'][kind] and identity[kind] not in candidate['identities'][kind] for kind in ('doi', 'arxiv')):
                    raise ValueError('Paper metadata public identifier differs from candidate')
                pdf = self.file(self.relative(paper / 'paper.pdf'), 'PDF')
                try:
                    import pymupdf as fitz
                except ImportError as exc:
                    raise ValueError('Materials verification requires PyMuPDF; record a block if unavailable') from exc
                try:
                    with fitz.open(pdf) as document:
                        if not document.is_pdf or not len(document) or document.needs_pass:
                            raise ValueError('PDF is empty or inaccessible')
                except Exception as exc:
                    raise ValueError(f'Materials require a valid readable PDF: {exc}') from exc
                version = nonblank(evidence.get('pdf_version'), 'pdf_version')
                if (metadata.get('selected_pdf_version') or metadata.get('arxiv_id')) != version:
                    raise ValueError('Selected PDF version differs from paper metadata')
                tex = evidence.get('latex_files')
                if not isinstance(tex, list):
                    raise ValueError('latex_files must list the available same-version source files (or be empty with unavailable evidence)')
                if tex:
                    if version != evidence.get('latex_version') or metadata.get('selected_source_version', version) != version:
                        raise ValueError('PDF and LaTeX versions must match')
                else:
                    nonblank(evidence.get('latex_unavailable_reason'), 'latex_unavailable_reason')
                    self.embed_report(evidence, 'latex_search_report')
                for value in tex:
                    path = self.file(value, 'LaTeX source')
                    if path.suffix.lower() != '.tex' or paper not in path.parents:
                        raise ValueError('LaTeX source must belong to this paper directory')
                open_source = evidence.get('open_source')
                if type(open_source) is not bool:
                    raise ValueError('Record whether an open-source implementation exists')
                entry = None
                if open_source:
                    entry = self.folder(evidence.get('repo_entry'), 'repo_entry')
                    repo, repo_metadata = resolve_repo_entry(entry)
                    code_url = str(metadata.get('code_url', '')).rstrip('/').removesuffix('.git')
                    repo_url = str(repo_metadata.get('repo_url', '')).rstrip('/').removesuffix('.git')
                    linked = (self.relative(entry) in metadata.get('related_code_paths', [])
                              or self.relative(paper) in repo_metadata.get('related_paper_paths', [])
                              or bool(code_url and code_url == repo_url))
                    if not linked:
                        raise ValueError('Selected repository must be associated by a public project path or matching code URL')
                    if Path(reading.git(repo, 'rev-parse', '--show-toplevel')).resolve() != repo:
                        raise ValueError('Selected repo_path must be a real checkout')
                    if reading.git(repo, 'rev-parse', 'HEAD') != repo_metadata.get('commit'):
                        raise ValueError('Repository HEAD and selected commit differ')
                    evidence['repo_commit'] = repo_metadata['commit']
                elif metadata.get('code_url') or metadata.get('related_code_paths'):
                    raise ValueError('Known repository conflicts with open_source=false')
                task.update(paper_dir=self.relative(paper), repo_entry=self.relative(entry) if entry else None, open_source=open_source)
                evidence['identity'] = identity
                registry = self._journal['identities']['papers']
                previous = registry.get(key, {})
                if previous and previous.get('path') != task['paper_dir']:
                    raise ValueError('Internal paper reference is already registered to another path; use the existing paper or a new internal reference')
                registry[key] = {**previous, 'path': task['paper_dir'], 'title': identity['title']}
                evidence['source_stamps'] = {self.relative(path): {'size': path.stat().st_size, 'mtime_ns': path.stat().st_mtime_ns}
                    for path in [pdf, *(self.file(value) for value in tex)]}
            elif stage == 'classified':
                priority = evidence.get('priority')
                if priority not in LIMITS:
                    raise ValueError('Full quick-read must confirm a valid priority')
                task['priority'] = priority
                state['candidates'][key]['priority'] = priority
                # Formal reclassification never refunds or recharges a slot.
                if priority == 'inbox':
                    stage = 'screening'
            elif stage == 'drafted':
                paper = self.folder(task['paper_dir'], 'paper_dir')
                parser = reading.markdown()
                result = {'documents': {}, 'errors': [], 'gaps': []}
                docs = {name: reading.inspect_document(self.file(self.relative(paper / name)), parser, result) for name in reading.DOCS}
                reading.check_sections(docs, result)
                if result['errors']:
                    raise ValueError('Draft sections are incomplete: ' + ', '.join(x['code'] for x in result['errors']))
            elif stage in ('mapped', 'self_checked'):
                evidence['artifact_check'] = self.check_artifacts(task)
            elif stage in ('reviewed', 'screened'):
                reviewer = nonblank(evidence.get('reviewer_id'), 'reviewer_id')
                if reviewer in task['author_ids']:
                    raise ValueError('Independent reviewer must differ from every author of this paper')
                review_text = self.report_text(evidence['review_report'], 'independent review report')
                if any(review_text == self.report_text(item['report']) for item in task['evidence'].values() if item.get('report')):
                    raise ValueError('Independent review must have its own report, not reuse an author stage report')
                if stage == 'reviewed':
                    evidence['artifact_check'] = self.check_artifacts(task, ready=True)
                task['review'] = {'reviewer_id': reviewer, 'author_revision': task['revision'],
                                  'review_report': evidence['review_report'], 'at': now()}
                task['status'] = 'done'
                state['candidates'][key]['status'] = 'done'
            task['phase'] = stage
            task['evidence'][stage] = evidence
            if stage in ('reviewed', 'screened'):
                task['review']['artifacts'] = self.stamps(task)
                if task.get('paper_dir'):
                    self._journal['papers'].setdefault(task['paper_dir'], {})['review'] = {
                        'status': 'verified', 'reviewer_id': task['review']['reviewer_id'],
                        'author_ids': task['author_ids'][:], 'revision': task['revision'],
                        'report_text': evidence['review_report']['content'], 'reviewed_at': task['review']['at']}
            event(state, 'stage_completed', key, phase=stage, revision=task['revision'],
                  evidence=copy.deepcopy(evidence), reviewer_id=evidence.get('reviewer_id'))
            return copy.deepcopy(task)

    def block(self, ident, reason):
        with self.transaction() as state:
            key = self.identify(state, ident)
            task = state['tasks'].get(key)
            reason = nonblank(reason, 'blocking reason')
            if task:
                if task['status'] == 'done':
                    raise ValueError('Use revise before blocking a completed task')
                task.update(status='blocked', blocked={'reason': reason, 'at': now(), 'phase': task['phase']})
            else:
                state['candidates'][key].update(status='blocked', blocked={'reason': reason, 'at': now()})
            event(state, 'blocked', key, reason=reason)

    def resume(self, ident, agent=None):
        with self.transaction() as state:
            if state['rounds'][-1]['status'] != 'open':
                if state['current_round'] >= 3:
                    raise ValueError('Three-round limit reached; keep this blocked item as a handoff task')
                state['current_round'] += 1
                state['rounds'].append({'number': state['current_round'], 'status': 'open', 'task_ids': []})
                event(state, 'round_opened', reason='Resume previously blocked or deferred work')
            key = self.identify(state, ident)
            task = state['tasks'].get(key)
            if task:
                if task['status'] != 'blocked':
                    raise ValueError('Task is not blocked')
                if agent:
                    self.assign_author(state, key, task, agent)
                task.update(status='active', blocked=None, round=state['current_round'])
                if key not in state['rounds'][-1]['task_ids']:
                    state['rounds'][-1]['task_ids'].append(key)
            else:
                candidate = state['candidates'][key]
                if candidate['status'] not in ('blocked', 'deferred', 'rejected'):
                    raise ValueError('Candidate is not blocked, deferred or rejected')
                candidate.update(status='queued', blocked=None)
            event(state, 'resumed', key)

    def dispose_candidate(self, ident, decision, reason):
        with self.transaction() as state:
            key = self.identify(state, ident)
            if key in state['tasks']:
                raise ValueError('Defer/reject applies to unassigned candidates; assigned work requires review or an explicit block')
            if decision not in ('deferred', 'rejected'):
                raise ValueError('Invalid candidate disposition')
            reason = nonblank(reason, 'candidate disposition reason')
            state['candidates'][key].update(status=decision, disposition={'reason': reason, 'at': now()})
            event(state, 'candidate_' + decision, key, reason=reason)

    def revise(self, ident, reason, from_stage='drafted'):
        with self.transaction() as state:
            if state['rounds'][-1]['status'] != 'open':
                state['rounds'][-1]['status'] = 'open'
                event(state, 'round_reopened_for_revision', reason=reason)
            key = self.identify(state, ident)
            task = state['tasks'].get(key)
            if not task or (task['phase'] == 'dispatched' and from_stage != 'materials'):
                raise ValueError('No completed stage exists to revise')
            reason = nonblank(reason, 'revision reason')
            previous_paper_dir = task.get('paper_dir')
            if from_stage not in ('materials', 'drafted'):
                raise ValueError('Revision starts at materials or drafted')
            if from_stage == 'materials':
                if task['priority'] == 'inbox':
                    raise ValueError('Inbox has only screening stages; promote it before registering formal materials')
                phase, keep = 'dispatched', ()
                task.update(paper_dir=None, repo_entry=None, open_source=None)
            elif task['priority'] == 'inbox':
                phase = 'screening'
                keep = ('screening',)
            else:
                # Reset to classified: draft changes, mappings and self-check must
                # be accepted again before a fresh independent review.
                if task['phase'] not in ('drafted', 'mapped', 'self_checked', 'reviewed'):
                    raise ValueError('Revision invalidation applies after drafting; repair earlier blocked stages and resume')
                phase, keep = 'classified', ('materials', 'classified')
            task['revision'] += 1
            task.update(status='active', phase=phase, review=None, blocked=None, round=state['current_round'])
            task['evidence'] = {k: v for k, v in task['evidence'].items() if k in keep}
            state['candidates'][key]['status'] = 'assigned'
            if previous_paper_dir:
                previous_review = self._journal['papers'].get(previous_paper_dir, {}).get('review')
                if previous_review:
                    previous_review['status'] = 'invalidated'
            if key not in state['rounds'][-1]['task_ids']:
                state['rounds'][-1]['task_ids'].append(key)
            event(state, 'author_revision', key, revision=task['revision'], from_stage=from_stage, reason=reason)

    def verify_terminal(self, task):
        self.artifact_paths(task)  # Reject missing reports/claimed source files.
        if task['status'] == 'blocked':
            nonblank(task.get('blocked', {}).get('reason'), 'blocking reason')
            return
        review = task.get('review')
        if task['status'] != 'done' or task['phase'] not in ('reviewed', 'screened') or not review:
            raise ValueError(f'{task["paper_id"]}: not independently reviewed or explicitly blocked')
        if review['reviewer_id'] in task['author_ids'] or review['author_revision'] != task['revision']:
            raise ValueError(f'{task["paper_id"]}: independent review is stale or not independent')
        if review['artifacts'] != self.stamps(task):
            raise ValueError(f'{task["paper_id"]}: artifacts changed after review; revise and obtain a fresh review')
        if task['phase'] == 'reviewed':
            self.check_artifacts(task, ready=True)

    def pending(self, state):
        result = []
        for key, candidate in state['candidates'].items():
            if candidate['status'] not in ('queued', 'blocked', 'deferred', 'rejected'):
                continue
            if candidate['status'] in ('deferred', 'rejected'):
                why = candidate['status'] + '_by_main'
            elif candidate['status'] == 'blocked':
                why = 'external_blocker'
            elif candidate['available_round'] > state['current_round']:
                why = 'next_round'
            elif state['used'][candidate['priority']] >= LIMITS[candidate['priority']]:
                why = 'quota_exhausted'
            else:
                why = 'dispatchable'
            result.append({'paper_id': key, 'priority': candidate['priority'], 'available_round': candidate['available_round'], 'reason': why})
        result += [{'paper_id': k, 'priority': t['priority'], 'reason': 'external_blocker', 'phase': t['phase'], 'detail': t['blocked']['reason']}
                   for k, t in state['tasks'].items() if t['status'] == 'blocked']
        return result

    def close_round(self, reason):
        with self.transaction() as state:
            current = state['rounds'][-1]
            if current['status'] != 'open':
                raise ValueError('Round already closed')
            for key in current['task_ids']:
                self.verify_terminal(state['tasks'][key])
            if any(x['reason'] == 'dispatchable' for x in self.pending(state)):
                raise ValueError('Processable current-round candidates remain; dispatch or explicitly block them')
            current.update(status='closed', closed_at=now(), reason=nonblank(reason, 'round conclusion'))
            event(state, 'round_closed', reason=reason)
            future = any(c['status'] == 'queued' and c['available_round'] <= state['current_round'] + 1 and state['used'][c['priority']] < LIMITS[c['priority']] for c in state['candidates'].values())
            if state['current_round'] < 3 and future:
                state['current_round'] += 1
                state['rounds'].append({'number': state['current_round'], 'status': 'open', 'task_ids': []})
                event(state, 'round_opened')
            return state['current_round']

    def verify_summary(self, state, value):
        path = self.file(value, 'final summary', 40)
        result = {'documents': {}, 'errors': [], 'gaps': []}
        text, tokens = reading.inspect_document(path, reading.markdown(), result)
        if result['errors']:
            raise ValueError('Final summary is empty, malformed or still contains placeholders')
        links = [child.attrGet('href') for token in tokens for child in token.children or [] if child.type == 'link_open']
        for key, task in state['tasks'].items():
            if task['phase'] != 'reviewed' or task['status'] != 'done':
                continue
            candidate = state['candidates'][key]
            if candidate['title'] not in text:
                raise ValueError(f'Final summary does not identify reviewed paper: {key}')
            paper = self.folder(task['paper_dir'], 'paper_dir')
            cited = False
            source_urls = [s['source'] for s in candidate['sources'] if urlsplit(s['source']).scheme in ('http', 'https')]
            for href in links:
                parts = urlsplit(href)
                if parts.scheme in ('http', 'https') and href in source_urls:
                    cited = True
                elif not parts.scheme and not parts.netloc and parts.path:
                    target = contained(self.project, path.parent / unquote(parts.path))
                    if target.is_file() and paper in target.parents:
                        cited = True
            if not cited:
                raise ValueError(f'Final summary needs a real paper/note source link for: {key}')
        return self.relative(path)

    def finalize(self, reason, summary='related_work/reading_guide.md'):
        with self.transaction() as state:
            reason = nonblank(reason, 'termination reason')
            if state['rounds'][-1]['status'] != 'closed':
                raise ValueError('Close the current round before finalizing')
            for task in state['tasks'].values():
                self.verify_terminal(task)
            pending = self.pending(state)
            if any(x['reason'] == 'dispatchable' for x in pending):
                raise ValueError('A processable candidate remains')
            if state['current_round'] == 3:
                condition = 'three_round_limit'
            elif not pending:
                condition = 'no_candidates'
            elif all(x['reason'] in ('quota_exhausted', 'external_blocker', 'deferred_by_main', 'rejected_by_main') for x in pending):
                condition = 'no_actionable_candidates'
            else:
                raise ValueError('An eligible later-round candidate remains; do not claim completion')
            summary = self.verify_summary(state, summary)
            if not hasattr(reading, 'check_public_artifacts'):
                raise ValueError('Update reading_artifacts.py: finalization requires its public identifier leakage check')
            public_check = reading.check_public_artifacts(self.project, self._journal,
                                                         extra_paths=(self.project / summary,))
            if public_check.get('errors'):
                raise ValueError('Private execution identifiers remain in public artifacts: ' + '; '.join(item.get('message', str(item)) for item in public_check['errors']))
            state['termination'] = {'condition': condition, 'reason': reason, 'summary': summary, 'at': now(),
                                    'pending': pending, 'exhaustive_search_claimed': False}
            state['status'] = 'finalized'
            if self._journal.get('active_run') == self.run_id:
                self._journal['active_run'] = None
            event(state, 'finalized', condition=condition, reason=reason)
            return copy.deepcopy(state['termination'])

    def status(self):
        state = self.load()
        return {'run_id': self.run_id, 'status': state['status'], 'current_round': state['current_round'],
                'round_status': state['rounds'][-1]['status'], 'limits': state['limits'], 'used': state['used'],
                'quota_basis': 'First formal dispatch tier; formal reclassification does not refund or charge again. Inbox promotion additionally consumes one formal slot.',
                'tasks': state['tasks'], 'pending': self.pending(state), 'termination': state['termination']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, required=True)
    parser.add_argument('--run')
    commands = parser.add_subparsers(dest='command', required=True)
    cmd = commands.add_parser('init'); cmd.add_argument('run_id')
    cmd = commands.add_parser('candidate', formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Register or deduplicate one candidate. Input is used only by the internal journal.',
        epilog='''JSON object fields:
  Required: paper_id (internal slug), title, priority (ultra|max|mid|inbox),
            source (public source location), need (reason this reading is needed).
  Optional: paper_type (method|survey|benchmark|dataset|theory|tool), doi, arxiv_id.
  New discovery: discovered_from = {"paper_id": internal parent reference,
                                    "location": original paper section/page}.
  After round 1, a pre-existing candidate instead requires origin = "backlog".
Use '-' for stdin or a temporary JSON file outside the project. Never persist
execution inputs or IDs in the public reading documents or their filenames.''')
    cmd.add_argument('file', type=Path, help="JSON input outside the project, or '-' for stdin")
    cmd = commands.add_parser('dispatch'); cmd.add_argument('paper'); cmd.add_argument('--agent', required=True); cmd.add_argument('--priority', choices=LIMITS)
    cmd = commands.add_parser('advance', formatter_class=argparse.RawDescriptionHelpFormatter,
        description='Accept exactly the next completed stage and embed its evidence in the internal journal.',
        epilog='''Evidence JSON fields:
  All author stages: report = {"text": actual findings} or {"file": outside file}.
  materials additionally: paper_dir (project-relative), pdf_version,
    latex_files (project-relative .tex paths), open_source (boolean).
    Available LaTeX requires latex_version equal to pdf_version. If unavailable,
    use [] plus latex_unavailable_reason and latex_search_report.
    Open-source work also requires repo_entry (project-relative associated entry).
  classified additionally: priority (ultra|max|mid|inbox).
  reviewed/screened: reviewer_id and review_report, instead of report.
    Reviewer must differ from every author; review must contain its own findings.
Reports accept report_text/report_file, review_report_text/review_report_file,
and latex_search_text/latex_search_file aliases. Files must be outside the project;
their contents are imported, so no report path is retained. Use '-' for stdin.
Stages run in order; blocked work must resume, revised work must be reviewed again.
The command records completed agent work; it never launches agents or jobs.''')
    cmd.add_argument('paper', help='Internal candidate reference')
    cmd.add_argument('stage', choices=FORMAL + SCREENING)
    cmd.add_argument('--evidence', type=Path, required=True, help="JSON input outside the project, or '-' for stdin")
    for name in ('block', 'revise'):
        cmd = commands.add_parser(name); cmd.add_argument('paper'); cmd.add_argument('--reason', required=True)
        if name == 'revise': cmd.add_argument('--from-stage', choices=('materials', 'drafted'), default='drafted')
    cmd = commands.add_parser('resume'); cmd.add_argument('paper'); cmd.add_argument('--agent')
    for name in ('defer', 'reject'):
        cmd = commands.add_parser(name); cmd.add_argument('paper'); cmd.add_argument('--reason', required=True)
    for name in ('close-round', 'finalize'):
        cmd = commands.add_parser(name); cmd.add_argument('--reason', required=True)
        if name == 'finalize': cmd.add_argument('--summary', default='related_work/reading_guide.md')
    commands.add_parser('status')
    args = parser.parse_args()
    try:
        if args.command != 'init' and not args.run:
            raise ValueError('--run is required')
        run = Run(args.project, args.run_id if args.command == 'init' else args.run)
        if args.command == 'init': result = run.init()
        elif args.command == 'candidate': result = {'paper_id': run.add_candidate(read_input_json(args.file, run.project))}
        elif args.command == 'dispatch': result = run.dispatch(args.paper, args.agent, args.priority)
        elif args.command == 'advance': result = run.advance(args.paper, args.stage, read_input_json(args.evidence, run.project))
        elif args.command == 'block': result = run.block(args.paper, args.reason)
        elif args.command == 'resume': result = run.resume(args.paper, args.agent)
        elif args.command == 'revise': result = run.revise(args.paper, args.reason, args.from_stage)
        elif args.command in ('defer', 'reject'): result = run.dispose_candidate(args.paper, 'deferred' if args.command == 'defer' else 'rejected', args.reason)
        elif args.command == 'close-round': result = {'current_round': run.close_round(args.reason)}
        elif args.command == 'finalize': result = run.finalize(args.reason, args.summary)
        else: result = run.status()
        print(json.dumps(result if result is not None else run.status(), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


def read_input_json(path: Path, project: Path):
    if str(path) == '-':
        value = json.load(sys.stdin)
        if not isinstance(value, dict):
            raise ValueError('Execution input must be a JSON object')
        return value
    source = path.expanduser().resolve()
    if source == project or project in source.parents:
        raise ValueError('Execution input JSON must be stdin or a temporary file outside the project')
    return read_json(source)


if __name__ == '__main__':
    raise SystemExit(main())
