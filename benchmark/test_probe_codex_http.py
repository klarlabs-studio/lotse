import hashlib
import json
from pathlib import Path
import tempfile
import unittest

try:
    import tomllib
except ImportError:  # Python 3.9 test environments can skip the stdlib TOML parser check.
    tomllib = None

from probe_codex_http import (CASES, PROVIDER_SETTINGS, build_command,
                              second_case_allowed, validate_plan)


class HttpProbeCommandTests(unittest.TestCase):
    def settings(self, argv):
        return {argv[i + 1].split('=', 1)[0]: argv[i + 1].split('=', 1)[1]
                for i, value in enumerate(argv[:-1]) if value == '-c'}

    def test_plain_http_uses_default_provider_and_removes_output_schema(self):
        argv = build_command('plain_http', 'gpt-6-luna', 'low', '/tmp/instructions',
                             Path('/tmp/ping.jsonl'), 'http://127.0.0.1:1234/v1/logs')
        settings = self.settings(argv)
        self.assertNotIn('--output-schema', argv)
        self.assertEqual(argv[-1], '-')
        self.assertNotIn('base_url', settings)
        self.assertEqual(json.loads(settings['features.code_mode']), False)
        for key, value in PROVIDER_SETTINGS.items():
            self.assertEqual(json.loads(settings[key]), value)
        self.assertEqual(json.loads(settings['otel.log_user_prompt']), False)
        self.assertEqual(argv[-1], '-')

    @unittest.skipIf(tomllib is None, 'requires Python 3.11 TOML parser')
    def test_plain_http_overrides_are_valid_toml(self):
        argv = build_command('plain_http', 'gpt-6-luna', 'low', '/tmp/instructions',
                             Path('/tmp/ping.jsonl'), 'http://127.0.0.1:1234/v1/logs')
        config = tomllib.loads('\n'.join(argv[i + 1] for i, value in enumerate(argv[:-1]) if value == '-c'))
        self.assertEqual(config['otel']['exporter']['otlp-http'], {
            'endpoint': 'http://127.0.0.1:1234/v1/logs', 'protocol': 'json'})

    def test_native_http_preserves_mcp_controls_and_adds_provider(self):
        argv = build_command('one_synthetic_mcp_ping_http', 'gpt-6-luna', 'low',
                             '/tmp/instructions', Path('/tmp/ping.jsonl'),
                             'http://127.0.0.1:1234/v1/logs')
        settings = self.settings(argv)
        self.assertNotIn('--output-schema', argv)
        self.assertEqual(json.loads(settings['features.code_mode']), False)
        self.assertEqual(json.loads(settings['mcp_servers.broker.required']), True)
        self.assertIn('--ping-server', str(argv))
        self.assertNotIn('base_url', settings)
        self.assertEqual(json.loads(settings['model_providers.graf_probe_http.request_max_retries']), 0)
        self.assertEqual(json.loads(settings['model_providers.graf_probe_http.stream_max_retries']), 0)
        self.assertEqual(argv[-1], '-')


class HttpProbePlanAndGateTests(unittest.TestCase):
    def test_plan_requires_freeze_limits_and_matching_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'benchmark').mkdir()
            hashes = {}
            for name in ('benchmark/probe_codex_http.py', 'benchmark/probe_codex_otel.py', 'benchmark/codex_solver.py',
                         'benchmark/codex_mcp_solver.py', 'benchmark/codex_otel.py'):
                source = root / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text(f'frozen source {name}')
                hashes[name] = hashlib.sha256(source.read_bytes()).hexdigest()
            plan = {
                'id': 'codex-otel-probe-v2',
                'scope': 'synthetic_instrumentation_diagnostic_only',
                'cases': list(CASES), 'frozen_before_invocations': True,
                'failure_policy': 'retain_failed_attempts_no_replacement',
                'maximum_cli_invocations': 2, 'timeout_seconds_per_invocation': 60,
                'requested_model': 'gpt-6-luna', 'reasoning_effort': 'low',
                'token_stop_threshold_after_invocation': 50000,
                'implementation_sha256': hashes,
            }
            validate_plan(plan, root / 'benchmark')
            plan['frozen_before_invocations'] = False
            with self.assertRaisesRegex(ValueError, 'frozen'):
                validate_plan(plan, root / 'benchmark')

    def test_second_case_requires_complete_valid_usage_and_telemetry(self):
        valid = {
            'exit_code': 0, 'timeout': False, 'capture_files': ['capture-0001.json'],
            'decode_errors': [], 'collector_errors': [], 'malformed_stream': False,
            'usage_errors': [], 'completed_usage': [{'usage': {
                'input_tokens': 100, 'output_tokens': 50, 'cached_input_tokens': 10,
                'reasoning_output_tokens': 20}}],
            'telemetry_usage_errors': [], 'cli_error_items': 0,
            'telemetry_usage_sum': {'input_token_count': 100, 'output_token_count': 50,
                                    'cached_token_count': 10, 'reasoning_token_count': 20},
        }
        self.assertTrue(second_case_allowed(valid, 150))
        self.assertFalse(second_case_allowed(valid, 149))
        for key, value in (
            ('capture_files', []), ('decode_errors', ['ValueError']),
            ('collector_errors', ['receipt_persist_failed']), ('malformed_stream', True),
            ('usage_errors', ['invalid_input_tokens']),
            ('telemetry_usage_errors', ['missing_completion_usage']), ('cli_error_items', 1),
        ):
            case = {**valid, key: value}
            self.assertFalse(second_case_allowed(case, 50000), key)
        for invalid_usage in (
            {'input_tokens': 100},
            {'input_tokens': -1, 'output_tokens': 0},
            {'input_tokens': 1, 'output_tokens': 0},
            {'input_tokens': 1, 'output_tokens': 0, 'cached_input_tokens': 2},
            {'input_tokens': 1, 'output_tokens': 0, 'reasoning_output_tokens': 1},
        ):
            self.assertFalse(second_case_allowed(
                {**valid, 'completed_usage': [{'usage': invalid_usage}]}, 50000))
        self.assertFalse(second_case_allowed({**valid, 'telemetry_usage_sum': {
            'input_token_count': 50001, 'output_token_count': 0}}, 50000))

    def test_telemetry_usage_sums_every_completion_and_fails_closed(self):
        from probe_codex_http import telemetry_usage
        event = lambda i, o: {'event_name': 'codex.sse_event', 'attributes': {
            'event.kind': 'response.completed', 'input_token_count': i,
            'output_token_count': o, 'cached_token_count': 0, 'reasoning_token_count': 0}}
        total, errors = telemetry_usage([event('2', 3), event(5, 7)])
        self.assertEqual(errors, [])
        self.assertEqual(total['input_token_count'], 7)
        self.assertEqual(total['output_token_count'], 10)
        self.assertEqual(telemetry_usage([event(2, 3), event(None, 7)]),
                         (None, ['invalid_completion_usage']))


if __name__ == '__main__':
    unittest.main()
