"""Conservative, identity-free reconciliation of synthetic Codex telemetry.

This diagnoses observed events. It cannot establish billing, retry completeness,
or a live spend cap and must never authorize a natural benchmark.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path


FIELDS = {
    'input_tokens': 'input_token_count',
    'output_tokens': 'output_token_count',
    'cached_input_tokens': 'cached_token_count',
    'reasoning_output_tokens': 'reasoning_token_count',
}


def integer(value):
    if isinstance(value, bool):
        raise ValueError('boolean token count')
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    if not isinstance(value, int) or value < 0:
        raise ValueError('missing or invalid token count')
    return value


def usage(values, fields):
    result = {key: integer(values.get(source)) for key, source in fields.items()}
    if result['cached_input_tokens'] > result['input_tokens']:
        raise ValueError('cached count exceeds input')
    if result['reasoning_output_tokens'] > result['output_tokens']:
        raise ValueError('reasoning count exceeds output')
    return result


def event_name(event):
    # OTLP eventName can be a Rust tracing source location, not the semantic name.
    return event.get('attributes', {}).get('event.name', event.get('event_name'))


def reconcile(events, stream, broker=None):
    errors = []
    counts = Counter(event_name(e) for e in events)
    requests = []
    completions = []
    seen = set()
    for event in events:
        name = event_name(event)
        attrs = event.get('attributes', {})
        if name == 'codex.websocket_request' or (
            name == 'codex.api_request' and attrs.get('endpoint', '').rstrip('/').endswith('/responses')
        ):
            requests.append(attrs)
        if name != 'codex.sse_event' or attrs.get('event.kind') != 'response.completed':
            continue
        fingerprint = json.dumps(event, sort_keys=True)
        if fingerprint in seen:
            errors.append('duplicate_completion_event')
        seen.add(fingerprint)
        try:
            completions.append(usage(attrs, FIELDS))
        except ValueError:
            errors.append('invalid_completion_usage')
    terminals = [e for e in stream if e.get('type') == 'turn.completed']
    cli = None
    if len(terminals) != 1:
        errors.append('expected_one_cli_usage')
    else:
        try:
            cli = usage(terminals[0].get('usage', {}), {k: k for k in FIELDS})
        except ValueError:
            errors.append('invalid_cli_usage')
    if not completions:
        errors.append('missing_completion_usage')
    if not requests:
        errors.append('missing_model_requests')
    if len(requests) != len(completions):
        errors.append('request_completion_count_mismatch')
    statuses = [a.get('success') for a in requests]
    unknown_success = sum(type(s) is not bool and s not in ('true', 'false') for s in statuses)
    failures = sum(s is False or s == 'false' for s in statuses)
    if unknown_success:
        errors.append('unknown_request_status')
    if failures:
        errors.append('failed_requests_require_retry_audit')
    total = {k: sum(c[k] for c in completions) for k in FIELDS}
    delta = {k: total[k] - cli[k] for k in FIELDS} if cli is not None else None
    if delta is not None and any(delta.values()):
        errors.append('cli_telemetry_usage_mismatch')
    cli_errors = sum(e.get('item', {}).get('type') == 'error' for e in stream)
    if cli_errors:
        errors.append('cli_error_items_require_audit')
    native = [e['item'] for e in stream if e.get('type') == 'item.completed'
              and e.get('item', {}).get('type') == 'mcp_tool_call']
    tools = [r for r in (broker or []) if r.get('request', {}).get('method') == 'tools/call']
    otel_ping = sum(event_name(e) == 'codex.tool_result' and
                    e.get('attributes', {}).get('tool_name') == 'ping' for e in events)
    parity = None
    if broker is not None:
        parity = (len(native) == len(tools) == otel_ping == 1 and
                  native[0].get('server') == 'broker' and native[0].get('tool') == 'ping' and
                  native[0].get('status') == 'completed' and
                  tools[0]['request'].get('params', {}).get('name') == 'ping' and
                  tools[0]['request'].get('params', {}).get('arguments') == {} and
                  tools[0].get('reply', {}).get('result', {}).get('isError') is False and
                  tools[0].get('reply', {}).get('result', {}).get('content') ==
                  [{'type': 'text', 'text': 'pong'}])
        if not parity:
            errors.append('tool_receipt_mismatch')
    return {
        'observed_websocket_requests': counts['codex.websocket_request'],
        'observed_http_requests': counts['codex.api_request'],
        'observed_model_request_events': len(requests),
        'observed_failed_model_request_events': failures,
        'observed_unknown_model_request_statuses': unknown_success,
        'observed_response_completions': len(completions),
        'completion_usage': completions, 'telemetry_usage_sum': total,
        'cli_usage': cli, 'telemetry_minus_cli': delta,
        'native_mcp_completed_calls': len(native), 'broker_tool_calls': len(tools),
        'telemetry_ping_results': otel_ping, 'tool_receipt_parity': parity,
        'telemetry_tool_results': counts['codex.tool_result'],
        'cli_error_items': cli_errors,
        'errors': sorted(set(errors)), 'reconciliation_pass': not errors,
        'request_retry_completeness': 'unproven', 'live_token_cap': 'unavailable',
        'complete_agent_accounting_pass': False, 'product_promotion_pass': False,
        'agent_savings': 'unmeasured',
    }


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def report(root):
    plan = json.loads((root / 'plan.json').read_text())
    checks = {name: sha(Path(name)) == digest
              for name, digest in plan['implementation_sha256'].items()}
    cases = {}
    for case in plan['order']:
        directory = root / case
        receipt = json.loads((directory / 'receipt.json').read_text())
        result = reconcile(json.loads((directory / 'decoded-events.json').read_text()),
                           jsonl(directory / 'stream.jsonl'),
                           jsonl(directory / 'ping.jsonl') if (directory / 'ping.jsonl').exists() else None)
        result['exit_code'] = receipt['exit_code']
        result['timeout'] = receipt['timeout']
        result['capture_count'] = len(receipt['capture_files'])
        result['decode_error_count'] = len(receipt['decode_errors'])
        result['collector_error_count'] = len(receipt['collector_errors'])
        result['evidence_sha256'] = {
            str(p.relative_to(root)): sha(p) for p in sorted(directory.rglob('*')) if p.is_file()
        }
        if (receipt['exit_code'] != 0 or receipt['timeout'] or not receipt['capture_files'] or
                receipt['decode_errors'] or receipt['collector_errors']):
            result['errors'].append('capture_integrity_failed')
            result['reconciliation_pass'] = False
        cases[case] = result
    return {
        'id': plan['id'], 'scope': plan['scope'], 'plan_sha256': sha(root / 'plan.json'),
        'frozen_implementation_hash_checks': checks, 'cases': cases,
        'reconciliation_pass': all(checks.values()) and all(c['reconciliation_pass'] for c in cases.values()),
        'billing_or_quota_impact': 'unknown', 'complete_agent_accounting_pass': False,
        'product_promotion_pass': False, 'agent_savings': 'unmeasured',
        'limitations': ['Successful synthetic probes do not prove retry or failure-path completeness.',
                       'Token sums describe observed completion events, not verified billed usage.',
                       'No enforceable live token cap; no natural tasks or comparative Graphify run.',
                       'First zero-output completion is retained; attributing it to prewarm is an inference.'],
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = report(args.root)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    raise SystemExit(0 if result['reconciliation_pass'] else 1)
