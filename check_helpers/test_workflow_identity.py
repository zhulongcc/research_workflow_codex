"""Public names, private aliases, and path continuity; all projects stay in /tmp."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import execution_state
import fetch_paper
import workflow as wf


class IdentityProject(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='workflow-identity-')
        self.root = Path(self.temporary.name)
        self.stdout = contextlib.redirect_stdout(io.StringIO())
        self.stdout.__enter__()
        wf.init_project(self.root)

    def tearDown(self):
        self.stdout.__exit__(None, None, None)
        self.temporary.cleanup()

    def paper(self, ident='runtime-paper-9327', title='Strand Aligned Hair Reconstruction', **kwargs):
        return wf.add_paper(self.root, ident, title=title, **kwargs)

    def assert_private(self, *values):
        journal = self.root / '.workflow/execution_state.json'
        for path in self.root.rglob('*'):
            if path == journal:
                continue
            for value in values:
                self.assertNotIn(value, path.relative_to(self.root).as_posix())
                if path.is_file():
                    self.assertNotIn(value, path.read_text(encoding='utf-8'))


class IdentityTests(IdentityProject):
    def test_paper_uses_title_everywhere_and_alias_only_in_journal(self):
        paper = self.paper()
        self.assertEqual(paper.name, 'strand-aligned-hair-reconstruction')
        metadata = wf.read_json(paper / 'metadata.json')
        self.assertNotIn('paper_id', metadata)
        self.assertNotIn('related_repo_ids', metadata)
        self.assertEqual(metadata['related_code_paths'], [])
        self.assertEqual(wf.find_paper(self.root, 'runtime-paper-9327')[0], paper)
        self.assertEqual(wf.find_paper(self.root, metadata['title'])[0], paper)
        self.assertEqual(wf.find_paper(self.root, paper.relative_to(self.root).as_posix())[0], paper)
        self.assert_private('runtime-paper-9327')

    def test_missing_title_does_not_create_entry_or_journal(self):
        with self.assertRaisesRegex(ValueError, 'title'):
            wf.add_paper(self.root, 'runtime-only')
        self.assertEqual(wf.paper_entries(self.root), [])
        self.assertFalse((self.root / '.workflow/execution_state.json').exists())

    def test_quoted_unicode_title_has_valid_json_and_portable_path(self):
        title = '中文标题：The "Hair" Method / 头发重建'
        paper = self.paper(title=title)
        self.assertEqual(wf.read_json(paper / 'reading_manifest.json')['paper_title'], title)
        self.assertLessEqual(len(paper.name.encode('utf-8')), 180)
        self.assertIn('中文标题', paper.name)

    def test_long_names_keep_full_heading_and_compact_table(self):
        title = 'Long Hair Reconstruction ' * 24
        paper = self.paper(title=title)
        self.assertLessEqual(len(paper.name.encode()), 180)
        index = (self.root / 'related_work/index.md').read_text()
        self.assertIn('## ' + title.strip(), index)
        for line in index.splitlines():
            if line.startswith('|'):
                self.assertLessEqual(sum(len(part.strip()) for part in line.split('|')[1:-1]), 160)

    def test_name_collision_refuses_merge(self):
        self.paper(title='Hair: A Method')
        with self.assertRaisesRegex(ValueError, 'exists'):
            self.paper('different-runtime', title='Hair / A Method')
        self.assertEqual(len(wf.paper_entries(self.root)), 1)

    def test_same_title_can_register_another_private_alias(self):
        paper = self.paper()
        self.assertEqual(self.paper('second-private-alias'), paper)
        self.assertEqual(wf.find_paper(self.root, 'second-private-alias')[0], paper)
        self.assert_private('runtime-paper-9327', 'second-private-alias')

    def test_alias_reuse_cannot_silently_select_another_title(self):
        self.paper()
        with self.assertRaisesRegex(ValueError, 'another title'):
            self.paper(title='Different Research Paper')

    def test_title_dedup_does_not_interpret_title_as_another_internal_alias(self):
        self.paper('unrelated-alias', title='First Real Title')
        other = self.paper('second-alias', title='unrelated-alias')
        self.assertEqual(wf.read_json(other / 'metadata.json')['title'], 'unrelated-alias')
        self.assertEqual(len(wf.paper_entries(self.root)), 2)

    def test_public_arxiv_version_is_preserved_and_not_a_directory(self):
        paper = self.paper(arxiv='2409.14778v1')
        self.assertEqual(wf.read_json(paper / 'metadata.json')['arxiv_id'], '2409.14778v1')
        with self.assertRaisesRegex(ValueError, 'version'):
            self.paper(arxiv='2409.14778v2')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            self.paper('another-alias', title='Different Title', arxiv='2409.14778v1')

    def test_repo_has_name_and_bidirectional_public_paths(self):
        paper = self.paper()
        repo = wf.add_reproduce(self.root, 'runtime-repo-1849', 'baselines',
                                paper_ids=['runtime-paper-9327'], name='Gaussian Haircut')
        self.assertEqual(repo.name, 'gaussian-haircut')
        metadata = wf.read_json(repo / 'metadata.json')
        self.assertEqual(metadata['name'], 'Gaussian Haircut')
        self.assertNotIn('repo_id', metadata)
        self.assertNotIn('paper_ids', metadata)
        self.assertEqual(metadata['related_paper_paths'], [paper.relative_to(self.root).as_posix()])
        self.assertEqual(wf.read_json(paper / 'metadata.json')['related_code_paths'], [repo.relative_to(self.root).as_posix()])
        self.assertEqual(wf.find_reproduce(self.root, 'Gaussian Haircut')[0], repo)
        self.assert_private('runtime-paper-9327', 'runtime-repo-1849')

    def test_repo_url_name_is_usable_without_name_argument(self):
        repo = wf.add_reproduce(self.root, 'private-checkout', 'baselines',
                                repo_url='https://github.com/author/OfficialHair.git')
        self.assertEqual(repo.name, 'officialhair')
        self.assertEqual(wf.read_json(repo / 'metadata.json')['name'], 'OfficialHair')
        self.assert_private('private-checkout')

    def test_repo_missing_name_and_url_fails_before_creation(self):
        with self.assertRaisesRegex(ValueError, 'name'):
            wf.add_reproduce(self.root, 'private-checkout', 'baselines')
        self.assertEqual(wf.reproduction_entries(self.root), [])

    def test_forward_association_lives_only_in_journal_until_resolved(self):
        repo = wf.add_reproduce(self.root, 'runtime-repo-1849', 'baselines',
                                paper_ids=['runtime-paper-9327'], name='Official Hair')
        self.assertEqual(wf.read_json(repo / 'metadata.json')['related_paper_paths'], [])
        self.assert_private('runtime-paper-9327', 'runtime-repo-1849')
        paper = self.paper()
        self.assertEqual(wf.read_json(repo / 'metadata.json')['related_paper_paths'], [paper.relative_to(self.root).as_posix()])
        self.assertEqual(wf.read_json(paper / 'metadata.json')['related_code_paths'], [repo.relative_to(self.root).as_posix()])
        self.assertNotIn('pending_paper_ids', execution_state.resolve_identity(self.root, 'repositories', 'runtime-repo-1849'))

    def test_idempotent_repo_registration_preserves_pending_associations(self):
        repo = wf.add_reproduce(self.root, 'runtime-repo-1849', 'baselines',
                                paper_ids=['runtime-paper-9327'], name='Official Hair')
        self.assertEqual(wf.add_reproduce(self.root, 'runtime-repo-1849', 'baselines'), repo)
        paper = self.paper()
        self.assertEqual(wf.read_json(repo / 'metadata.json')['related_paper_paths'], [paper.relative_to(self.root).as_posix()])

    def test_retier_preserves_names_and_moves_runtime_and_association_paths(self):
        paper = self.paper(tier='inbox')
        repo = wf.add_reproduce(self.root, 'runtime-repo-1849', 'baselines',
                                paper_ids=['runtime-paper-9327'], name='Official Hair')
        old = paper.relative_to(self.root).as_posix()
        with execution_state.transaction(self.root) as state:
            state['papers'][old] = {'internal_review_id': 'private-review', 'path': old + '/note.md'}
            state['runs']['private-run'] = {'tasks': {'runtime-paper-9327': {
                'paper_dir': old, 'evidence': {'materials': {'paper_dir': str(paper),
                'source_stamps': {old + '/paper.pdf': {'size': 10, 'mtime_ns': 3}},
                'report': {'content': old + '/paper.pdf was inspected with actual findings.'}}},
                'review': {'artifacts': {old + '/note.md': {'size': 20, 'mtime_ns': 2}}}}},
                'history': [{'paper_dir': old}]}
        wf.retier(self.root, 'runtime-paper-9327', 'ultra')
        moved = self.root / 'related_work/ultra' / paper.name
        new = moved.relative_to(self.root).as_posix()
        self.assertFalse(paper.exists())
        self.assertEqual(wf.find_paper(self.root, 'runtime-paper-9327')[0], moved)
        state = execution_state.load(self.root)
        self.assertIn(new, state['papers'])
        task = state['runs']['private-run']['tasks']['runtime-paper-9327']
        self.assertEqual(task['paper_dir'], new)
        self.assertEqual(task['evidence']['materials']['paper_dir'], str(moved))
        self.assertIn(new + '/paper.pdf', task['evidence']['materials']['source_stamps'])
        self.assertEqual(task['evidence']['materials']['report']['content'], old + '/paper.pdf was inspected with actual findings.')
        self.assertIn(new + '/note.md', task['review']['artifacts'])
        self.assertEqual(state['runs']['private-run']['history'][0]['paper_dir'], old)
        self.assertEqual(wf.read_json(repo / 'metadata.json')['related_paper_paths'], [new])
        wf.retier(self.root, 'Strand Aligned Hair Reconstruction', 'max')
        self.assertEqual(wf.find_paper(self.root, 'runtime-paper-9327')[0].parent.name, 'max')

    def test_survey_retier_keeps_survey_folder(self):
        paper = self.paper(paper_type='survey')
        wf.retier(self.root, 'runtime-paper-9327', 'ultra')
        self.assertTrue(paper.exists())
        self.assertEqual(paper.parent.name, 'survey')
        self.assertEqual(wf.read_json(paper / 'metadata.json')['priority'], 'ultra')

    def test_stale_alias_refuses_silent_title_fallback(self):
        self.paper()
        execution_state.register_identity(self.root, 'papers', 'Readable Title', 'related_work/inbox/missing')
        with self.assertRaisesRegex(ValueError, 'stale'):
            wf.find_paper(self.root, 'Readable Title')

    def test_legacy_lookup_is_read_only(self):
        entry = self.root / 'related_work/mid/legacy-folder'
        entry.mkdir()
        metadata = {'entry_kind': 'paper', 'paper_id': 'old-internal', 'title': 'Old Public Title',
                    'priority': 'mid'}
        wf.save_json(entry / 'metadata.json', metadata)
        before = (entry / 'metadata.json').read_bytes()
        self.assertEqual(wf.find_paper(self.root, 'old-internal')[0], entry)
        wf.reindex(self.root)
        self.assertEqual((entry / 'metadata.json').read_bytes(), before)
        self.assertNotIn('old-internal', (self.root / 'related_work/index.md').read_text())

    def test_code_tree_resolves_private_alias_and_public_name(self):
        repo = wf.add_reproduce(self.root, 'runtime-repo-1849', 'baselines', name='Official Hair')
        (repo / 'repo/model.py').write_text('pass\n')
        wf.code_tree(self.root, 'baselines', 'runtime-repo-1849')
        self.assertIn('model.py', (repo / 'code_tree.md').read_text())
        wf.code_tree(self.root, 'baselines', 'Official Hair')
        self.assert_private('runtime-repo-1849')

    def test_legacy_minimal_repo_metadata_remains_resolvable(self):
        entry = self.root / 'reproduce/baselines/legacy'
        (entry / 'repo').mkdir(parents=True)
        wf.save_json(entry / 'metadata.json', {'repo_path': 'repo'})
        wf.code_tree(self.root, 'baselines', 'legacy')
        self.assertTrue((entry / 'code_tree.md').is_file())

    def test_cli_retains_execution_parameters_and_adds_name(self):
        script = Path(wf.__file__)
        commands = [
            ['add-paper', str(self.root), 'cli-temporary-paper', '--title', 'Readable Paper'],
            ['add-reproduce', str(self.root), 'cli-temporary-repo', '--name', 'Readable Method',
             '--paper-id', 'cli-temporary-paper'],
            ['retier', str(self.root), 'cli-temporary-paper', 'ultra'],
        ]
        for arguments in commands:
            result = subprocess.run([sys.executable, str(script), *arguments], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / 'related_work/ultra/readable-paper').is_dir())
        self.assertTrue((self.root / 'reproduce/baselines/readable-method').is_dir())
        self.assert_private('cli-temporary-paper', 'cli-temporary-repo')


class FetchIdentityTests(IdentityProject):
    def setUp(self):
        super().setUp()
        fetch_paper.ARXIV_METADATA.clear()

    def atom(self, version='2409.14778v1', title='Human Hair Reconstruction'):
        return ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://arxiv.org/abs/'
                + version + '</id><title>' + title + '</title></entry></feed>').encode()

    def response(self, payload):
        return contextlib.nullcontext(io.BytesIO(payload))

    def fake_download(self, url, target, pdf=False):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'%PDF-1.7\nmock only' if pdf else b'\\documentclass{article}\n\\begin{document}Hair\\end{document}')
        return {'url': url, 'bytes': target.stat().st_size, 'content_type': 'application/pdf' if pdf else 'application/x-tex'}

    def test_new_fetch_gets_official_title_before_creating_directory(self):
        with patch.object(fetch_paper, 'urlopen', return_value=self.response(self.atom())) as network, \
                patch.object(fetch_paper.time, 'sleep'), patch.object(fetch_paper, 'download', side_effect=self.fake_download):
            paper = fetch_paper.fetch(self.root, '2409.14778v1', 'private-import', 'ultra')
        self.assertEqual(paper.name, 'human-hair-reconstruction')
        self.assertEqual(network.call_count, 1)
        self.assertIn('id_list=2409.14778v1', network.call_args.args[0].full_url)
        self.assertEqual(wf.read_json(paper / 'metadata.json')['arxiv_id'], '2409.14778v1')
        self.assert_private('private-import')

    def test_missing_official_title_does_not_create_entry(self):
        with patch.object(fetch_paper, 'urlopen', return_value=self.response(self.atom(title=''))), \
                patch.object(fetch_paper.time, 'sleep'), self.assertRaisesRegex(ValueError, 'title'):
            fetch_paper.fetch(self.root, '2409.14778v1', 'private-import', 'ultra')
        self.assertEqual(wf.paper_entries(self.root), [])

    def test_explicit_version_rejects_other_version_title(self):
        with patch.object(fetch_paper, 'urlopen', return_value=self.response(self.atom(version='2409.14778v2'))), \
                patch.object(fetch_paper.time, 'sleep'), self.assertRaisesRegex(ValueError, 'different'):
            fetch_paper.fetch(self.root, '2409.14778v1', 'private-import', 'ultra')
        self.assertEqual(wf.paper_entries(self.root), [])

    def test_latest_resolution_reuses_version_specific_title(self):
        with patch.object(fetch_paper, 'urlopen', return_value=self.response(self.atom())) as network, \
                patch.object(fetch_paper.time, 'sleep'), patch.object(fetch_paper, 'download', side_effect=self.fake_download):
            selected = fetch_paper.select_id(self.root, '2409.14778', 'private-import')
            paper = fetch_paper.fetch(self.root, selected, 'private-import', 'ultra')
        self.assertEqual(network.call_count, 1)
        self.assertEqual(paper.name, 'human-hair-reconstruction')

    def test_explicit_verified_title_skips_metadata_query_and_resume_keeps_name(self):
        with patch.object(fetch_paper, 'urlopen', side_effect=AssertionError('No actual network allowed')), \
                patch.object(fetch_paper, 'download', side_effect=self.fake_download):
            paper = fetch_paper.fetch(self.root, '2409.14778v1', 'private-import', 'ultra', title='Verified Hair Title')
            resumed = fetch_paper.fetch(self.root, '2409.14778v1', 'private-import', 'ultra')
        self.assertEqual(paper, resumed)
        self.assertEqual(paper.name, 'verified-hair-title')


if __name__ == '__main__':
    unittest.main()
