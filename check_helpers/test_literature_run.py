"""The ledger must enforce ordering/quotas using /tmp artifacts, not real projects."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from literature_run import Run, read_input_json
import execution_state
import reading_artifacts as reading

try:
    import pymupdf as fitz
except ImportError:
    fitz = None


def json_file(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


class FixtureSupport:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='literature-run-', dir='/tmp')
        self.root = Path(self.temp.name)
        self.run = Run(self.root, 'test-run')
        self.run.init()

    def tearDown(self):
        self.temp.cleanup()

    def candidate(self, key='paper-one', priority='ultra', **fields):
        data = {'paper_id': key, 'title': self.title(key), 'priority': priority,
                'paper_type': 'method', 'source': 'https://example.invalid/' + self.public_slug(key),
                'need': 'Read the cited method to verify the research comparison.', **fields}
        return self.run.add_candidate(data)

    def public_slug(self, key='paper-one'):
        return 'study-' + key.removeprefix('paper-')

    def title(self, key='paper-one'):
        return self.public_slug(key).replace('-', ' ').title()

    def paper_path(self, key='paper-one'):
        return self.root / 'related_work' / 'ultra' / self.public_slug(key)

    def report(self, key, stage, text=None):
        return {'text': text or f'# {key} — {stage}\n\nInspected actual paper evidence and recorded the findings for this stage.\n'}

    def screening(self, key='paper-one', reviewer='reviewer-one'):
        self.run.advance(key, 'screening', {'report': self.report(key, 'screening')})
        self.run.advance(key, 'screened', {'reviewer_id': reviewer, 'review_report': self.report(key, 'screened')})

    def summary(self, papers=()):
        path = self.root / 'related_work/reading_guide.md'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('# Literature summary\n\nThis run records the actual reviewed literature and explicitly preserves unprocessed follow-up work.\n\n' + '\n'.join(f'- {self.title(key)}: [source](ultra/{self.public_slug(key)}/note.md)' for key in papers))
        return path


class LedgerTests(FixtureSupport, unittest.TestCase):
    def test_single_journal_preserves_completed_runs_and_embedded_history(self):
        self.candidate(priority='inbox')
        self.run.dispatch('paper-one', 'screening-author')
        self.screening()
        self.run.close_round('All selected screening is independently reviewed')
        self.summary()
        self.run.finalize('The present candidate set has been screened')
        Run(self.root, 'subsequent-run').init()
        state = execution_state.load(self.root)
        self.assertEqual(set(state['runs']), {'test-run', 'subsequent-run'})
        self.assertEqual(state['active_run'], 'subsequent-run')
        previous = state['runs']['test-run']
        self.assertEqual(previous['status'], 'finalized')
        self.assertIn('Inspected actual paper evidence', previous['tasks']['paper-one']['evidence']['screening']['report']['content'])
        self.assertIn('content', next(e for e in previous['events'] if e['action'] == 'stage_completed')['evidence']['report'])
        self.assertEqual([p.name for p in (self.root / '.workflow').iterdir()], ['execution_state.json'])
        self.assertFalse((self.root / '.workflow/literature_runs').exists())

    def test_legacy_run_files_cannot_silently_reset_or_bypass_quotas(self):
        legacy = self.root / '.workflow/literature_runs/earlier-execution/state.json'
        json_file(legacy, {'current_round': 2, 'used': {'ultra': 40}, 'charges': ['preserve-me']})
        before = self.run.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'preserving rounds and quota charges'):
            Run(self.root, 'fresh-run').init()
        with self.assertRaisesRegex(ValueError, 'Legacy literature run files'):
            self.run.load()
        with self.assertRaisesRegex(ValueError, 'Legacy literature run files'):
            self.candidate()
        self.assertEqual(self.run.path.read_bytes(), before)

    def test_external_reports_are_embedded_without_retaining_input_paths(self):
        self.candidate(priority='inbox'); self.run.dispatch('paper-one', 'author')
        with tempfile.TemporaryDirectory(prefix='execution-input-', dir='/tmp') as directory:
            source = Path(directory) / 'temporary-report.md'
            source.write_text('The author inspected the screening evidence and documented its actual relevance.')
            self.run.advance('paper-one', 'screening', {'report_file': str(source)})
            source.unlink()
            self.assertNotIn(directory, self.run.path.read_text())
        self.run.advance('paper-one', 'screened', {'reviewer_id': 'independent', 'review_report_text': 'The independent reviewer checked the original screening evidence and confirmed the limited conclusions.'})
        self.run.close_round('Imported reports remain available after external inputs are removed')
        self.assertEqual(self.run.load()['tasks']['paper-one']['status'], 'done')

    def test_project_local_inputs_rejected_and_stdin_cli_supported(self):
        source = self.root / 'input.json'
        source.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'outside the project'):
            read_input_json(source, self.root)
        self.candidate(priority='inbox'); self.run.dispatch('paper-one', 'author')
        source.write_text('This report must not persist in a separate file inside the project.')
        before = self.run.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'outside the project'):
            self.run.advance('paper-one', 'screening', {'report_file': str(source)})
        self.assertEqual(self.run.path.read_bytes(), before)
        source.unlink()
        script = Path(__file__).resolve().parents[1] / 'scripts/literature_run.py'
        data = {'report_text': 'The actual screening evidence has been inspected and its findings are recorded inline.'}
        process = subprocess.run([sys.executable, str(script), '--project', str(self.root), '--run', 'test-run', 'advance', 'paper-one', 'screening', '--evidence', '-'], input=json.dumps(data), capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)['phase'], 'screening')
        for command, expected in [('candidate', 'discovered_from'), ('advance', 'review_report_text')]:
            help_result = subprocess.run([sys.executable, str(script), '--project', str(self.root), command, '--help'], capture_output=True, text=True)
            self.assertEqual(help_result.returncode, 0)
            self.assertIn(expected, help_result.stdout)

    def test_shared_transaction_preserves_other_records_and_rolls_back(self):
        execution_state.register_identity(self.root, 'repositories', 'private-repo', 'reproduce/baselines/public-name', 'Public Name')
        with execution_state.transaction(self.root) as journal:
            journal['identities']['repositories']['private-repo']['pending_paper_ids'] = ['paper-one']
            journal['papers']['related_work/ultra/public-paper'] = {'review': {'status': 'pending'}}
        execution_state.register_identity(self.root, 'repositories', 'private-repo', 'reproduce/baselines/public-name', 'Public Name')
        self.assertEqual(execution_state.resolve_identity(self.root, 'repositories', 'private-repo')['pending_paper_ids'], ['paper-one'])
        before = self.run.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'deliberate abort'):
            with execution_state.transaction(self.root) as journal:
                journal['papers'].clear()
                raise ValueError('deliberate abort')
        self.assertEqual(self.run.path.read_bytes(), before)
        self.assertFalse((self.run.directory / 'execution_state.lock').exists())
        self.assertFalse((self.run.directory / 'execution_state.json.part').exists())

    def test_global_quota_is_strict_and_rollback_is_atomic(self):
        for index in range(41):
            key = f'paper-{index}'
            self.candidate(key)
            if index < 40:
                self.run.dispatch(key, f'agent-{index}')
            else:
                with self.assertRaisesRegex(ValueError, 'Quota exhausted'):
                    self.run.dispatch(key, f'agent-{index}')
        state = self.run.load()
        self.assertEqual(state['used'], {'ultra': 40, 'max': 0, 'mid': 0, 'inbox': 0})
        self.assertNotIn('paper-40', state['tasks'])
        self.assertEqual(len(state['charges']), 40)

    def test_arxiv_versions_doi_and_paper_ids_are_deduplicated(self):
        self.candidate(arxiv_id='2409.14778v1', doi='https://doi.org/10.1234/AbC')
        self.assertEqual(self.candidate('another-name', 'max', arxiv_id='https://arxiv.org/abs/2409.14778v2'), 'paper-one')
        self.assertEqual(self.candidate('third-name', doi='10.1234/abc'), 'paper-one')
        self.assertEqual(self.candidate(), 'paper-one')
        state = self.run.load()
        self.assertEqual(len(state['candidates']), 1)
        self.assertEqual(state['candidates']['paper-one']['aliases'], ['another-name', 'third-name'])
        self.assertEqual(sum(state['used'].values()), 0)

    def test_one_author_per_paper_and_order_are_enforced(self):
        self.candidate(); self.candidate('paper-two')
        self.run.dispatch('paper-one', 'author')
        with self.assertRaisesRegex(ValueError, 'One paper per author'):
            self.run.dispatch('paper-two', 'author')
        with self.assertRaisesRegex(ValueError, 'Expected stage materials'):
            self.run.advance('paper-one', 'reviewed', {'reviewer_id': 'reviewer', 'review_report': self.report('paper-one', 'reviewed')})
        self.assertEqual(self.run.load()['used']['ultra'], 1)

    def test_inbox_promotion_charges_formal_once_and_cannot_expand(self):
        self.candidate(priority='inbox', paper_type='survey')
        self.run.dispatch('paper-one', 'screening-author')
        self.screening()
        with self.assertRaisesRegex(ValueError, 'inbox cannot expand'):
            self.candidate('child-paper', discovered_from={'paper_id': 'paper-one', 'location': 'References, item 3'})
        self.run.dispatch('paper-one', 'formal-author', 'max')
        state = self.run.load()
        self.assertEqual(state['used'], {'ultra': 0, 'max': 1, 'mid': 0, 'inbox': 1})
        self.run.block('paper-one', 'Temporary source availability issue')
        self.run.resume('paper-one', 'retry-author')
        self.assertEqual(self.run.load()['used'], state['used'])
        self.assertEqual(self.run.load()['tasks']['paper-one']['phase'], 'dispatched')

    def test_blocked_task_can_resume_after_round_close_without_new_charge(self):
        self.candidate(); self.run.dispatch('paper-one', 'author')
        self.run.block('paper-one', 'PDF unavailable; preserve this as an explicit pending item')
        self.run.close_round('No currently processable candidates')
        self.run.resume('paper-one')
        state = self.run.load()
        self.assertEqual(state['current_round'], 2)
        self.assertEqual(state['tasks']['paper-one']['phase'], 'dispatched')
        self.assertEqual(state['used']['ultra'], 1)

    def test_author_cannot_supply_independent_review(self):
        self.candidate(priority='inbox'); self.run.dispatch('paper-one', 'author')
        evidence = {'report': self.report('paper-one', 'screening')}
        self.run.advance('paper-one', 'screening', evidence)
        with self.assertRaisesRegex(ValueError, 'Independent reviewer'):
            self.run.advance('paper-one', 'screened', {'reviewer_id': 'author', 'review_report': self.report('paper-one', 'reviewed')})
        with self.assertRaisesRegex(ValueError, 'own report'):
            self.run.advance('paper-one', 'screened', {'reviewer_id': 'reviewer', 'review_report': evidence['report']})

    def test_review_is_invalidated_by_revision_or_changed_files(self):
        self.candidate(priority='inbox'); self.run.dispatch('paper-one', 'author'); self.screening()
        self.run.revise('paper-one', 'Update author conclusions and request independent review again')
        task = self.run.load()['tasks']['paper-one']
        self.assertIsNone(task['review'])
        self.assertEqual(task['phase'], 'screening')
        self.assertEqual(task['revision'], 2)
        self.assertEqual(self.run.load()['used']['inbox'], 1)

    def test_defer_and_reject_are_explicit_not_fake_external_blocks(self):
        self.candidate(); self.candidate('paper-two')
        self.run.dispose_candidate('paper-one', 'deferred', 'Useful later but not necessary for the present comparison')
        self.run.dispose_candidate('paper-two', 'rejected', 'Outside the current problem scope')
        self.run.close_round('No necessary candidate remains')
        with self.assertRaisesRegex(ValueError, 'final summary'):
            self.run.finalize('The present selection is exhausted, not the literature')
        self.summary()
        termination = self.run.finalize('Only deliberately deferred or rejected candidates remain')
        self.assertEqual({x['reason'] for x in termination['pending']}, {'deferred_by_main', 'rejected_by_main'})
        self.assertEqual(sum(self.run.load()['used'].values()), 0)
        self.assertFalse(termination['exhaustive_search_claimed'])

    def test_missing_materials_can_be_blocked_and_handed_off(self):
        self.candidate(); self.run.dispatch('paper-one', 'author')
        self.run.block('paper-one', 'No accessible PDF or usable source; cannot claim reading')
        self.run.close_round('All remaining work is blocked')
        self.summary()
        result = self.run.finalize('External PDF access blocks this work')
        self.assertEqual(result['pending'][0]['paper_id'], 'paper-one')
        self.assertEqual(self.run.load()['used']['ultra'], 1)

    def test_lock_and_path_escape_are_rejected(self):
        (self.run.directory / 'execution_state.lock').write_text('another-writer')
        with self.assertRaisesRegex(ValueError, 'Another writer'):
            self.candidate()
        (self.run.directory / 'execution_state.lock').unlink()
        with self.assertRaises(ValueError):
            self.run.file('../outside-project')
        with self.assertRaises(ValueError):
            Run(self.root, '../run')

    def test_status_cli_is_single_json_and_read_only(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/literature_run.py'
        before = self.run.path.read_bytes()
        process = subprocess.run([sys.executable, str(script), '--project', str(self.root), '--run', 'test-run', 'status'], capture_output=True, text=True)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(json.loads(process.stdout)['current_round'], 1)
        self.assertEqual(before, self.run.path.read_bytes())


@unittest.skipIf(fitz is None, 'Optional PyMuPDF unavailable')
class FormalStageTests(FixtureSupport, unittest.TestCase):
    def material(self, key='paper-one', no_latex=False):
        paper = self.paper_path(key)
        paper.mkdir(parents=True, exist_ok=True)
        json_file(paper / 'metadata.json', {'entry_kind': 'paper', 'priority': 'ultra', 'title': self.title(key), 'arxiv_id': '0000.00001v1'})
        document = fitz.open(); page = document.new_page()
        page.insert_text((72, 72), 'Source paper document for integration fixtures.')
        document.save(paper / 'paper.pdf'); document.close()
        (paper / 'main.tex').write_text('\\documentclass{article}\n\\begin{document}Real source fixture\\end{document}\n')
        value = {'paper_dir': paper.relative_to(self.root).as_posix(), 'pdf_version': '0000.00001v1', 'latex_version': '0000.00001v1', 'latex_files': [(paper / 'main.tex').relative_to(self.root).as_posix()], 'open_source': False, 'report': self.report(key, 'materials')}
        if no_latex:
            value.update(latex_files=[], latex_unavailable_reason='Official source archive unavailable after source checks', latex_search_report=self.report(key, 'latex-search'))
            del value['latex_version']
        return value

    def documents(self, key='paper-one'):
        paper = self.paper_path(key)
        note = '# ' + self.title(key) + '\n\n## 材料\n\n[Paper PDF](paper.pdf)\n\n## Highlights\n\nAuthor contribution extracted faithfully with its actual source attribution.\n'
        note += '\n'.join(f'\n## {name}\n\nThis section explains actual paper content, its supporting evidence, and the boundaries of the stated conclusions.\n' for name in ('一句话总结', '背景与动机', '方法详解', '实验与消融', '局限与启发'))
        qa = '# Questions retained for ongoing discussion\n'
        for n in range(1, 7):
            qa += f'\n### Q{n} What does this evidence establish?\n\nThis answer explains the paper evidence and its precise scope and limitations.\n\nEvidence: Source paper section {n}.\n'
        (paper / 'note.md').write_text(note)
        (paper / 'qa.md').write_text(qa)
        (paper / 'translation_zh.md').write_text('# Translation\n' + ''.join(f'\n## {section}\n\n这是来自原文的实际章节翻译测试材料，保留论述范围，并说明来源和总结边界。\n' for section in ['摘要', 'Highlights', '方法', '不足', '未来展望']))
        json_file(paper / 'reading_manifest.json', {'schema_version': 1, 'paper_title': self.title(key), 'status': 'complete', 'open_source': False, 'source': {'pdf': 'paper.pdf', 'version': '0000.00001v1'}, 'review': {'status': 'verified', 'reviewed_at': '2026-09-24', 'evidence': 'Read actual source and checked these fixture artifacts.'}, 'code': {'status': 'not_applicable'}, 'gaps': [], 'verified_differences': []})

    def classify(self, key='paper-one', priority='ultra', no_latex=False):
        self.candidate(key, priority)
        self.run.dispatch(key, 'author-' + key)
        self.run.advance(key, 'materials', self.material(key, no_latex))
        self.run.advance(key, 'classified', {'priority': priority, 'report': self.report(key, 'classified')})

    def complete(self, key='paper-one'):
        self.documents(key)
        for stage in ('drafted', 'mapped', 'self_checked'):
            self.run.advance(key, stage, {'report': self.report(key, stage)})
        reading.render(self.paper_path(key))
        self.run.advance(key, 'reviewed', {'reviewer_id': 'independent-' + key, 'review_report': self.report(key, 'reviewed')})

    def test_real_formal_workflow_and_summary_requirement(self):
        self.classify(); self.complete()
        self.run.close_round('The reviewed paper closes this selection')
        self.summary()
        with self.assertRaisesRegex(ValueError, 'does not identify'):
            self.run.finalize('No candidate remains')
        self.summary(['paper-one'])
        result = self.run.finalize('The present candidate set is processed, without claiming exhaustive search')
        self.assertEqual(result['condition'], 'no_candidates')
        self.assertEqual(self.run.load()['tasks']['paper-one']['review']['author_revision'], 1)

    def test_finalization_rejects_execution_id_leak_outside_single_journal(self):
        self.classify(); self.complete()
        self.run.close_round('The selected paper has been independently reviewed')
        summary = self.summary(['paper-one'])
        original = summary.read_text()
        summary.write_text(original + '\nInternal screening reference: paper-one.\n')
        with self.assertRaisesRegex(ValueError, 'Private execution identifiers'):
            self.run.finalize('The selected work is ready except for the deliberately leaked reference')
        self.assertEqual(self.run.load()['status'], 'active')
        summary.write_text(original)
        self.run.finalize('The reviewed source and human-readable summary are ready')
        journal = execution_state.load(self.root)
        self.assertEqual(journal['papers']['related_work/ultra/study-one']['review']['reviewer_id'], 'independent-paper-one')
        self.assertNotIn('paper_id', json.loads((self.paper_path() / 'metadata.json').read_text()))

    def test_custom_final_summary_is_included_in_identifier_check(self):
        self.classify(); self.complete()
        self.run.close_round('The actual reading and independent review are complete')
        custom = self.root / 'deliverables/final-summary.md'
        custom.parent.mkdir()
        text = '# Actual reading summary\n\nStudy One is reviewed with explicit evidence and a documented reading scope.\n\n[Reading source](../related_work/ultra/study-one/note.md)\n'
        custom.write_text(text + '\nPrivate run reference: test-run.\n')
        with self.assertRaisesRegex(ValueError, 'Private execution identifiers'):
            self.run.finalize('The actual reading is complete', 'deliverables/final-summary.md')
        custom.write_text(text)
        self.run.finalize('The checked summary contains public provenance', 'deliverables/final-summary.md')

    def test_unavailable_latex_is_explicit_but_wrong_available_version_fails(self):
        self.classify(no_latex=True)
        self.assertEqual(self.run.load()['tasks']['paper-one']['phase'], 'classified')
        self.candidate('paper-two'); self.run.dispatch('paper-two', 'author-two')
        evidence = self.material('paper-two'); evidence['latex_version'] = '0000.00001v2'
        with self.assertRaisesRegex(ValueError, 'versions must match'):
            self.run.advance('paper-two', 'materials', evidence)
        evidence = self.material('paper-three', True)
        del evidence['latex_search_report']
        self.candidate('paper-three'); self.run.dispatch('paper-three', 'author-three')
        with self.assertRaisesRegex(ValueError, 'latex_search_report'):
            self.run.advance('paper-three', 'materials', evidence)

    def test_materials_require_actual_public_repository_association(self):
        self.candidate(); self.run.dispatch('paper-one', 'author')
        evidence = self.material()
        entry = self.root / 'reproduce/baselines/public-code'
        repo = entry / 'repo'
        repo.mkdir(parents=True)
        (repo / 'implementation.py').write_text('def public_method():\n    return 1\n')
        for arguments in [('init', '-q'), ('add', 'implementation.py'),
                          ('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-qm', 'source fixture')]:
            subprocess.run(['git', '-C', str(repo), *arguments], check=True, capture_output=True)
        commit = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
        json_file(entry / 'metadata.json', {'name': 'Public Code', 'repo_path': 'repo', 'repo_path_base': 'entry_directory', 'repo_url': 'https://example.invalid/official.git', 'commit': commit})
        evidence.update(open_source=True, repo_entry=entry.relative_to(self.root).as_posix())
        with self.assertRaisesRegex(ValueError, 'associated'):
            self.run.advance('paper-one', 'materials', evidence)
        metadata = json.loads((self.paper_path() / 'metadata.json').read_text())
        metadata['related_code_paths'] = [evidence['repo_entry']]
        json_file(self.paper_path() / 'metadata.json', metadata)
        self.run.advance('paper-one', 'materials', evidence)
        self.assertEqual(self.run.load()['tasks']['paper-one']['evidence']['materials']['repo_commit'], commit)

    def test_known_code_path_cannot_be_registered_as_closed_source(self):
        self.candidate(); self.run.dispatch('paper-one', 'author')
        evidence = self.material()
        metadata = json.loads((self.paper_path() / 'metadata.json').read_text())
        metadata['related_code_paths'] = ['reproduce/baselines/public-code']
        json_file(self.paper_path() / 'metadata.json', metadata)
        with self.assertRaisesRegex(ValueError, 'Known repository'):
            self.run.advance('paper-one', 'materials', evidence)

    def test_formal_reclassification_keeps_first_charge(self):
        self.candidate(); self.run.dispatch('paper-one', 'author')
        self.run.advance('paper-one', 'materials', self.material())
        self.run.advance('paper-one', 'classified', {'priority': 'mid', 'report': self.report('paper-one', 'classified')})
        state = self.run.load()
        self.assertEqual(state['used'], {'ultra': 1, 'max': 0, 'mid': 0, 'inbox': 0})
        self.assertEqual(state['tasks']['paper-one']['initial_formal_priority'], 'ultra')
        self.assertEqual(state['tasks']['paper-one']['priority'], 'mid')

    def test_three_rounds_new_discovery_waits_and_remaining_work_is_preserved(self):
        self.classify('paper-one'); self.complete('paper-one')
        parent = 'paper-one'
        for n in (2, 3, 4):
            key = f'paper-{n}'
            self.candidate(key, discovered_from={'paper_id': parent, 'location': 'Section 2, related-method reference'})
            with self.assertRaisesRegex(ValueError, 'next round'):
                self.run.dispatch(key, 'author-' + key)
            self.run.close_round('Main agent combined and deduplicated the discovered candidates')
            if n < 4:
                self.run.dispatch(key, 'author-' + key)
                self.run.advance(key, 'materials', self.material(key))
                self.run.advance(key, 'classified', {'priority': 'ultra', 'report': self.report(key, 'classified')})
                self.complete(key)
            parent = key
        self.summary(['paper-one', 'paper-2', 'paper-3'])
        result = self.run.finalize('Completed the third round; record the next necessary reading as a handoff')
        self.assertEqual(result['condition'], 'three_round_limit')
        self.assertEqual(result['pending'][0]['paper_id'], 'paper-4')
        self.assertEqual(self.run.load()['used']['ultra'], 3)

    def test_mutated_artifact_and_unresolved_reading_gap_prevent_review(self):
        self.classify(); self.documents()
        for stage in ('drafted', 'mapped', 'self_checked'):
            self.run.advance('paper-one', stage, {'report': self.report('paper-one', stage)})
        manifest = self.paper_path() / 'reading_manifest.json'
        data = json.loads(manifest.read_text()); data['gaps'] = ['Unverified source claim']
        json_file(manifest, data)
        with self.assertRaisesRegex(ValueError, 'not workflow_ready'):
            self.run.advance('paper-one', 'reviewed', {'reviewer_id': 'independent', 'review_report': self.report('paper-one', 'reviewed')})
        data['gaps'] = []; json_file(manifest, data)
        reading.render(self.paper_path())
        self.run.advance('paper-one', 'reviewed', {'reviewer_id': 'independent', 'review_report': self.report('paper-one', 'reviewed')})
        note = self.paper_path() / 'note.md'
        note.write_text(note.read_text() + '\nChanged after independent verification.\n')
        with self.assertRaisesRegex(ValueError, 'artifacts changed'):
            self.run.close_round('Must fail because review is stale')
        self.run.revise('paper-one', 'Author revises the final note after review')
        self.assertEqual(self.run.load()['tasks']['paper-one']['phase'], 'classified')
        self.assertIsNone(self.run.load()['tasks']['paper-one']['review'])

    def test_missing_or_stale_html_prevents_independent_acceptance(self):
        self.classify(); self.documents()
        for stage in ('drafted', 'mapped', 'self_checked'):
            self.run.advance('paper-one', stage, {'report': self.report('paper-one', stage)})
        review = {'reviewer_id': 'independent', 'review_report': self.report('paper-one', 'reviewed')}
        with self.assertRaisesRegex(ValueError, 'HTML reading copy'):
            self.run.advance('paper-one', 'reviewed', review)
        paper = self.paper_path()
        reading.render(paper)
        html = paper / 'reading.html'
        older = (paper / 'note.md').stat().st_mtime_ns - 10_000_000
        os.utime(html, ns=(older, older))
        with self.assertRaisesRegex(ValueError, 'reading.html is stale'):
            self.run.advance('paper-one', 'reviewed', review)
        reading.render(paper)
        self.run.advance('paper-one', 'reviewed', review)
        html.write_text(html.read_text() + '\n<!-- Changed after review -->\n')
        with self.assertRaisesRegex(ValueError, 'artifacts changed'):
            self.run.close_round('Must reject a substituted HTML artifact')

    def test_changed_source_requires_explicit_material_reset_without_new_charge(self):
        self.classify(); self.documents()
        paper = self.paper_path()
        metadata = json.loads((paper / 'metadata.json').read_text())
        metadata['arxiv_id'] = '0000.00001v2'
        json_file(paper / 'metadata.json', metadata)
        manifest = json.loads((paper / 'reading_manifest.json').read_text())
        manifest['source']['version'] = '0000.00001v2'
        json_file(paper / 'reading_manifest.json', manifest)
        self.run.advance('paper-one', 'drafted', {'report': self.report('paper-one', 'drafted')})
        with self.assertRaisesRegex(ValueError, 'version changed after materials'):
            self.run.advance('paper-one', 'mapped', {'report': self.report('paper-one', 'mapped')})
        before = self.run.load()['used']
        self.run.revise('paper-one', 'Correct the selected source version explicitly', from_stage='materials')
        task = self.run.load()['tasks']['paper-one']
        self.assertEqual(task['phase'], 'dispatched')
        self.assertEqual(task['evidence'], {})
        evidence = {'paper_dir': self.paper_path().relative_to(self.root).as_posix(), 'pdf_version': '0000.00001v2', 'latex_version': '0000.00001v2', 'latex_files': [(self.paper_path() / 'main.tex').relative_to(self.root).as_posix()], 'open_source': False, 'report': self.report('paper-one', 'materials-corrected')}
        self.run.advance('paper-one', 'materials', evidence)
        self.run.advance('paper-one', 'classified', {'priority': 'ultra', 'report': self.report('paper-one', 'classified')})
        for stage in ('drafted', 'mapped', 'self_checked'):
            self.run.advance('paper-one', stage, {'report': self.report('paper-one', stage)})
        reading.render(paper)
        self.run.advance('paper-one', 'reviewed', {'reviewer_id': 'independent', 'review_report': self.report('paper-one', 'reviewed')})
        self.assertEqual(self.run.load()['used'], before)
        self.assertEqual(self.run.load()['tasks']['paper-one']['review']['author_revision'], 2)


if __name__ == '__main__':
    unittest.main()
