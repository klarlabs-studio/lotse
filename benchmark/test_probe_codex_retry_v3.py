"""Unit coverage for the frozen immediate/delayed local retry diagnostic."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import tomllib

import probe_codex_retry_v3 as probe


class RetryV3Tests(unittest.TestCase):
    def valid_plan(self):
        hashes = {
            name: hashlib.sha256((probe.BASE / name).read_bytes()).hexdigest()
            for name in probe.REQUIRED_HASHES
        }
        return {
            'id': probe.PLAN_ID,
            'scope': 'synthetic_instrumentation_diagnostic_only',
            'maximum_cli_invocations_per_case': 1,
            'maximum_cli_invocations_total': 2,
            'timeout_seconds': 60,
            'maximum_retries': 1,
            'expected_response_attempts': 2,
            'frozen_before_invocations': True,
            'failure_policy': 'retain_planned_conditions_no_replacements',
            'requested_model': 'gpt-6-luna',
            'reasoning_effort': 'low',
            'cases': ['immediate_failure', 'delayed_failure'],
            'response_delay_seconds': dict(probe.CASES),
            'implementation_sha256': hashes,
        }

    def test_cases_and_settings_keep_analytics_disabled_and_exporter_fixed(self):
        self.assertEqual(probe.CASES, {'immediate_failure': 0, 'delayed_failure': 6})
        from probe_codex_retry import settings
        fast = settings('http://127.0.0.1:10001/v1', 'http://127.0.0.1:10002/v1/logs')
        delayed = settings('http://127.0.0.1:10001/v1', 'http://127.0.0.1:10002/v1/logs')
        self.assertEqual(fast, delayed)
        parsed = tomllib.loads('\n'.join(fast))
        self.assertFalse(parsed['analytics']['enabled'])
        self.assertEqual(parsed['otel']['exporter'], tomllib.loads('\n'.join(delayed))['otel']['exporter'])
        self.assertIn('model_providers.graf_local_retry.request_max_retries=1', fast)

    def test_plan_rejects_reordering_delay_changes_and_wrong_bounds(self):
        plan = self.valid_plan()
        with patch.object(probe, 'verify_hashes', return_value={}):
            self.assertEqual(probe.validate_plan(plan), {})
            for altered in (
                dict(plan, cases=['delayed_failure', 'immediate_failure']),
                dict(plan, response_delay_seconds={'immediate_failure': 0, 'delayed_failure': 7}),
                dict(plan, maximum_cli_invocations_total=3),
            ):
                with self.assertRaises(ValueError):
                    probe.validate_plan(altered)

    def test_hash_mismatch_rejected(self):
        plan = self.valid_plan()
        plan['implementation_sha256']['benchmark/codex_solver.py'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'hash verification'):
            probe.verify_hashes(plan)

    def test_delay_only_wraps_503_after_receipt(self):
        order = []

        class Handler:
            def record(self, status, payload=b''):
                order.append(('receipt', status, payload))

            def reply(self, status, payload=b'fixed'):
                order.append(('reply', status, payload))

            def synthetic_post(self, status, payload):
                self.record(status, payload)
                self.reply(status, payload)

        server = Mock()
        server.RequestHandlerClass = Handler
        stub = Mock(server=server, delay_seconds=6)
        delayed_stub = probe.DelayStub('/tmp/no-network', 6)
        delayed_stub.server = server
        with patch('probe_codex_retry_v3.RetryStub.__enter__', return_value=stub), \
                patch('probe_codex_retry_v3.time.sleep', side_effect=lambda n: order.append(('sleep', n))):
            wrapped_stub = delayed_stub.__enter__()
            handler = Handler()
            handler.synthetic_post(503, b'failure')
            handler.synthetic_post(404, b'other')
        self.assertIs(wrapped_stub, stub)
        self.assertEqual(order, [
            ('receipt', 503, b'failure'), ('sleep', 6), ('reply', 503, b'failure'),
            ('receipt', 404, b'other'), ('reply', 404, b'other'),
        ])

    def test_receipt_gate_accepts_failed_first_observation_but_checks_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            plan_path = Path(temp) / 'plan.json'
            plan = self.valid_plan()
            plan_path.write_text(json.dumps(plan))
            receipt_path = Path(temp) / 'local_retry_v3' / 'immediate_failure' / 'receipt.json'
            receipt_path.parent.mkdir(parents=True)
            receipt_path.write_text(json.dumps({
                'id': probe.PLAN_ID, 'case': 'immediate_failure',
                'plan_sha256': probe.sha256(plan_path),
                'implementation_sha256': plan['implementation_sha256'],
                'diagnostic_observation_pass': False,
            }))
            self.assertFalse(probe.require_previous(plan_path, plan)['diagnostic_observation_pass'])
            receipt = json.loads(receipt_path.read_text())
            receipt['plan_sha256'] = 'changed'
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'does not match'):
                probe.require_previous(plan_path, plan)


if __name__ == '__main__':
    unittest.main()
