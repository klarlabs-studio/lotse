"""Unit coverage for the frozen two-case local retry diagnostic."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import tomllib

import probe_codex_retry_v2 as probe


class RetryV2Tests(unittest.TestCase):
    def test_analytics_is_only_configuration_difference(self):
        off = probe.settings_v2('http://127.0.0.1:10001/v1', 'http://127.0.0.1:10002/v1/logs', False)
        on = probe.settings_v2('http://127.0.0.1:10001/v1', 'http://127.0.0.1:10002/v1/logs', True)
        self.assertEqual(len(off), len(on))
        self.assertEqual([x for x in off if x != 'analytics.enabled=false'],
                         [x for x in on if x != 'analytics.enabled=true'])
        self.assertIn('analytics.enabled=false', off)
        self.assertIn('analytics.enabled=true', on)
        self.assertIn('model_providers.graf_local_retry.requires_openai_auth=false', off)
        self.assertIn('model_providers.graf_local_retry.request_max_retries=1', off)
        self.assertEqual(sum(x.endswith('request_max_retries=1') for x in off), 1)
        parsed_off = tomllib.loads('\n'.join(off))
        parsed_on = tomllib.loads('\n'.join(on))
        self.assertFalse(parsed_off['analytics']['enabled'])
        self.assertTrue(parsed_on['analytics']['enabled'])
        self.assertEqual(parsed_off['model_providers'], parsed_on['model_providers'])
        self.assertEqual(parsed_off['otel'], parsed_on['otel'])

    def valid_plan(self):
        hashes = {}
        for name in probe.REQUIRED_HASHES:
            hashes[name] = hashlib.sha256((probe.BASE / name).read_bytes()).hexdigest()
        return {
            'id': probe.PLAN_ID,
            'scope': 'synthetic_instrumentation_diagnostic_only',
            'maximum_cli_invocations_per_case': 1,
            'maximum_cli_invocations_total': 2,
            'timeout_seconds': 60,
            'maximum_retries': 1,
            'expected_response_attempts': 2,
            'frozen_before_invocations': True,
            'requested_model': 'gpt-6-luna',
            'reasoning_effort': 'low',
            'failure_policy': 'retain_planned_conditions_no_replacements',
            'cases': ['analytics_disabled', 'analytics_enabled'],
            'implementation_sha256': hashes,
        }

    def test_plan_rejects_case_reordering_and_wrong_bounds(self):
        plan = self.valid_plan()
        with patch.object(probe, 'verify_hashes', return_value={}):
            self.assertEqual(probe.validate_plan(plan), {})
            reordered = dict(plan, cases=['analytics_enabled', 'analytics_disabled'])
            with self.assertRaises(ValueError):
                probe.validate_plan(reordered)
            altered = dict(plan, maximum_cli_invocations_total=3)
            with self.assertRaises(ValueError):
                probe.validate_plan(altered)

    def test_hash_mismatch_rejected(self):
        plan = self.valid_plan()
        plan['implementation_sha256']['benchmark/codex_solver.py'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'hash verification'):
            probe.verify_hashes(plan)

    def test_enabled_case_requires_matching_previous_receipt_even_if_failed(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            plan_path = base / 'plan.json'
            plan = self.valid_plan()
            plan_path.write_text(json.dumps(plan))
            receipt_path = base / 'local_retry_v2' / 'analytics_disabled' / 'receipt.json'
            receipt_path.parent.mkdir(parents=True)
            receipt_path.write_text(json.dumps({
                'id': probe.PLAN_ID,
                'case': 'analytics_disabled',
                'plan_sha256': probe.sha256(plan_path),
                'implementation_sha256': plan['implementation_sha256'],
                'diagnostic_observation_pass': False,
            }))
            receipt = probe.require_previous(plan_path, plan)
            self.assertFalse(receipt['diagnostic_observation_pass'])
            receipt['plan_sha256'] = 'changed'
            receipt_path.write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError, 'does not match'):
                probe.require_previous(plan_path, plan)


if __name__ == '__main__':
    unittest.main()
