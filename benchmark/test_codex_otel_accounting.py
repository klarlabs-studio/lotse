import copy
import unittest

from codex_otel_accounting import reconcile


def event(name, **attrs):
    return {'event_name': 'event source.rs:1', 'attributes': {'event.name': name, **attrs}}


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.events = [event('codex.api_request', endpoint='/models', success=True),
                       event('codex.websocket_request', success='true'),
                       event('codex.sse_event', **{'event.kind': 'response.completed',
                             'input_token_count': '20', 'output_token_count': '5',
                             'cached_token_count': 10, 'reasoning_token_count': 0})]
        self.stream = [{'type': 'turn.completed', 'usage': {
            'input_tokens': 20, 'output_tokens': 5, 'cached_input_tokens': 10,
            'reasoning_output_tokens': 0}}]

    def test_semantic_name_and_models_are_not_inference_requests(self):
        r = reconcile(self.events, self.stream)
        self.assertTrue(r['reconciliation_pass'])
        self.assertEqual(r['observed_model_request_events'], 1)
        self.assertFalse(r['complete_agent_accounting_pass'])

    def test_zero_output_completion_is_counted_and_blocks_mismatched_totals(self):
        extra = copy.deepcopy(self.events[-1])
        extra['attributes'].update(input_token_count='7', output_token_count='0', cached_token_count=0)
        r = reconcile(self.events + [self.events[1], extra], self.stream)
        self.assertEqual(r['telemetry_minus_cli']['input_tokens'], 7)
        self.assertIn('cli_telemetry_usage_mismatch', r['errors'])

    def test_missing_invalid_and_inconsistent_tokens_fail_closed(self):
        for value in [None, True, -1, 2.5, 'NaN', '']:
            with self.subTest(value=value):
                events = copy.deepcopy(self.events)
                events[-1]['attributes']['input_token_count'] = value
                self.assertFalse(reconcile(events, self.stream)['reconciliation_pass'])
        self.events[-1]['attributes']['cached_token_count'] = 21
        self.assertIn('invalid_completion_usage', reconcile(self.events, self.stream)['errors'])

    def test_duplicate_usage_and_missing_terminal_fail_closed(self):
        self.assertIn('duplicate_completion_event', reconcile(self.events + [self.events[-1]], self.stream)['errors'])
        for stream in [[], self.stream * 2]:
            self.assertIn('expected_one_cli_usage', reconcile(self.events, stream)['errors'])

    def test_failure_unknown_and_request_count_mismatch(self):
        for success in [False, None, 0, 1]:
            self.events[1]['attributes']['success'] = success
            self.assertFalse(reconcile(self.events, self.stream)['reconciliation_pass'])
        self.assertIn('request_completion_count_mismatch', reconcile(self.events[:1] + self.events[2:], self.stream)['errors'])

    def test_http_response_attempts_and_cli_errors_are_visible(self):
        self.events[1] = event('codex.api_request', endpoint='/v1/responses', success=True)
        self.assertEqual(reconcile(self.events, self.stream)['observed_model_request_events'], 1)
        self.stream.append({'type': 'item.completed', 'item': {'type': 'error'}})
        self.assertIn('cli_error_items_require_audit', reconcile(self.events, self.stream)['errors'])

    def test_independent_tool_receipt_and_identity_free_output(self):
        self.events.append(event('codex.tool_result', tool_name='ping', **{'user.email': 'private@example.com'}))
        self.stream.append({'type': 'item.completed', 'item': {'type': 'mcp_tool_call',
                            'server': 'broker', 'tool': 'ping', 'status': 'completed'}})
        broker = [{'request': {'method': 'tools/call', 'params': {'name': 'ping', 'arguments': {}}},
                   'reply': {'result': {'isError': False, 'content': [{'type': 'text', 'text': 'pong'}]}}}]
        result = reconcile(self.events, self.stream, broker)
        self.assertTrue(result['tool_receipt_parity'])
        self.assertNotIn('private@example.com', str(result))
        self.assertFalse(reconcile(self.events, self.stream, [])['reconciliation_pass'])
        broker[0]['reply']['result']['isError'] = True
        self.assertFalse(reconcile(self.events, self.stream, broker)['reconciliation_pass'])


if __name__ == '__main__':
    unittest.main()
