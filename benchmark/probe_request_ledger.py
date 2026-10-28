"""Frozen loopback-only request-ledger diagnostic; never invokes a model."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
from urllib.parse import urlsplit

from request_ledger import Ledger
from request_gateway import LocalGateway

BASE = Path(__file__).resolve().parent.parent
CASES = ['success', 'http_retry', 'interrupted_stream', 'budget_denial',
         'unknown_bound', 'usage_violation', 'concurrent_admission']
REQUIRED = ['benchmark/request_ledger.py', 'benchmark/request_gateway.py',
            'benchmark/probe_request_ledger.py', 'benchmark/test_request_ledger.py',
            'benchmark/test_request_gateway.py']
BODY = b'{"model":"synthetic-fixture","input":"OK","max_output_tokens":16}'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def response(input_tokens=8):
    return json.dumps({'status': 'completed', 'usage': {
        'input_tokens': input_tokens, 'output_tokens': 3,
        'total_tokens': input_tokens + 3,
        'input_tokens_details': {'cached_tokens': 0},
        'output_tokens_details': {'reasoning_tokens': 0},
    }}).encode()


class FixtureServer:
    def __init__(self, directory, case):
        self.directory, self.case = directory, case
        self.requests = []
        self.lock = threading.Lock()

    def __enter__(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                with owner.lock:
                    index = len(owner.requests)
                    record = {'index': index, 'path': self.path,
                              'body_sha256': sha(body), 'body_bytes': len(body),
                              'auth_present': self.headers.get('Authorization') is not None}
                    owner.requests.append(record)
                    with (owner.directory / 'upstream-receipts.jsonl').open('a') as out:
                        out.write(json.dumps(record) + '\n')
                        out.flush()
                        os.fsync(out.fileno())
                status, content_type, payload = 200, 'application/json', response()
                if owner.case == 'http_retry' and index == 0:
                    status, payload = 503, b'{"error":"synthetic failure"}'
                elif owner.case == 'interrupted_stream':
                    content_type = 'text/event-stream'
                    payload = b'data: {"type":"response.created"}\n\n'
                elif owner.case == 'usage_violation':
                    payload = response(9)  # Exceeds the independently supplied fixture bound.
                (owner.directory / f'upstream-response-{index}.bin').write_bytes(payload)
                self.send_response(status)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}/v1/responses'
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def client(endpoint, directory, index):
    parts = urlsplit(endpoint)
    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=10)
    try:
        conn.request('POST', parts.path.rstrip('/') + '/responses', BODY,
                     {'Content-Type': 'application/json'})
        result = conn.getresponse()
        data = result.read()
        (directory / f'client-response-{index}.bin').write_bytes(data)
        return {'status': result.status, 'body_sha256': sha(data), 'body_bytes': len(data)}
    finally:
        conn.close()


def run(plan_path, output):
    if output.exists():
        raise FileExistsError(output)
    plan = json.loads(plan_path.read_text())
    if (plan.get('id') != 'request-ledger-local-v1' or plan.get('cases') != CASES
            or plan.get('scope') != 'synthetic_fixture_only'
            or plan.get('frozen_before_invocations') is not True
            or plan.get('request_sha256') != sha(BODY)
            or plan.get('limits') != {'default_tokens': 96, 'default_attempts': 4,
                                     'denial_tokens': 23, 'concurrent_tokens': 24}):
        raise ValueError('plan does not match the fixed diagnostic')
    pinned = plan.get('implementation_sha256', {})
    checks = {n: sha((BASE / n).read_bytes()) == pinned.get(n) for n in REQUIRED}
    if not all(checks.values()):
        raise ValueError('frozen source hash mismatch')
    root = plan_path.resolve().parent / 'run'
    root.mkdir(mode=0o700)  # No replacement or overwritten diagnostic cases.
    cases = {}
    for case in CASES:
        directory = root / case
        directory.mkdir(mode=0o700)
        (directory / 'request.bin').write_bytes(BODY)
        limit = 23 if case == 'budget_denial' else 24 if case == 'concurrent_admission' else 96
        ledger = Ledger(directory / 'ledger.jsonl', token_limit=limit, attempt_limit=4)
        bounds = {} if case == 'unknown_bound' else {sha(BODY): {
            'source': 'synthetic_fixture', 'request_sha256': sha(BODY),
            'model': 'synthetic-fixture', 'input_tokens': 8}}
        with FixtureServer(directory, case) as upstream:
            with LocalGateway(ledger, upstream.url, bounds) as gateway:
                if case == 'concurrent_admission':
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        replies = list(pool.map(lambda i: client(gateway.endpoint, directory, i), range(2)))
                else:
                    replies = [client(gateway.endpoint, directory, 0)]
                    if case == 'http_retry':
                        replies.append(client(gateway.endpoint, directory, 1))
        summary = ledger.summary()
        count = len(upstream.requests)
        expected_count = 0 if case in ('budget_denial', 'unknown_bound') else 2 if case == 'http_retry' else 1
        expected_reserve = expected_count * 24
        checks_case = {
            'upstream_count_matches': count == expected_count,
            'all_upstream_bodies_match': all(r['body_sha256'] == sha(BODY) for r in upstream.requests),
            'no_auth_forwarded': all(not r['auth_present'] for r in upstream.requests),
            'reservation_matches': summary['reserved_tokens'] == expected_reserve,
            'within_fixture_budget': summary['reserved_tokens'] <= limit,
            'no_pending_attempts': summary['pending_attempts'] == 0,
            'all_request_bodies_receipted': summary['request_count'] == len(replies),
            'accepted_matches_upstream': summary['accepted_attempts'] == count,
        }
        if case in ('http_retry', 'interrupted_stream', 'usage_violation'):
            checks_case['failed_usage_remains_unknown'] = summary['unknown_usage_attempts'] >= 1
        if case == 'usage_violation':
            checks_case['usage_violation_poisons_ledger'] = summary['poisoned'] is True
        if case in ('budget_denial', 'unknown_bound'):
            checks_case['client_denied'] = replies[0]['status'] >= 400
        if case == 'concurrent_admission':
            checks_case['one_concurrent_request_denied'] = sum(r['status'] >= 400 for r in replies) == 1
        if case in ('success', 'http_retry', 'concurrent_admission'):
            checks_case['one_successful_reply'] = sum(r['status'] == 200 for r in replies) == 1
            checks_case['observed_usage_exact'] = summary['actual_observed_tokens'] == 11
        ledger.close()
        journal = [json.loads(line) for line in (directory / 'ledger.jsonl').read_text().splitlines()]
        admitted = [e for e in journal if e['event'] == 'admitted']
        denied = [e for e in journal if e['event'] == 'denied']
        finished = [e for e in journal if e['event'] == 'finish']
        outcomes = {'http_retry': ['http_error', 'completed'],
                    'interrupted_stream': ['interrupted'],
                    'budget_denial': [], 'unknown_bound': []}.get(case, ['completed'])
        checks_case.update({
            'durable_config': journal[0]['event'] == 'config',
            'durable_admissions_exact': len(admitted) == expected_count,
            'durable_denials_exact': len(denied) == len(replies) - expected_count,
            'durable_request_numbers': sorted(e['request_number'] for e in admitted + denied) == list(range(1, len(replies) + 1)),
            'durable_reservation_exact': sum(e['reserved_tokens'] for e in admitted) == expected_reserve,
            'durable_each_reservation_exact': all(e['reserved_tokens'] == 24 for e in admitted),
            'durable_request_hashes': all(e['request_sha256'] == sha(BODY) for e in admitted + denied),
            'durable_outcomes_exact': [e['outcome'] for e in finished] == outcomes,
            'durable_attempt_ids_match': sorted(e['attempt_id'] for e in admitted) == sorted(e['attempt_id'] for e in finished),
            'durable_attempt_ids_unique': sorted(e['attempt_id'] for e in admitted) == list(range(1, expected_count + 1)),
            'no_unexpected_journal_events': len(journal) == 1 + len(replies) + expected_count,
        })
        if case in ('budget_denial', 'concurrent_admission', 'unknown_bound'):
            reason = 'missing_input_bound' if case == 'unknown_bound' else 'token_limit'
            checks_case['durable_denial_reason'] = [e['reason'] for e in denied] == [reason]
        if case == 'usage_violation':
            checks_case['durable_invalid_usage'] = len(finished) == 1 and bool(finished[0].get('invalid_usage'))
        cases[case] = {'summary': summary, 'upstream_requests': upstream.requests,
                       'client_receipts': replies, 'checks': checks_case,
                       'fixture_diagnostic_pass': all(checks_case.values()),
                       'evidence_sha256': {str(p.relative_to(BASE)): sha(p.read_bytes())
                                           for p in sorted(directory.rglob('*')) if p.is_file()}}
    result = {'id': plan['id'], 'scope': plan['scope'], 'plan_sha256': sha(plan_path.read_bytes()),
              'implementation_hash_checks': checks, 'cases': cases,
              'fixture_diagnostic_pass': all(c['fixture_diagnostic_pass'] for c in cases.values()),
              'live_provider_invocations': 0, 'complete_agent_accounting_pass': False,
              'live_token_cap': 'real_provider_unverified', 'agent_savings': 'unmeasured',
              'product_promotion_pass': False}
    with output.open('x') as out:
        out.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'fixture_diagnostic_pass': result['fixture_diagnostic_pass'],
                      'complete_agent_accounting_pass': False}))
    return 0 if result['fixture_diagnostic_pass'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.plan, args.output))
