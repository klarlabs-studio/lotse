import hashlib
import json
import unittest

from codex_solver import command, environment
from probe_codex_retry import _as_status, evaluate_retry, request_receipt, settings


class RetryProbeUnitTests(unittest.TestCase):
    @staticmethod
    def retry_event(attempt, success=False, status=503):
        return {'event_name': 'rust/src/source.rs:10', 'attributes': {
            'event.name': 'codex.api_request', 'endpoint': '/v1/responses',
            'attempt': attempt, 'success': success,
            'http.response.status_code': status,
        }}

    @staticmethod
    def retry_receipts():
        return [{'method': 'POST', 'path': '/v1/responses', 'status': 503, 'auth_present': False},
                {'method': 'POST', 'path': '/v1/responses', 'status': 503, 'auth_present': False}]

    def test_explicit_retry_events_match_independent_stub_receipts(self):
        result = evaluate_retry([self.retry_event(0), self.retry_event(1)], self.retry_receipts())
        self.assertEqual(result['telemetry_attempts'], [0, 1])
        self.assertEqual(result['telemetry_statuses'], [503, 503])
        self.assertEqual(result['telemetry_success_values'], [False, False])
        self.assertTrue(result['telemetry_retry_observation_matches_receipts'])
        self.assertTrue(result['telemetry_success_false'])

    def test_success_mismatch_or_missing_fields_fails_closed(self):
        events = [self.retry_event(0), self.retry_event(1, success=True)]
        result = evaluate_retry(events, self.retry_receipts())
        self.assertFalse(result['telemetry_retry_observation_matches_receipts'])
        self.assertFalse(result['telemetry_success_false'])
        missing = self.retry_event(1)
        del missing['attributes']['attempt']
        result = evaluate_retry([self.retry_event(0), missing], self.retry_receipts())
        self.assertFalse(result['telemetry_retry_observation_matches_receipts'])
        self.assertEqual(result['telemetry_attempts'], [0, None])

    def test_wrong_status_or_endpoint_is_not_credited_as_retry(self):
        result = evaluate_retry([self.retry_event(0, status=200), self.retry_event(1)], self.retry_receipts())
        self.assertFalse(result['telemetry_retry_observation_matches_receipts'])
        wrong_endpoint = [self.retry_event(0), self.retry_event(1)]
        wrong_endpoint[1]['attributes']['endpoint'] = '/models'
        result = evaluate_retry(wrong_endpoint, self.retry_receipts())
        self.assertFalse(result['telemetry_retry_observation_matches_receipts'])
        self.assertEqual(result['telemetry_response_event_count'], 1)

    def test_status_parser_rejects_bool_and_float_values(self):
        self.assertIsNone(_as_status(True))
        self.assertIsNone(_as_status(503.0))
        self.assertIsNone(_as_status(503.9))
        self.assertEqual(_as_status(503), 503)
        self.assertEqual(_as_status('503'), 503)

    def test_independent_receipt_status_and_auth_must_match(self):
        events = [self.retry_event(0), self.retry_event(1)]
        receipts = self.retry_receipts()
        receipts[1]['status'] = 401
        result = evaluate_retry(events, receipts)
        self.assertFalse(result['telemetry_retry_observation_matches_receipts'])
        receipts[1]['status'] = 503
        receipts[1]['auth_present'] = True
        result = evaluate_retry(events, receipts)
        self.assertFalse(result['telemetry_retry_observation_matches_receipts'])

    def test_receipt_keeps_identity_free_body_fingerprint_and_auth_flag(self):
        entry = request_receipt('POST', '/v1/responses', 401, b'synthetic request', True)
        self.assertEqual(entry['body_sha256'], hashlib.sha256(b'synthetic request').hexdigest())
        self.assertEqual(entry['body_size'], len(b'synthetic request'))
        self.assertTrue(entry['auth_present'])
        self.assertNotIn('Authorization', json.dumps(entry))
        self.assertNotIn('synthetic request', json.dumps(entry))

    def test_command_uses_no_schema_or_auth_endpoint_and_disables_code_mode(self):
        configured = settings('http://127.0.0.1:4321/v1', 'http://127.0.0.1:4322/v1/logs')
        args = command('gpt-6-luna', 'low', 'instructions.txt', 'unused-schema.json')
        schema_index = args.index('--output-schema')
        del args[schema_index:schema_index + 2]
        args = [*args[:-1], *[part for value in configured for part in ('-c', value)], '-']
        self.assertNotIn('--output-schema', args)
        self.assertIn('features.code_mode=false', args)
        self.assertIn('model_providers.graf_local_retry.requires_openai_auth=false', args)
        self.assertIn('model_providers.graf_local_retry.base_url="http://127.0.0.1:4321/v1"', args)
        self.assertFalse(any('api.openai.com' in value or 'Authorization' in value for value in args))

    def test_environment_is_allowlisted(self):
        self.assertLessEqual(set(environment()),
                             {'HOME', 'PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'USER', 'SHELL', 'CODEX_HOME'})


if __name__ == '__main__':
    unittest.main()
