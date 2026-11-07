"""Regression checks for diff routing and targeted corpus selection."""
import json
import unittest
from unittest.mock import patch

from tools.batch_feedback import changed_paths, select, smoke_cases
from tools.conformance import run_cases
from tools.specification import ROOT


class BatchFeedbackTests(unittest.TestCase):
    def test_workflow_triggers_keep_branch_feedback_and_tag_acceptance_separate(self):
        workflows = ROOT / '.github/workflows'
        feedback = (workflows / 'r6-feedback.yml').read_text()
        gate = (workflows / 'r6-specification.yml').read_text()
        self.assertIn('pull_request:\n    branches: [r6-rebuild]', feedback)
        self.assertIn('push:\n    branches: [r6-rebuild]', feedback)
        self.assertIn('--fast --changed-from github-event', feedback)
        self.assertNotIn('ci_artifacts build', feedback)
        self.assertIn("push:\n    tags: ['r6-accept/*']", gate)
        self.assertNotIn('branches:', gate.split('permissions:')[0])
        self.assertIn('--require-recovery', gate)

    def test_drawing_batch_selects_its_corpus_and_explicit_tests(self):
        corpora = {'polylines', 'lines', 'arrays'}
        paths = ['conformance/polylines/v5-force-overlay-time.pine', 'tools/polylines_fixtures.py',
                 'spec/polylines-profile.json', 'tests/test_polylines.py', 'docs/PINE_FUNCTION_COVERAGE.md']
        self.assertEqual(select(paths, corpora, {'test_polylines.py'}), (['polylines'], ['test_polylines.py'], []))
        self.assertEqual(select(['conformance/chart-points/cases.json'], corpora | {'chart-points'}, set()),
                         (['chart-points'], [], []))

    def test_shared_unknown_and_unavailable_base_use_all_smokes(self):
        corpora = {'polylines', 'lines'}
        for paths, missing, unmapped in (
                (['packages/compiler/semantic/binder.py'], False, []),
                (['packages/unknown.py'], False, ['packages/unknown.py']), (['tests/test_batch_feedback.py'], True, [])):
            with self.subTest(paths=paths, missing=missing):
                self.assertEqual(select(paths, corpora, {'test_batch_feedback.py'}, missing_base=missing),
                                 (sorted(corpora), [], unmapped))

    def test_changed_pine_case_and_edited_expectation_have_priority(self):
        path = ROOT / 'conformance/polylines/cases.json'
        name = 'v5-force-overlay-time'
        self.assertEqual(smoke_cases(path, set()), ('v6-drawings',))
        self.assertEqual(smoke_cases(path, {f'conformance/polylines/{name}.pine'}), (name,))
        rows = json.loads(path.read_bytes())['cases']
        rejected = 'v6-force-overlay-type'
        old = {row['case_id']: row for row in rows if row['case_id'] not in (name, rejected)}
        self.assertEqual(smoke_cases(path, set(), old), (name, rejected))

    def test_targeted_conformance_executes_both_pilot_versions_without_writing_report(self):
        path = ROOT / 'conformance/results/polylines.json'
        before = path.read_bytes() if path.exists() else None
        ids = ['v5-force-overlay-time', 'v6-force-overlay-time']
        report = run_cases(cases_path='conformance/polylines/cases.json', case_ids=ids)
        self.assertEqual([row['case_id'] for row in report['results']], ids)
        self.assertTrue(all(row['observed']['polylines'][0]['force_overlay'] is True for row in report['results']))
        self.assertEqual(path.read_bytes() if path.exists() else None, before)
        with self.assertRaisesRegex(ValueError, 'existing conformance cases'):
            run_cases(cases_path='conformance/polylines/cases.json', case_ids=['unknown'])

    def test_push_uses_before_and_pr_uses_base_with_both_sides_of_a_rename(self):
        for event, base in (({'before': 'push-base'}, 'push-base'), ({'pull_request': {'base': {'sha': 'pr-base'}}}, 'pr-base')):
            with self.subTest(base=base), patch.dict('os.environ', {'GITHUB_EVENT_PATH': 'event.json'}), \
                 patch('tools.batch_feedback.read_json', return_value=event), patch('tools.batch_feedback.git') as git:
                git.side_effect = (b'commit\n', b'old.py\0new.py\0', b'untracked.py\0')
                self.assertEqual(changed_paths('github-event'), ('commit', ['new.py', 'old.py', 'untracked.py']))
                git.assert_any_call('rev-parse', '--verify', '--end-of-options', base+'^{commit}', allow_missing=True)
                git.assert_any_call('diff', '--name-only', '--no-renames', '-z', 'commit', '--')


if __name__ == '__main__':
    unittest.main()
