import unittest

from report_codex_comparison import comparison


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.spec = dict(id='batch', arms=['graf', 'graphify', 'search'], task_ids=['task'], attempts=2)
        self.report = dict(batch='batch', cases=[dict(task='task', attempt=k, arm=arm,
            passed=(arm != 'graf'), reported_usage_complete=True, audit=dict(total_tokens=100),
            native_mcp_calls=3, invocations=1, wall_s=2, error='failed', agent_called_finish=False)
            for k in (1, 2) for arm in self.spec['arms']])

    def test_failed_and_protocol_invalid_attempts_still_count(self):
        result = comparison(self.report, self.spec)
        self.assertEqual(result['complete_matched_groups'], 2)
        self.assertEqual(result['shared_totals']['graf']['reported_tokens'], 200)
        self.assertEqual(result['shared_totals']['graf']['verifier_passes'], 0)
        self.assertEqual(result['shared_totals']['graphify']['verifier_passes'], 2)
        self.assertEqual(result['shared_totals']['graf']['attempts_with_errors'], 2)
        self.assertFalse(result['agent_efficiency_acceptance_pass'])

    def test_missing_arm_excludes_whole_group_but_preserves_all_cases(self):
        self.report['cases'].pop()
        result = comparison(self.report, self.spec)
        self.assertEqual(result['complete_matched_groups'], 1)
        self.assertEqual(result['missing_trials'], 1)
        self.assertEqual(len(result['cases']), 6)
        self.assertEqual(sum(c['included_in_shared_totals'] for c in result['cases']), 3)
        self.assertEqual(result['shared_totals']['graf']['reported_tokens'], 100)
        self.assertIsNone(next(c['summary'] for c in result['cases'] if c['status'] == 'missing'))

    def test_incomplete_usage_is_unknown_not_zero(self):
        for change in (dict(reported_usage_complete=False), dict(audit=None)):
            self.report['cases'][0].update(change)
            self.assertIsNone(comparison(self.report, self.spec)['shared_totals']['graf']['reported_tokens'])

    def test_duplicates_foreign_cases_and_batch_mismatch_rejected(self):
        for case in (dict(self.report['cases'][0]), dict(self.report['cases'][0], task='foreign')):
            report = dict(self.report, cases=self.report['cases'] + [case])
            with self.assertRaises(ValueError):
                comparison(report, self.spec)
        with self.assertRaises(ValueError):
            comparison(dict(self.report, batch='wrong'), self.spec)


if __name__ == '__main__':
    unittest.main()
