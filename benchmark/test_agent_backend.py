import copy
from pathlib import Path
import unittest

from agent_backend import QUERY_ARGS, query_command
from compare_graphify import graf_query_command


class AgentBackendTests(unittest.TestCase):
    def setUp(self):
        self.plan = {'comparison': {'shared_output_budget': 2000}, 'arms': [
            {'id': arm, 'binary': arm, 'query_args': list(args)} for arm, args in QUERY_ARGS.items()]}

    def command(self, arm='graf_v61', query='example', budget=2000):
        return query_command(self.plan, arm, '/source', '/index/graph.json', query, budget, Path('/base'))

    def test_graf_commands_match_frozen_retrieval_driver_exactly(self):
        for arm, historical in [('graf_v59', 'graf_evidence_rescue'), ('graf_v61', 'graf_evidence_selective')]:
            self.assertEqual(self.command(arm), graf_query_command(
                Path('/base') / arm, Path('/source'), historical, 'example', 2000))

    def test_graphify_native_cli_keeps_budget_and_source_graph(self):
        self.assertEqual(self.command('graphify'), [
            '/base/graphify', 'query', 'example', '--graph', '/index/graph.json', '--budget', '2000'])

    def test_untrusted_query_stays_one_literal_argument(self):
        query = '-x $(touch /tmp/no) `id` {budget}\nhello'
        for arm in QUERY_ARGS:
            command = self.command(arm, query)
            self.assertEqual(command.count(query), 1)

    def test_old_units_command_or_extra_ranking_flags_are_rejected(self):
        for args in [['units', '{query}', '--text'], [*QUERY_ARGS['graf_v61'], '--rewrite']]:
            self.plan['arms'][1]['query_args'] = args
            with self.assertRaisesRegex(ValueError, 'evidence contract'):
                self.command()

    def test_budget_cannot_silently_differ_by_arm(self):
        for budget in [True, 0, -1, 2001, 8001, '2000']:
            for arm in QUERY_ARGS:
                with self.assertRaises(ValueError):
                    self.command(arm, budget=budget)

    def test_duplicate_or_unknown_arm_rejected(self):
        with self.assertRaises(ValueError):
            self.command('other')
        self.plan['arms'].append(copy.deepcopy(self.plan['arms'][1]))
        with self.assertRaises(ValueError):
            self.command()


if __name__ == '__main__':
    unittest.main()
