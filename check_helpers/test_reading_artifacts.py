"""Adversarial checks for evidence gates, Unicode table rows, and local rendering."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import reading_artifacts as reading
import workflow

try:
    import pymupdf as fitz
except ImportError:
    fitz = None


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


class MarkdownTests(unittest.TestCase):
    def setUp(self):
        self.parser = reading.markdown()

    def count(self, body):
        return reading.table_rows(self.parser.parse('| Heading | Value |\n|---|---|\n' + body))[1]['characters']

    def test_unicode_boundary_and_formatting(self):
        self.assertEqual(self.count('| **中** | [' + 'a' * 159 + '](https://example.com/' + 'x' * 200 + ') |'), 160)
        self.assertEqual(self.count('| 中 | ' + 'a' * 160 + ' |'), 161)
        self.assertEqual(self.count('| A | 你好<br>世界 |'), 6)
        self.assertEqual(self.count('| &amp; | `a b` |'), 4)
        self.assertEqual(self.count('| x | 😺 |'), 2)
        self.assertEqual(self.count('| a   b | c |'), 4)
        self.assertEqual(self.count('| **a**   b | c |'), 4)
        self.assertEqual(self.count('| `a   b` | c |'), 6)

    def test_break_cannot_evade_limit(self):
        self.assertEqual(self.count('| a | ' + '甲' * 80 + '<br>' + '乙' * 80 + ' |'), 162)

    def test_escaped_pipe_is_content(self):
        self.assertEqual(self.count('| A\\|B | C |'), 4)

    def test_raw_html_cannot_hide_table_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'note.md'
            path.write_text('# Note\n<table><tr><td>' + 'x' * 170 + '</td></tr></table>')
            report = {'errors': [], 'gaps': [], 'documents': {}}
            reading.inspect_document(path, self.parser, report)
            self.assertIn('unsupported_html', [e['code'] for e in report['errors']])


class RepositoryPathTests(unittest.TestCase):
    def test_retier_updates_current_grade_without_reason_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with contextlib.redirect_stdout(io.StringIO()):
                workflow.init_project(root)
                path = workflow.add_paper(root, 'runtime-paper', 'ultra', title='Example')
                workflow.retier(root, 'runtime-paper', 'max')
            metadata = json.loads((root / 'related_work/max/example/metadata.json').read_text())
            self.assertEqual(metadata['priority'], 'max')
            self.assertNotIn('priority_history', metadata)
            self.assertTrue((root / 'related_work/reading_workflow.md').is_file())
            self.assertNotIn('待人工审核', (root / 'related_work/index.md').read_text())

    def test_nested_repo_and_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            entry = Path(tmp) / 'entry'
            repo = entry / 'repo' / 'ActualRepo'
            repo.mkdir(parents=True)
            write_json(entry / 'metadata.json', {'repo_path': 'repo/ActualRepo', 'repo_path_base': 'entry_directory'})
            self.assertEqual(workflow.resolve_repo_entry(entry)[0], repo)
            write_json(entry / 'metadata.json', {'repo_path': '../../outside'})
            with self.assertRaises(ValueError):
                workflow.resolve_repo_entry(entry)
            (entry / 'escape').symlink_to(Path(tmp))
            write_json(entry / 'metadata.json', {'repo_path': 'escape'})
            with self.assertRaises(ValueError):
                workflow.resolve_repo_entry(entry)

    def test_tree_and_branch_use_nested_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry = root / 'reproduce/baselines/example'
            repo = entry / 'repo/Nested'
            repo.mkdir(parents=True)
            (repo / 'model.py').write_text('result = 1\n')
            git = lambda *args: subprocess.run(['git', '-C', str(repo), *args], check=True, capture_output=True, text=True)
            git('init', '-q')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'add', 'model.py')
            git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')
            write_json(entry / 'metadata.json', {'repo_path': 'repo/Nested', 'repo_path_base': 'entry_directory'})
            with contextlib.redirect_stdout(io.StringIO()):
                workflow.code_tree(root, 'baselines', 'example')
                workflow.annotation_branch(root, 'baselines', 'example')
            self.assertIn('model.py', (entry / 'code_tree.md').read_text())
            self.assertEqual(git('branch', '--show-current').stdout.strip(), 'reading/annotated')
            self.assertEqual(json.loads((entry / 'metadata.json').read_text())['annotation_branch'], 'reading/annotated')
            with self.assertRaises(ValueError):
                workflow.annotation_branch(root, 'baselines', 'example')


@unittest.skipIf(fitz is None, 'PyMuPDF optional dependency unavailable')
class ReferenceTests(unittest.TestCase):
    def test_two_code_spans_at_same_paper_location_keep_both_snippets(self):
        import annotate_paper
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repo = root / 'repo'
            repo.mkdir()
            (repo / 'model.py').write_text('def evaluate():\n    return 42\n')
            doc = fitz.open()
            doc.new_page().insert_text((72, 72), 'An exact paper anchor')
            doc.save(root / 'paper.pdf')
            doc.close()
            mappings = [{'verified': True, 'color': 'BLUE', 'code_file': 'model.py',
                         'code_lines': [n, n], 'symbol': 'evaluate', 'paper_location': 'Section 3 Method',
                         'explanation': 'This code block implements the stated paper method.',
                         'anchors': [{'page': 1, 'text': 'An exact paper anchor'}]} for n in (1, 2)]
            write_json(root / 'map.json', {'paper_title': 'A Real Paper Title', 'pdf_version': 'v1',
                                         'code_version': 'verified source version', 'mappings': mappings})
            count = annotate_paper.annotate(root / 'paper.pdf', repo, root / 'map.json', root / 'annotated.pdf')
            self.assertEqual(count, 2)
            with fitz.open(root / 'annotated.pdf') as result:
                page = result[0]
                contents = [ann.info['content'] for ann in page.annots()]
            self.assertTrue(any('代码（原样摘录）：\ndef evaluate():' in text for text in contents))
            self.assertTrue(any('代码（原样摘录）：\n    return 42' in text for text in contents))


@unittest.skipIf(fitz is None, 'PyMuPDF optional dependency unavailable')
class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.paper = self.root / 'paper'
        self.paper.mkdir()
        self.entry = self.root / 'entry'
        self.repo = self.entry / 'repo/Nested'
        self.repo.mkdir(parents=True)
        self.git('init', '-q')
        self.git('config', 'user.name', 'Local test')
        self.git('config', 'user.email', 'test@example.invalid')
        for number in range(1, 6):
            (self.repo / f'm{number}.py').write_text(f'value{number} = {number}\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'fixture baseline')
        self.head = self.git('rev-parse', 'HEAD')
        write_json(self.entry / 'metadata.json', {'repo_path': 'repo/Nested', 'repo_path_base': 'entry_directory', 'commit': self.head})
        write_json(self.paper / 'metadata.json', {'title': 'Fixture Paper', 'arxiv_id': '0000.00001v1', 'related_code_paths': ['entry']})
        note = '''# Fixture

## 材料

[Paper PDF](paper.pdf)

## Highlights

> Author contribution quoted from section one with a precise source attribution.

## 一句话总结

The paper solves a stated reconstruction problem with a specific scene representation.

## 背景与动机

Existing approaches leave the paper's stated constraints unresolved, motivating the proposed representation.

## 方法详解

The method transforms observed inputs into an optimized representation through the documented stages.

## 实验与消融

The paper compares the stated baseline conditions and isolates the effect of its representation.

## 局限与启发

The reported evidence supports the tested conditions and leaves broader generalization unverified.

[First detailed question](qa.md#q1)
'''
        qa = '# Questions retained for later discussion\n\n'
        for number in range(1, 7):
            qa += f'<a id="q{number}"></a>\n\n### Q{number} What does the paper establish?\n\nAnswer: This is a substantive grounded answer referring to the source paper section.\n\nEvidence: Section {number} of the selected source paper.\n\n'
        (self.paper / 'note.md').write_text(note)
        (self.paper / 'qa.md').write_text(qa)
        (self.paper / 'translation_zh.md').write_text('# 中文译文\n' + ''.join(f'\n## {name}\n\n这是具有实质内容的译文示例，包含来自原论文的明确论述及出处说明。\n' for name in ['摘要', 'Highlights', '方法', '不足', '未来展望']))
        document = fitz.open()
        page = document.new_page()
        for number in range(1, 6):
            page.insert_text((72, 60 + 32 * number), f'Module {number} exact paper anchor')
        document.save(self.paper / 'paper.pdf')
        document.close()
        self.mapping = {'paper_title': 'Fixture Paper', 'pdf_version': '0000.00001v1', 'code_version': self.head, 'mappings': []}
        coverage = {}
        for number, (category, color) in enumerate(reading.CATEGORIES.items(), 1):
            location = f'Section {number}'
            (self.repo / f'm{number}.py').write_text(f'# 论文对应：Fixture Paper｜{location}\n# Verified semantic description of this module.\nvalue{number} = {number}\n')
            self.mapping['mappings'].append({'verified': True, 'color': color, 'paper_location': f'Section {number}', 'code_file': f'm{number}.py', 'code_lines': [3, 3], 'symbol': f'value{number}', 'explanation': '经过论文与代码共同核验的模块对应说明。', 'anchors': [{'page': 1, 'text': f'Module {number} exact paper anchor'}]})
            coverage[category] = {'status': 'covered', 'locations': [location], 'reason': 'Inspected source paper and actual implementation.'}
        write_json(self.paper / 'paper_code_annotations.json', self.mapping)
        (self.entry / 'code_map.md').write_text('# Code mapping\n\n' + '\n'.join(item['paper_location'] + ' ' + item['code_file'] + ': inspected real source evidence in the paper and implementation.' for item in self.mapping['mappings']))
        self.manifest = {'schema_version': 1, 'paper_title': 'Fixture Paper', 'status': 'complete', 'open_source': True, 'source': {'pdf': 'paper.pdf', 'version': '0000.00001v1'}, 'review': {'status': 'verified', 'reviewed_at': '2026-09-24', 'evidence': 'Fixture review only; never describes scientific content.'}, 'code': {'status': 'complete', 'mapping_file': 'paper_code_annotations.json', 'annotated_pdf': 'paper_annotated.pdf', 'coverage': coverage}, 'gaps': []}
        self.save_manifest()
        import annotate_paper
        annotate_paper.annotate(self.paper / 'paper.pdf', self.repo, self.paper / 'paper_code_annotations.json', self.paper / 'paper_annotated.pdf')

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], check=True, capture_output=True, text=True).stdout.strip()

    def save_manifest(self):
        write_json(self.paper / 'reading_manifest.json', self.manifest)

    def codes(self):
        return [item['code'] for item in reading.check(self.paper, self.entry)['errors']]

    def test_evidence_complete_then_declared_gap_is_partial(self):
        report = reading.check(self.paper, self.entry)
        self.assertEqual(report['status'], 'complete', report)
        self.manifest['gaps'] = ['The paper evaluation script is unavailable.']
        self.save_manifest()
        report = reading.check(self.paper, self.entry)
        self.assertEqual(report['status'], 'partial')
        self.assertEqual(report['structural_checks'], 'passed')
        self.assertEqual(report['gaps'][0]['code'], 'declared_gap')

    def test_annotation_branch_metadata_does_not_gate_acceptance(self):
        metadata_path = self.entry / 'metadata.json'
        metadata = json.loads(metadata_path.read_text())
        current_branch = self.git('branch', '--show-current')
        for stored_branch in ('', 'reading/annotated', 'an-old-reading-branch'):
            with self.subTest(annotation_branch=stored_branch):
                metadata['annotation_branch'] = stored_branch
                write_json(metadata_path, metadata)
                report = reading.check(self.paper, self.entry)
                self.assertEqual(report['status'], 'complete', report)
                self.assertEqual(self.git('branch', '--show-current'), current_branch)

    def test_verified_difference_finishes_reading_without_claiming_consistency(self):
        self.manifest['code']['status'] = 'differences_verified'
        self.manifest['code']['coverage']['inference_optimization']['status'] = 'difference'
        self.manifest['verified_differences'] = [{
            'description': 'The selected implementation omits the paper postprocessing step.',
            'evidence': ['../entry/code_map.md', 'paper.pdf'],
        }]
        self.save_manifest()
        report = reading.check(self.paper, self.entry)
        self.assertTrue(report['workflow_ready'], report)
        self.assertEqual(report['reading_status'], 'complete')
        self.assertEqual(report['correspondence_status'], 'differences_verified')
        self.assertEqual(report['status'], 'partial')
        self.manifest['gaps'] = ['An additional implementation detail remains unverified.']
        self.save_manifest()
        self.assertFalse(reading.check(self.paper, self.entry)['workflow_ready'])

    def test_difference_cannot_hide_missing_or_external_evidence(self):
        self.manifest['code']['status'] = 'differences_verified'
        self.save_manifest()
        self.assertIn('difference_evidence', self.codes())
        for ref in ('missing.md', '/etc/passwd', 'https://example.com'):
            with self.subTest(ref=ref):
                self.manifest['verified_differences'] = [{
                    'description': 'An implementation difference has purportedly been verified.',
                    'evidence': [ref],
                }]
                self.save_manifest()
                self.assertIn('difference_evidence', self.codes())

    def test_non_open_source_still_requires_a_real_pdf(self):
        metadata = json.loads((self.paper / 'metadata.json').read_text())
        metadata['related_code_paths'] = []
        write_json(self.paper / 'metadata.json', metadata)
        self.manifest['open_source'] = False
        self.manifest['code'] = {'status': 'not_applicable'}
        self.save_manifest()
        self.assertTrue(reading.check(self.paper)['workflow_ready'])
        (self.paper / 'paper.pdf').write_text('not a PDF')
        report = reading.check(self.paper)
        self.assertFalse(report['workflow_ready'])
        self.assertIn('source_pdf', [item['code'] for item in report['errors']])

    def test_references_and_actual_code_are_checked_not_trusted(self):
        path = self.repo / 'm1.py'
        path.write_text(path.read_text().replace('value1 = 1', 'value1 = 999'))
        codes = self.codes()
        self.assertIn('code_behavior_changed', codes)
        self.assertIn('pdf_snippet_mismatch', codes)

    def test_missing_readable_code_citation_is_rejected(self):
        path = self.repo / 'm1.py'
        path.write_text(path.read_text().replace('Fixture Paper｜Section 1', 'Generic comment'))
        self.assertIn('code_reference', self.codes())

    def test_old_runtime_metadata_and_mapping_ids_are_rejected(self):
        self.manifest['paper_id'] = 'temporary-paper'
        self.save_manifest()
        self.mapping['mappings'][0]['id'] = 'temporary-paper:M01'
        write_json(self.paper / 'paper_code_annotations.json', self.mapping)
        self.assertIn('temporary_identifier', self.codes())

    def test_comment_like_text_inside_string_cannot_change_behavior(self):
        baseline = ['value = """', '# data, not a comment', '"""']
        changed = ['value = """', '# changed data', '"""']
        self.assertFalse(reading.comment_only_changes(baseline, changed, '.py'))

    def test_out_of_range_and_escape_are_rejected(self):
        self.mapping['mappings'][0]['code_lines'] = [400, 401]
        self.mapping['mappings'][1]['code_file'] = '../../../paper/note.md'
        write_json(self.paper / 'paper_code_annotations.json', self.mapping)
        self.assertGreaterEqual(self.codes().count('mapping_evidence'), 2)

    def test_current_head_must_match_declared_version(self):
        self.mapping['code_version'] = 'a' * 40
        write_json(self.paper / 'paper_code_annotations.json', self.mapping)
        self.assertIn('code_version', self.codes())

    def test_missing_annotations_and_pdf_body_changes(self):
        target = self.paper / 'paper_annotated.pdf'
        target.unlink()
        document = fitz.open(self.paper / 'paper.pdf')
        document[0].insert_text((72, 400), 'Changed paper body')
        document.save(target)
        document.close()
        codes = self.codes()
        self.assertIn('pdf_body_changed', codes)
        self.assertIn('pdf_mapping_missing', codes)

    def test_empty_section_and_placeholder_fail(self):
        qa = self.paper / 'qa.md'
        qa.write_text(qa.read_text().replace('Answer: This is a substantive grounded answer referring to the source paper section.', 'TODO', 1))
        codes = self.codes()
        self.assertIn('six_answer', codes)
        self.assertIn('placeholder', codes)

    def test_question_table_is_navigation_not_answer(self):
        qa = self.paper / 'qa.md'
        original = qa.read_text()
        navigation = '| 编号 | 问题 | 答案 |\n|---|---|---|\n' + ''.join(f'| Q{i} | What does the paper establish? | [答案](#q{i}) |\n' for i in range(1, 7))
        qa.write_text(original + '\n' + navigation)
        self.assertEqual(self.codes(), [])
        qa.write_text('# Questions\n\n' + navigation)
        self.assertIn('six_answer', self.codes())
        self.assertIn('six_question', self.codes())

    def test_navigation_or_evidence_cannot_replace_a_question_answer(self):
        qa = self.paper / 'qa.md'
        original = qa.read_text()
        question = '### Q1 What does the paper establish?'
        for replacement in ('[Read the entire detailed answer from the paper source](paper.pdf)',
                            '#### Evidence\n\nSection one contains a long source statement without any answer here.'):
            with self.subTest(replacement=replacement):
                qa.write_text(original[:original.index(question)] + question + '\n\n' + replacement + '\n\n' + original[original.index('### Q2'):])
                self.assertIn('six_answer', self.codes())

    def test_each_answer_needs_evidence(self):
        qa = self.paper / 'qa.md'
        qa.write_text(qa.read_text().replace('Evidence: Section 1 of the selected source paper.', '', 1))
        self.assertIn('six_evidence', self.codes())

    def test_inline_and_paragraph_end_evidence_preserve_real_answers(self):
        qa = self.paper / 'qa.md'
        original = qa.read_text()
        start, end = original.index('### Q1'), original.index('### Q2')
        for evidence in ('[依据：§3.3，式（2）和表1]', '【证据来源：§4.2，表2】',
                         '[Evidence: Section 3.3 and Figure 2]', '[依据：§3.3，式（2）](paper.pdf#page=3)'):
            with self.subTest(evidence=evidence):
                question = '### Q1 What does the paper establish?\n\n'
                answer = 'The paper explains the tested reconstruction setting ' + evidence + ' and establishes a result limited to its reported conditions.\n\n'
                qa.write_text(original[:start] + question + answer + original[end:])
                self.assertEqual(self.codes(), [])
                qa.write_text(original[:start] + question + 'The paper establishes a result limited to the reconstruction conditions that were actually tested. ' + evidence + '\n\n' + original[end:])
                self.assertEqual(self.codes(), [])
                qa.write_text(original[:start] + question + evidence + '\n\n' + original[end:])
                self.assertIn('six_answer', self.codes())
                self.assertNotIn('six_evidence', self.codes())

    def test_evidence_table_header_does_not_stop_following_answer(self):
        qa = self.paper / 'qa.md'
        original = qa.read_text()
        start, end = original.index('### Q5'), original.index('### Q6')
        heading = '### Q5 What evidence supports the reported result?\n\n'
        table = '| 证据 | 结果 |\n|---|---|\n| Synthetic comparison | The reported orientation error decreases. |\n\n'
        answer = 'The comparison supports the tested orientation setting, while the ablation distinguishes how the optimization and representation affect the observed result. [依据：§4.3，图8与表2]\n\n'
        qa.write_text(original[:start] + heading + table + answer + original[end:])
        self.assertEqual(self.codes(), [])
        qa.write_text(original[:start] + heading + table + '[依据：§4.3，图8与表2]\n\n' + original[end:])
        self.assertIn('six_answer', self.codes())

    def test_nested_questions_cannot_borrow_the_next_answer(self):
        qa = self.paper / 'qa.md'
        original = qa.read_text()
        first, second = original.index('### Q1'), original.index('### Q2')
        qa.write_text(original[:first] + '### Q1 What does the paper establish?\n\n' + original[second:].replace('### Q', '#### Q'))
        self.assertIn('six_answer', self.codes())

    def test_code_and_image_alts_do_not_replace_prose_answer(self):
        qa = self.paper / 'qa.md'
        original = qa.read_text()
        start, end = original.index('### Q1'), original.index('### Q2')
        for content in ('```text\nA long code block with no prose answer explaining the paper.\n```',
                        '![A long image description that must not replace a written answer.](figure.png)'):
            qa.write_text(original[:start] + '### Q1 What does the paper establish?\n\n' + content + '\n\nEvidence: Section 1 of the selected paper.\n\n' + original[end:])
            self.assertIn('six_answer', self.codes())

    def test_note_needs_detailed_sections_and_prescribed_order(self):
        path = self.paper / 'note.md'
        original = path.read_text()
        start, end = original.index('## 方法详解'), original.index('## 实验与消融')
        path.write_text(original[:start] + '## 方法详解\n\n### Only a long subsection title with no actual method explanation\n\n' + original[end:])
        self.assertIn('note_section', self.codes())
        # Exchange entire sections, rather than only changing their labels twice.
        background = original[original.index('## 背景与动机'):start]
        method = original[start:end]
        path.write_text(original[:original.index('## 背景与动机')] + method + background + original[end:])
        self.assertIn('note_section_order', self.codes())
        path.write_text('# Note\n\n## Highlights\n\nAuthor contribution quoted with clear scientific meaning and source scope.\n')
        self.assertIn('note_section', self.codes())

    def test_old_note_question_links_require_migration(self):
        qa = self.paper / 'qa.md'
        qa.write_text(qa.read_text() + '\n[Obsolete answer](./note.md#q1)\n')
        self.assertIn('old_question_link', self.codes())

    def test_render_is_offline_and_rewrites_qa_links(self):
        qa = self.paper / 'qa.md'
        qa.write_text(qa.read_text() + '\n[Second answer](#q2)\n')
        note = self.paper / 'note.md'
        note.write_text(note.read_text() + '\n```tex\n\\Sigma = R S S^T R^T\n```\n')
        target, report = reading.render(self.paper, self.entry)
        self.assertEqual(report['status'], 'complete', report)
        page = target.read_text()
        self.assertIn('href="#qa-q1"', page)
        self.assertIn('id="qa-q1"', page)
        self.assertIn('href="#qa-q2"', page)
        self.assertNotIn('id="note-q1"', page)
        self.assertIn('\\Sigma = R S S^T R^T', page)
        self.assertNotIn('<script', page)
        self.assertIn('border-top:2px', page)
        self.assertIn('border-bottom:2px', page)
        self.assertIn('thead{border-bottom:1px', page)
        self.assertNotIn('file://', page)
        self.assertIn('href="note.md"', page)
        self.assertNotIn('验收状态', page)
        self.assertNotIn(reading.NOTICE, page)
        diagnostic, _ = reading.render(self.paper, self.entry, self.paper / 'diagnostic.html', diagnostic=True)
        self.assertIn('验收状态', diagnostic.read_text())
        before = (self.paper / 'note.md').read_text()
        reading.render(self.paper, self.entry)
        self.assertEqual((self.paper / 'note.md').read_text(), before)
        target.write_text('<html>Personal reading page</html>')
        with self.assertRaises(ValueError):
            reading.render(self.paper, self.entry)


if __name__ == '__main__':
    unittest.main()
