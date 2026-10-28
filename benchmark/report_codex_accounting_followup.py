"""Identity-free reports for synthetic Codex HTTP accounting follow-ups."""
import argparse
import hashlib
import json
from pathlib import Path

from codex_otel_accounting import FIELDS, event_name, jsonl, reconcile, sha, usage


HTTP_PLAN_ID = 'codex-otel-probe-v2'
HTTP_CASES = ['plain_http', 'one_synthetic_mcp_ping_http']
RETRY_PLAN_ID = 'codex-local-retry-v1'
RETRY_DIR = 'local_retry'
STATUS_FIELDS = ('http.response.status_code', 'http_status_code', 'status_code', 'status')


def _read(path):
    with path.open() as stream:
        return json.load(stream)


def _valid_hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def _plan(root, expected_id, expected_cases=None):
    path = root / 'plan.json'
    plan = _read(path)
    errors = []
    if not isinstance(plan, dict) or plan.get('id') != expected_id:
        errors.append('unexpected_plan_id')
        plan = plan if isinstance(plan, dict) else {}
    if plan.get('scope') != 'synthetic_instrumentation_diagnostic_only':
        errors.append('unexpected_plan_scope')
    if expected_cases is not None and plan.get('cases') != expected_cases:
        errors.append('unexpected_case_order')
    hashes = plan.get('implementation_sha256')
    checks = {}
    if not isinstance(hashes, dict) or not hashes:
        errors.append('missing_implementation_hashes')
        hashes = {}
    for name, expected in hashes.items():
        # Hash names must resolve inside the repository and refer to a file.
        candidate = (Path(__file__).resolve().parent.parent / name).resolve()
        try:
            safe = candidate.is_relative_to(Path(__file__).resolve().parent.parent)
        except AttributeError:  # pragma: no cover - compatibility with older Python
            safe = str(candidate).startswith(str(Path(__file__).resolve().parent.parent) + '/')
        checks[str(name)] = bool(safe and candidate.is_file() and _valid_hash(expected)
                                 and sha(candidate) == expected)
    if not checks or not all(checks.values()):
        errors.append('implementation_hash_mismatch')
    return plan, sha(path), checks, errors


def _evidence_hashes(directory, root):
    return {str(p.relative_to(root)): sha(p) for p in sorted(directory.rglob('*')) if p.is_file()}


def _error_list(receipt, key):
    value = receipt.get(key)
    return value if isinstance(value, list) else ['invalid_' + key]


def _http_case(root, name, plan_sha):
    directory = root / name
    result = {'status': 'not_run', 'case': name, 'errors': []}
    if not directory.is_dir():
        return result
    result['status'] = 'failed'
    try:
        receipt = _read(directory / 'receipt.json')
        events = _read(directory / 'decoded-events.json')
        stream = jsonl(directory / 'stream.jsonl')
        ping_path = directory / 'ping.jsonl'
        broker = jsonl(ping_path) if ping_path.is_file() else None
        if not isinstance(receipt, dict) or not isinstance(events, list) or not isinstance(stream, list):
            raise ValueError('invalid_receipt_or_events')
        audit = reconcile(events, stream, broker)
        completion_events = [event for event in events if event_name(event) == 'codex.sse_event' and
                             event.get('attributes', {}).get('event.kind') == 'response.completed']
        valid_completion_usage = []
        no_token_markers = 0
        for event in completion_events:
            attrs = event.get('attributes', {})
            if not any(field in attrs for field in FIELDS.values()):
                no_token_markers += 1
                continue
            try:
                valid_completion_usage.append(usage(attrs, FIELDS))
            except (ValueError, TypeError):
                pass
        observed_valid_sum = ({key: sum(item[key] for item in valid_completion_usage) for key in FIELDS}
                              if valid_completion_usage else None)
        errors = list(audit['errors'])
        if receipt.get('plan_sha256') != plan_sha:
            errors.append('receipt_plan_hash_mismatch')
        if receipt.get('exit_code') != 0:
            errors.append('nonzero_or_missing_exit_code')
        if receipt.get('timeout') is not False:
            errors.append('timeout_or_unknown')
        if not isinstance(receipt.get('capture_files'), list) or not receipt['capture_files']:
            errors.append('missing_captures')
            capture_present = False
        else:
            capture_present = all(isinstance(name, str) and Path(name).name == name and
                                  len(list(directory.rglob(name))) == 1 for name in receipt['capture_files'])
            if not capture_present:
                errors.append('capture_integrity_failed')
        if receipt.get('case') != name:
            errors.append('receipt_case_mismatch')
        for field in ('decode_errors', 'collector_errors', 'usage_errors'):
            if _error_list(receipt, field):
                errors.append(field)
        if receipt.get('malformed_stream') is not False:
            errors.append('malformed_or_unknown_stream')
        if receipt.get('execution_error') is not None:
            errors.append('execution_error')
        if audit['observed_websocket_requests'] != 0:
            errors.append('websocket_request_observed')
        # The HTTP v2 probes are expected to reconcile one completion per request.
        if not audit['reconciliation_pass']:
            errors.extend(audit['errors'])
        if name == HTTP_CASES[1] and audit['tool_receipt_parity'] is not True:
            errors.append('synthetic_ping_receipt_mismatch')
        statuses = [event.get('attributes', {}) for event in events if event_name(event) == 'codex.api_request' and
                    isinstance(event.get('attributes', {}).get('endpoint'), str) and
                    event.get('attributes', {}).get('endpoint').rstrip('/').endswith('/responses')]
        missing_success = sum('success' not in attrs for attrs in statuses)
        if missing_success:
            errors.append('missing_request_success_status')
        cli_matches_valid = (observed_valid_sum == audit['cli_usage'] if observed_valid_sum is not None else None)
        result.update({
            'status': 'passed' if not errors else 'failed',
            'errors': sorted(set(errors)),
            'exit_code': receipt.get('exit_code'), 'timeout': receipt.get('timeout'),
            'capture_count': len(receipt.get('capture_files', [])) if isinstance(receipt.get('capture_files'), list) else None,
            'capture_files_present': capture_present,
            'decode_error_count': len(receipt.get('decode_errors', [])) if isinstance(receipt.get('decode_errors'), list) else None,
            'collector_error_count': len(receipt.get('collector_errors', [])) if isinstance(receipt.get('collector_errors'), list) else None,
            'usage_error_count': len(receipt.get('usage_errors', [])) if isinstance(receipt.get('usage_errors'), list) else None,
            'observed_http_requests': audit['observed_http_requests'],
            'observed_websocket_requests': audit['observed_websocket_requests'],
            'observed_model_request_events': audit['observed_model_request_events'],
            'observed_response_completions': audit['observed_response_completions'],
            'response_completed_event_records': len(completion_events),
            'valid_usage_bearing_completion_records': len(valid_completion_usage),
            'no_token_field_marker_records': no_token_markers,
            'invalid_usage_bearing_completion_records': len(completion_events) - len(valid_completion_usage) - no_token_markers,
            'observed_valid_completion_usage_sum': observed_valid_sum,
            'cli_usage_matches_observed_valid_completion_sum': cli_matches_valid,
            'complete_usage_verified': not errors,
            'missing_request_success_status_count': missing_success,
            'cli_error_item_count': audit['cli_error_items'], 'cli_usage': audit['cli_usage'],
            'telemetry_minus_cli': audit['telemetry_minus_cli'],
            'native_mcp_completed_calls': audit['native_mcp_completed_calls'],
            'broker_tool_calls': audit['broker_tool_calls'], 'telemetry_ping_results': audit['telemetry_ping_results'],
            'tool_receipt_parity': audit['tool_receipt_parity'],
            'evidence_sha256': _evidence_hashes(directory, root),
        })
    except (OSError, ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError):
        result['errors'] = ['missing_or_corrupt_case_evidence']
        result['evidence_sha256'] = _evidence_hashes(directory, root)
    return result


def http_report(root):
    plan, plan_sha, hashes, plan_errors = _plan(root, HTTP_PLAN_ID, HTTP_CASES)
    cases = {name: _http_case(root, name, plan_sha) for name in HTTP_CASES}
    if cases[HTTP_CASES[0]]['status'] != 'passed' and cases[HTTP_CASES[1]]['status'] == 'not_run':
        cases[HTTP_CASES[1]]['reason'] = 'first_case_failed_frozen_gate'
    passed = not plan_errors and all(case['status'] == 'passed' for case in cases.values())
    return {'id': HTTP_PLAN_ID, 'scope': 'synthetic_http_probe_diagnostic_only',
            'plan_sha256': plan_sha, 'commands_and_artifacts_sha256': hashes,
            'plan_errors': plan_errors, 'cases': cases, 'probe_pass': passed,
            'complete_agent_accounting_pass': False, 'live_token_cap': 'unavailable',
            'product_promotion_pass': False, 'agent_savings': 'unmeasured', 'billing': 'unknown'}


def _strict_int(value):
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    return value if type(value) is int and value >= 0 else None


def _status(attrs):
    for key in STATUS_FIELDS:
        if key in attrs:
            return _strict_int(attrs[key])
    return None


def _safe_stub_entry(entry):
    if not isinstance(entry, dict):
        return None
    method, path = entry.get('method'), entry.get('path')
    status = _strict_int(entry.get('status'))
    size = _strict_int(entry.get('body_size'))
    digest = entry.get('body_sha256')
    auth = entry.get('auth_present')
    if (not isinstance(method, str) or not isinstance(path, str) or status is None or size is None
            or not _valid_hash(digest) or type(auth) is not bool):
        return None
    return {'method': method, 'path': path, 'status': status, 'body_size': size,
            'body_sha256': digest, 'auth_present': auth}


def retry_report(root):
    plan, plan_sha, hashes, plan_errors = _plan(root, RETRY_PLAN_ID)
    directory = root / RETRY_DIR
    errors = list(plan_errors)
    result = {'id': RETRY_PLAN_ID, 'scope': 'local_synthetic_failure_path_observation_only',
              'plan_sha256': plan_sha, 'commands_and_artifacts_sha256': hashes,
              'plan_errors': plan_errors, 'status': 'failed'}
    try:
        receipt = _read(directory / 'receipt.json')
        events = _read(directory / 'decoded-events.json')
        stream = jsonl(directory / 'stream.jsonl')
        candidates = sorted(directory.rglob('requests.jsonl'))
        if len(candidates) != 1:
            errors.append('missing_or_ambiguous_stub_receipt')
            stub_raw = []
        else:
            stub_raw = jsonl(candidates[0])
        stub = [_safe_stub_entry(item) for item in stub_raw]
        if any(item is None for item in stub):
            errors.append('invalid_stub_receipt')
        safe_stub = [item for item in stub if item is not None]
        response_receipts = [item for item in safe_stub
                             if item['method'] == 'POST' and item['path'] == '/v1/responses']
        receipt_ok = (len(response_receipts) == 2 and
                      all(x['status'] == 503 for x in response_receipts) and
                      all(x['auth_present'] is False for x in safe_stub))
        if not receipt_ok:
            errors.append('stub_receipt_expectation_mismatch')
        if receipt.get('plan_sha256') != plan_sha:
            errors.append('receipt_plan_hash_mismatch')
        exit_code = receipt.get('exit_code')
        if type(exit_code) is not int or exit_code <= 0 or receipt.get('timeout') is not False:
            errors.append('cli_failure_path_not_observed')
        if receipt.get('launch_error') is not None:
            errors.append('launch_error')
        captures = receipt.get('capture_files')
        if not isinstance(captures, list) or not captures:
            errors.append('missing_captures')
        elif not all(isinstance(name, str) and Path(name).name == name and
                     len(list(directory.rglob(name))) == 1 for name in captures):
            errors.append('capture_integrity_failed')
        if receipt.get('malformed_stream') is not False:
            errors.append('malformed_or_unknown_stream')
        for field in ('decode_errors', 'collector_errors'):
            if _error_list(receipt, field):
                errors.append(field)
        if not isinstance(events, list) or not isinstance(stream, list):
            raise ValueError('invalid_retry_events')
        if (any(e.get('type') == 'turn.completed' for e in stream) or
                any(event_name(e) == 'codex.sse_event' and
                    e.get('attributes', {}).get('event.kind') == 'response.completed' for e in events)):
            errors.append('successful_completion_usage_observed')
        if any(event_name(e) == 'codex.websocket_request' for e in events):
            errors.append('websocket_request_observed')
        responses = []
        for event in events:
            if event_name(event) != 'codex.api_request':
                continue
            attrs = event.get('attributes', {})
            endpoint = attrs.get('endpoint')
            if not isinstance(endpoint, str) or not endpoint.rstrip('/').endswith('/responses'):
                continue
            attempt = _strict_int(attrs.get('attempt'))
            status = _status(attrs)
            success = attrs.get('success')
            responses.append({'attempt': attempt, 'status': status, 'success': success if type(success) is bool else None})
        expected = [{'attempt': 0, 'status': 503, 'success': False},
                    {'attempt': 1, 'status': 503, 'success': False}]
        if len(responses) != 2 or sorted(responses, key=lambda x: -1 if x['attempt'] is None else x['attempt']) != expected:
            errors.append('telemetry_attempts_do_not_match_stub')
        if any(event_name(e) == 'codex.api_request' and
               isinstance(e.get('attributes', {}).get('endpoint'), str) and
               e.get('attributes', {}).get('endpoint').rstrip('/').endswith('/responses') and
               _status(e.get('attributes', {})) is None for e in events):
            errors.append('unknown_response_status')
        result.update({'stub_requests': safe_stub, 'response_stub_receipts': response_receipts,
                       'telemetry_response_attempts': responses,
                       'cli_exit_code': receipt.get('exit_code'), 'timeout': receipt.get('timeout'),
                       'decode_error_count': len(receipt.get('decode_errors', [])) if isinstance(receipt.get('decode_errors'), list) else None,
                       'collector_error_count': len(receipt.get('collector_errors', [])) if isinstance(receipt.get('collector_errors'), list) else None,
                       'usage': 'unavailable', 'errors': sorted(set(errors)),
                       'evidence_sha256': _evidence_hashes(directory, root)})
    except (OSError, ValueError, TypeError, KeyError, AttributeError, json.JSONDecodeError):
        errors.append('missing_or_corrupt_retry_evidence')
        result.update({'errors': sorted(set(errors)), 'evidence_sha256': _evidence_hashes(directory, root),
                       'usage': 'unavailable'})
    result['retryfailurepath_pass'] = not result.get('errors')
    result['status'] = 'passed' if result['retryfailurepath_pass'] else 'failed'
    result.update({'complete_agent_accounting_pass': False, 'live_token_cap': 'unavailable',
                   'product_promotion_pass': False, 'agent_savings': 'unmeasured', 'billing': 'unknown'})
    return result


def report(http_root, retry_root):
    http = http_report(http_root)
    retry = retry_report(retry_root)
    return {'http_probe': http, 'local_retry_probe': retry,
            'probe_pass': http['probe_pass'] and retry['retryfailurepath_pass'],
            'complete_agent_accounting_pass': False, 'live_token_cap': 'unavailable',
            'product_promotion_pass': False, 'agent_savings': 'unmeasured', 'billing': 'unknown'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--http-root', type=Path, required=True)
    parser.add_argument('--retry-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    result = report(args.http_root, args.retry_root)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    return 0 if result['probe_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
