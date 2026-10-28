import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from report_codex_accounting_followup import http_report, retry_report


BASE = Path(__file__).resolve().parent.parent


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + '\n')


def event(name, **attrs):
    return {'event_name': 'untrusted outer event name',
            'attributes': {'event.name': name, **attrs}}


def http_fixture(root, case, *, duplicate=False, bad_receipt_hash=False):
    directory = root / case
    usage = {'input_tokens': 12, 'output_tokens': 3,
             'cached_input_tokens': 2, 'reasoning_output_tokens': 1}
    telemetry_usage = {'input_token_count': 12, 'output_token_count': 3,
                       'cached_token_count': 2, 'reasoning_token_count': 1}
    events = [event('codex.api_request', endpoint='https://local/v1/responses', success=True)]
    events.append(event('codex.sse_event', **{'event.kind': 'response.completed', **telemetry_usage}))
    stream = [{'type': 'turn.completed', 'usage': usage}]
    if duplicate:
        events.insert(1, dict(events[0]))
    if case == 'one_synthetic_mcp_ping_http':
        events.append(event('codex.tool_result', tool_name='ping'))
        stream.append({'type': 'item.completed', 'item': {'type': 'mcp_tool_call',
                       'server': 'broker', 'tool': 'ping', 'status': 'completed'}})
        dump(directory / 'ping.jsonl', {'request': {'method': 'tools/call', 'params': {
            'name': 'ping', 'arguments': {}}}, 'reply': {'result': {'isError': False,
            'content': [{'type': 'text', 'text': 'pong'}]}}})
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'stream.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in stream))
    dump(directory / 'decoded-events.json', events)
    (directory / 'capture-a.json').write_text('{}')
    plan_sha = hashlib.sha256(b'plan').hexdigest()
    receipt = {'case': case, 'plan_sha256': '0' * 64 if bad_receipt_hash else plan_sha,
               'exit_code': 0, 'timeout': False, 'execution_error': None,
               'malformed_stream': False, 'completed_usage': [{'usage': usage}],
               'usage_errors': [], 'capture_files': ['capture-a.json'],
               'decode_errors': [], 'collector_errors': []}
    dump(directory / 'receipt.json', receipt)
    return plan_sha


def http_root(root, cases, *, bad_hash=False):
    hashes = {'benchmark/codex_otel_accounting.py': digest(BASE / 'benchmark/codex_otel_accounting.py')}
    if bad_hash:
        hashes['benchmark/codex_otel_accounting.py'] = '0' * 64
    dump(root / 'plan.json', {'id': 'codex-otel-probe-v2',
          'scope': 'synthetic_instrumentation_diagnostic_only',
          'cases': ['plain_http', 'one_synthetic_mcp_ping_http'],
          'implementation_sha256': hashes})
    plan_sha = digest(root / 'plan.json')
    for case in cases:
        # Build fixtures after the plan is written, then patch each receipt's digest.
        http_fixture(root, case)
        receipt_path = root / case / 'receipt.json'
        receipt = json.loads(receipt_path.read_text())
        receipt['plan_sha256'] = plan_sha
        dump(receipt_path, receipt)
    return plan_sha


def retry_fixture(root, *, status=503, attempts=(0, 1), ancillary=False, auth_on_ancillary=False,
                  captures=True, usage=False):
    directory = root / 'local_retry'
    dump(root / 'plan.json', {'id': 'codex-local-retry-v1',
          'scope': 'synthetic_instrumentation_diagnostic_only',
          'implementation_sha256': {'benchmark/codex_otel_accounting.py':
                                    digest(BASE / 'benchmark/codex_otel_accounting.py')}})
    plan_sha = digest(root / 'plan.json')
    request = {'method': 'POST', 'path': '/v1/responses', 'status': 503,
               'body_size': 10, 'body_sha256': hashlib.sha256(b'1234567890').hexdigest(),
               'auth_present': False}
    stub = [request, dict(request)]
    if ancillary:
        stub.insert(0, {'method': 'GET', 'path': '/models', 'status': 404,
                        'body_size': 0, 'body_sha256': hashlib.sha256(b'').hexdigest(),
                        'auth_present': auth_on_ancillary})
    (directory / 'requests.jsonl').parent.mkdir(parents=True, exist_ok=True)
    (directory / 'requests.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in stub))
    events = [event('codex.api_request', endpoint='/v1/responses', attempt=a,
                    http_response_status_code=status, success=False) for a in attempts]
    # This explicit alias is accepted by the report's allowlist.
    for e in events:
        e['attributes']['http.response.status_code'] = e['attributes'].pop('http_response_status_code')
    dump(directory / 'decoded-events.json', events)
    stream = [{'type': 'turn.completed', 'usage': {}}] if usage else []
    (directory / 'stream.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in stream))
    if captures:
        (directory / 'capture.json').write_text('{}')
    dump(directory / 'receipt.json', {'plan_sha256': plan_sha, 'exit_code': 1,
          'timeout': False, 'launch_error': None,
          'capture_files': ['capture.json'] if captures else [], 'malformed_stream': False,
          'decode_errors': [], 'collector_errors': []})


class FollowupReportTests(unittest.TestCase):
    def test_http_probe_pass_requires_both_cases_and_ping_parity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            http_root(root, ['plain_http', 'one_synthetic_mcp_ping_http'])
            report = http_report(root)
            self.assertTrue(report['probe_pass'])
            self.assertFalse(report['complete_agent_accounting_pass'])
            self.assertTrue(report['cases']['one_synthetic_mcp_ping_http']['tool_receipt_parity'])
            self.assertNotIn('event_name', str(report))

    def test_missing_case_is_not_run_and_blocks_overall_pass(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            http_root(root, ['plain_http'])
            receipt_path = root / 'plain_http' / 'receipt.json'
            receipt = json.loads(receipt_path.read_text())
            receipt['plan_sha256'] = '0' * 64
            dump(receipt_path, receipt)
            report = http_report(root)
            self.assertEqual(report['cases']['one_synthetic_mcp_ping_http']['status'], 'not_run')
            self.assertEqual(report['cases']['one_synthetic_mcp_ping_http']['reason'],
                             'first_case_failed_frozen_gate')
            self.assertFalse(report['probe_pass'])

    def test_marker_completion_and_missing_request_status_are_reported_without_zero_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            http_root(root, ['plain_http', 'one_synthetic_mcp_ping_http'])
            events_path = root / 'plain_http' / 'decoded-events.json'
            events = json.loads(events_path.read_text())
            events[0]['attributes'].pop('success')
            events.append(event('codex.sse_event', **{'event.kind': 'response.completed'}))
            dump(events_path, events)
            stream_path = root / 'plain_http' / 'stream.jsonl'
            stream = [json.loads(line) for line in stream_path.read_text().splitlines()]
            stream.append({'type': 'item.completed', 'item': {'type': 'error'}})
            stream_path.write_text(''.join(json.dumps(item) + '\n' for item in stream))
            case = http_report(root)['cases']['plain_http']
            self.assertEqual(case['response_completed_event_records'], 2)
            self.assertEqual(case['valid_usage_bearing_completion_records'], 1)
            self.assertEqual(case['no_token_field_marker_records'], 1)
            self.assertEqual(case['missing_request_success_status_count'], 1)
            self.assertEqual(case['cli_error_item_count'], 1)
            self.assertEqual(case['observed_valid_completion_usage_sum'], {
                'input_tokens': 12, 'output_tokens': 3, 'cached_input_tokens': 2,
                'reasoning_output_tokens': 1})
            self.assertFalse(case['complete_usage_verified'])

    def test_http_duplicate_requests_bad_receipt_hash_and_bad_source_hash_fail(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            http_root(root, ['plain_http', 'one_synthetic_mcp_ping_http'])
            case_receipt = root / 'plain_http' / 'receipt.json'
            receipt = json.loads(case_receipt.read_text())
            receipt['plan_sha256'] = '0' * 64
            dump(case_receipt, receipt)
            events_path = root / 'plain_http' / 'decoded-events.json'
            events = json.loads(events_path.read_text())
            events.insert(1, dict(events[0]))
            dump(events_path, events)
            report = http_report(root)
            self.assertFalse(report['probe_pass'])
            self.assertIn('receipt_plan_hash_mismatch', report['cases']['plain_http']['errors'])
            self.assertIn('request_completion_count_mismatch', report['cases']['plain_http']['errors'])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            http_root(root, ['plain_http', 'one_synthetic_mcp_ping_http'], bad_hash=True)
            report = http_report(root)
            self.assertFalse(report['probe_pass'])
            self.assertIn('implementation_hash_mismatch', report['plan_errors'])

    def test_retry_observation_requires_two_matching_attempts_and_unknown_status_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            retry_fixture(root)
            report = retry_report(root)
            self.assertTrue(report['retryfailurepath_pass'])
            self.assertEqual([x['attempt'] for x in report['telemetry_response_attempts']], [0, 1])
            self.assertEqual(report['usage'], 'unavailable')
            self.assertFalse(report['complete_agent_accounting_pass'])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            retry_fixture(root, status='unknown')
            report = retry_report(root)
            self.assertFalse(report['retryfailurepath_pass'])
            self.assertIn('unknown_response_status', report['errors'])

    def test_retry_filters_expected_posts_but_preserves_and_checks_other_stub_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            retry_fixture(root, ancillary=True)
            report = retry_report(root)
            self.assertTrue(report['retryfailurepath_pass'])
            self.assertEqual(len(report['stub_requests']), 3)
            self.assertEqual(len(report['response_stub_receipts']), 2)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            retry_fixture(root, ancillary=True, auth_on_ancillary=True)
            report = retry_report(root)
            self.assertFalse(report['retryfailurepath_pass'])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            retry_fixture(root, captures=False)
            report = retry_report(root)
            self.assertFalse(report['retryfailurepath_pass'])
            self.assertIn('missing_captures', report['errors'])

    def test_retry_missing_usage_receipt_and_ambiguous_stub_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            retry_fixture(root, usage=True)
            report = retry_report(root)
            self.assertFalse(report['retryfailurepath_pass'])
            self.assertIn('successful_completion_usage_observed', report['errors'])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            retry_fixture(root)
            duplicate_path = root / 'local_retry' / 'nested' / 'requests.jsonl'
            duplicate_path.parent.mkdir()
            duplicate_path.write_text((root / 'local_retry' / 'requests.jsonl').read_text())
            report = retry_report(root)
            self.assertFalse(report['retryfailurepath_pass'])
            self.assertIn('missing_or_ambiguous_stub_receipt', report['errors'])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            retry_fixture(root, attempts=(0, 0))
            report = retry_report(root)
            self.assertFalse(report['retryfailurepath_pass'])
            self.assertIn('telemetry_attempts_do_not_match_stub', report['errors'])


if __name__ == '__main__':
    unittest.main()
