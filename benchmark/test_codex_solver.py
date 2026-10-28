import json
import unittest
from pathlib import Path
from codex_solver import command, parse_events


def stream(action=None):
    return [{"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(
        action or {"operation": "read", "arguments": {"path": "main.py"}})}},
        {"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 20,
                                               "output_tokens": 12}}]


class CodexTests(unittest.TestCase):
    def test_usage_preserves_unknown_cost_and_reasoning(self):
        op, args, usage = parse_events(stream(), "frozen", {"read"})
        self.assertEqual((op, args), ("read", {"path": "main.py"}))
        self.assertEqual(usage["input_tokens"], 100)
        self.assertIsNone(usage["cost_usd"])
        self.assertIsNone(usage["reasoning_tokens"])
        self.assertIsNone(usage["internal_model_request_count"])

    def test_errors_native_tools_and_missing_usage_fail_closed(self):
        variants = [stream()[:-1], stream() + [{"type": "turn.failed"}],
                    stream() + [{"type": "item.started", "item": {"type": "command_execution"}}],
                    stream({"operation": "retrieve", "arguments": {}}),
                    stream({"operation": "read", "arguments": []})]
        for events in variants:
            with self.subTest(events=events), self.assertRaises(ValueError):
                parse_events(events, "frozen", {"read"})
        for value in [None, -1, True, 101]:
            events = stream()
            events[-1]["usage"]["cached_input_tokens"] = value
            with self.assertRaises(ValueError):
                parse_events(events, "frozen", {"read"})

    def test_command_disables_foreign_config_and_builtin_access(self):
        args = command("gpt-6-luna", "low", Path('/x/instructions'), Path('/x/schema'))
        joined = ' '.join(args)
        for flag in ['--ignore-user-config', '--ephemeral', '--sandbox read-only',
                     'features.shell_tool=false', 'agents.enabled=false', 'project_doc_max_bytes=0',
                     'features.plugins=false', 'features.apps=false', 'features.hooks=false']:
            self.assertIn(flag, joined)
        self.assertEqual(args[-1], '-')


if __name__ == '__main__':
    unittest.main()

class DiagnosticTests(unittest.TestCase):
    def test_plan_is_paired_and_deterministic(self):
        from run_codex_batch import entries
        spec = {'task_ids': ['a','b'], 'attempts': 2, 'arms': ['graf','graphify','search'], 'seed': 33}
        self.assertEqual(entries(spec), entries(spec))
        self.assertEqual(len(entries(spec)), 12)
        self.assertEqual(len(set(entries(spec))), 12)

    def test_missing_usage_and_trials_never_pass_gate(self):
        from run_codex_batch import report
        spec = {'id': 'd', 'task_ids': ['a'], 'attempts': 1, 'arms': ['graf','graphify'], 'seed': 33}
        result = report(spec, [{'arm': 'graf', 'passed': False, 'audit': None, 'reported_usage_complete': False}])
        self.assertFalse(result['product_promotion_pass'])
        self.assertFalse(result['agent_efficiency_acceptance_pass'])
        self.assertIn('Missing trials',result['blockers'])
        self.assertIn('Incomplete invocation usage',result['blockers'])

class DispatchTests(unittest.TestCase):
    def test_only_first_action_is_dispatched_and_all_usage_counted(self):
        events = stream({'operation': 'edit', 'arguments': {'path': 'main.py', 'old': '\treturn "Helo"\n', 'new': '\treturn "Hello"\n', 'query': None}})
        events.insert(1, {'type':'item.completed', 'item': {'type':'agent_message', 'text':json.dumps({'operation':'test','arguments':{}})}})
        operation, arguments, usage = parse_events(events, 'frozen', {'edit','test'})
        self.assertEqual(operation,'edit')
        self.assertEqual(arguments['old'], '\treturn "Helo"\n')
        self.assertNotIn('query',arguments)
        self.assertEqual(usage['discarded_action_messages'],1)
        self.assertEqual(usage['input_tokens'],100)

    def test_invalid_argument_types_do_not_reach_broker(self):
        for arguments in [{'budget':True},{'unexpected':'x'},{'path':123}]:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                parse_events(stream({'operation':'read','arguments':arguments}),'m',{'read'})
