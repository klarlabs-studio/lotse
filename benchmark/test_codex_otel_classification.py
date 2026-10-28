import unittest

from codex_otel_classification import classify


def completion(**attrs):
    return {'event_name': 'source.rs:7', 'attributes': {
        'event.name': 'codex.sse_event', 'event.kind': 'response.completed', **attrs}}


def usage(**extra):
    return completion(input_token_count=12, output_token_count=3,
                      cached_token_count=4, reasoning_token_count=1, **extra)


class ClassificationTests(unittest.TestCase):
    def test_http_tokenless_marker_and_usage_are_distinct(self):
        events = [
            {'attributes': {'event.name': 'codex.api_request', 'endpoint': '/v1/responses'}},
            completion(), usage(),
        ]
        report = classify(events)
        self.assertEqual(report['observed_http_requests'], 1)
        self.assertEqual(report['observed_response_completed_events'], 2)
        self.assertEqual(report['observed_response_completed_markers'], 1)
        self.assertEqual(report['observed_usage_records'], 1)
        self.assertEqual(report['usage_totals']['input_tokens'], 12)
        self.assertEqual(report['correlation']['uncorrelated_usage_records'], 1)
        self.assertFalse(report['complete_agent_accounting'])
        self.assertFalse(report['product_promotion'])

    def test_websocket_zero_output_usage_is_retained(self):
        record = completion(input_token_count='5', output_token_count='0',
                            cached_token_count=0, reasoning_token_count=0,
                            tool_token_count=0)
        record['attributes']['event.name'] = 'codex.sse_event'
        report = classify([{'attributes': {'event.name': 'codex.websocket_request'}}, record])
        self.assertEqual(report['observed_websocket_requests'], 1)
        self.assertEqual(report['observed_usage_records'], 1)
        self.assertEqual(report['usage_records'][0]['output_tokens'], 0)
        self.assertEqual(report['usage_records'][0]['tool_token_count'], 0)
        self.assertEqual(report['observed_response_completed_markers'], 0)

    def test_partial_and_malformed_usage_fail_closed(self):
        cases = [completion(input_token_count=1),
                 completion(input_token_count=1, output_token_count=1,
                            cached_token_count=0, reasoning_token_count=True),
                 completion(input_token_count=1, output_token_count=-1,
                            cached_token_count=0, reasoning_token_count=0),
                 completion(input_token_count=1, output_token_count=1.5,
                            cached_token_count=0, reasoning_token_count=0)]
        report = classify(cases)
        self.assertEqual(report['observed_usage_records'], 0)
        self.assertEqual(report['invalid_usage_records'], len(cases))
        self.assertIn('invalid_or_partial_usage_record', report['errors'])

    def test_duplicate_identifiable_records_are_reported_without_values(self):
        a = usage(**{'response.id': 'private-response-token'})
        b = usage(**{'response.id': 'private-response-token', 'request.id': 'other-private-token'})
        report = classify([a, b])
        self.assertEqual(report['duplicate_identifiable_usage_records'], 1)
        self.assertEqual(report['correlation']['key_names'], ['request.id', 'response.id'])
        self.assertNotIn('private-response-token', repr(report))
        self.assertNotIn('other-private-token', repr(report))

    def test_optional_token_only_event_is_partial_not_marker(self):
        report = classify([completion(tool_token_count=0)])
        self.assertEqual(report['observed_response_completed_markers'], 0)
        self.assertEqual(report['invalid_usage_records'], 1)

    def test_ambiguous_uncorrelated_records_are_never_paired_by_order(self):
        report = classify([completion(), usage(), completion(), usage()])
        self.assertEqual(report['observed_response_completed_events'], 4)
        self.assertEqual(report['observed_response_completed_markers'], 2)
        self.assertEqual(report['observed_usage_records'], 2)
        self.assertEqual(report['correlation']['uncorrelated_usage_records'], 2)
        self.assertIn('usage_records_uncorrelated_completeness_unproven', report['errors'])
        self.assertFalse(report['correlation']['available'])


if __name__ == '__main__':
    unittest.main()
