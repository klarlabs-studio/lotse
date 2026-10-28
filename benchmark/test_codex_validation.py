import unittest

from codex_validation import evaluate
from run_codex_mcp_batch import entries


class SolverValidationTests(unittest.TestCase):
    def setUp(self):
        self.spec = dict(task_ids=['a', 'b', 'c'], attempts=2,
                         arms=['graf_current_orient', 'graphify_orient', 'search_only'],
                         paired_order=True, seed=42,
                         solver_validation=dict(min_passes_per_arm=4, min_passes_per_task_arm=1))
        self.runs = [dict(task=t, attempt=k, arm=arm, passed=True, reported_usage_complete=True,
                          error=None, infrastructure_error=False, agent_called_finish=True)
                     for t, k, arm in entries(self.spec)]

    def test_all_cases_required_without_duplicates_or_foreign_cases(self):
        self.assertTrue(evaluate(self.spec, self.runs)['solver_readiness_pass'])
        for runs in [[], self.runs[:-1], self.runs + [self.runs[0]],
                     self.runs + [dict(self.runs[0], task='foreign')]]:
            self.assertFalse(evaluate(self.spec, runs)['solver_readiness_pass'])

    def test_aggregate_passes_do_not_hide_complete_task_loss(self):
        for run in self.runs:
            if run['task'] == 'a' and run['arm'] == 'graf_current_orient':
                run['passed'] = False
        result = evaluate(self.spec, self.runs)
        self.assertTrue(result['protocol_validation_pass'])
        self.assertEqual(result['arms']['graf_current_orient']['verifier_passes'], 4)
        self.assertFalse(result['behavioral_validation_pass'])
        self.assertEqual(len(result['severe_quality_losses']), 4)
        self.assertFalse(result['product_promotion_pass'])

    def test_usage_error_or_forced_finish_blocks_readiness(self):
        for change in [dict(error='timeout'), dict(reported_usage_complete=False),
                       dict(infrastructure_error=True), dict(agent_called_finish=False)]:
            runs = [dict(r) for r in self.runs]
            runs[0].update(change)
            result = evaluate(self.spec, runs)
            self.assertFalse(result['protocol_validation_pass'])
            self.assertFalse(result['solver_readiness_pass'])

    def test_new_discovery_requirement_does_not_rewrite_historical_protocol(self):
        self.assertTrue(evaluate(self.spec, self.runs)['protocol_validation_pass'])
        self.spec['require_tool_discovery'] = True
        self.assertFalse(evaluate(self.spec, self.runs)['protocol_validation_pass'])
        for run in self.runs:
            run['tool_discovery'] = dict(valid=True)
        self.assertTrue(evaluate(self.spec, self.runs)['protocol_validation_pass'])
        self.runs[0]['tool_discovery']['valid'] = False
        self.assertFalse(evaluate(self.spec, self.runs)['protocol_validation_pass'])

    def test_order_preserves_adjacent_sequential_comparators_per_pair(self):
        order = entries(self.spec)
        self.assertEqual(order, entries(self.spec))
        self.assertEqual(len(order), 18)
        for i in range(0, len(order), 3):
            group = order[i:i+3]
            self.assertEqual(len({(t, k) for t, k, arm in group}), 1)
            self.assertEqual({arm for t, k, arm in group}, set(self.spec['arms']))


if __name__ == '__main__':
    unittest.main()
