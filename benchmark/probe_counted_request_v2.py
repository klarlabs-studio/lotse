"""Frozen offline count-before-generation protocol diagnostic; no network."""
import argparse
import hashlib
import json
from pathlib import Path

from counted_request import CountedFixtureSession

BASE = Path(__file__).resolve().parent.parent
CASES = ['success', 'count_http_error', 'count_invalid', 'generation_retry',
         'output_cap', 'input_mismatch', 'missing_usage', 'count_limit', 'budget_denial']
SOURCES = ['benchmark/counted_request.py', 'benchmark/test_counted_request.py',
           'benchmark/probe_counted_request_v2.py', 'benchmark/request_ledger.py']
BODY = b'{"model":"synthetic-fixture","input":"OK","instructions":"Be concise.","max_output_tokens":16}'
PROJECTION = {'model': 'synthetic-fixture', 'input': 'OK', 'instructions': 'Be concise.'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Fixture:
    def __init__(self, directory, case):
        self.directory, self.case, self.calls = directory, case, []
        self.generation_count = 0

    def __call__(self, path, body):
        index = len(self.calls)
        status = 200
        if path == '/v1/responses/input_tokens':
            payload = {'object': 'response.input_tokens', 'input_tokens': 8}
            if self.case == 'count_http_error':
                status, payload = 503, {'error': 'synthetic count failure'}
            elif self.case == 'count_invalid':
                payload['input_tokens'] = True
        elif path == '/v1/responses':
            self.generation_count += 1
            inputs, outputs = (9 if self.case == 'input_mismatch' else 8), 3
            if self.case == 'output_cap':
                outputs = 16
            payload = {'status': 'completed', 'usage': {
                'input_tokens': inputs, 'output_tokens': outputs,
                'total_tokens': inputs + outputs,
                'output_tokens_details': {'reasoning_tokens': outputs},
            }}
            if self.case == 'output_cap':
                payload.update(status='incomplete', incomplete_details={'reason': 'max_output_tokens'})
            if self.case == 'missing_usage':
                payload.pop('usage')
            if self.case == 'generation_retry' and self.generation_count == 1:
                status, payload = 503, {'error': 'synthetic generation failure'}
        else:
            raise AssertionError('unexpected transport path')
        raw = json.dumps(payload, sort_keys=True).encode()
        count_journal = [json.loads(line) for line in (self.directory / 'count-journal.jsonl').read_text().splitlines()]
        generation_journal = [json.loads(line) for line in (self.directory / 'generation-ledger.jsonl').read_text().splitlines()]
        durable_before_call = (count_journal[-1]['event'] == 'count_begin' if path.endswith('/input_tokens')
                               else count_journal[-1]['event'] == 'count_end' and 'input_tokens' in count_journal[-1]
                               and generation_journal[-1]['event'] == 'admitted')
        (self.directory / f'transport-{index}-request.bin').write_bytes(body)
        (self.directory / f'transport-{index}-response.bin').write_bytes(raw)
        self.calls.append({'path': path, 'request_sha256': sha(body),
                           'response_sha256': sha(raw), 'status': status,
                           'durable_before_call': durable_before_call})
        return status, raw


def run(plan_path, output):
    if output.exists():
        raise FileExistsError(output)
    plan = json.loads(plan_path.read_text())
    if (plan.get('id') != 'counted-request-local-v2' or plan.get('cases') != CASES
            or plan.get('scope') != 'synthetic_fixture_only'
            or plan.get('request_sha256') != sha(BODY)
            or plan.get('frozen_before_invocations') is not True):
        raise ValueError('plan does not match fixed diagnostic')
    hashes = {p: sha((BASE / p).read_bytes()) == plan['implementation_sha256'].get(p) for p in SOURCES}
    if not all(hashes.values()):
        raise ValueError('source changed after freeze')
    root = plan_path.resolve().parent / 'run'
    root.mkdir(mode=0o700)
    cases = {}
    for case in CASES:
        directory = root / case
        fixture = Fixture(directory, case)
        session = CountedFixtureSession(directory, fixture,
                                        token_limit=23 if case == 'budget_denial' else 96,
                                        attempt_limit=4, count_limit=1 if case == 'count_limit' else 4)
        returns = []
        for _ in range(2 if case in ('generation_retry', 'count_limit') else 1):
            try:
                returns.append({'result': session.run(BODY)})
            except Exception as exc:
                returns.append({'exception_type': type(exc).__name__})
        summary = session.summary()
        session.close()
        (directory / 'independent-transport-receipts.json').write_text(json.dumps(fixture.calls, indent=2) + '\n')
        counts = [c for c in fixture.calls if c['path'].endswith('/input_tokens')]
        generations = [c for c in fixture.calls if c['path'] == '/v1/responses']
        expected_gen = 0 if case in ('count_http_error', 'count_invalid', 'budget_denial') else 2 if case == 'generation_retry' else 1
        expected_count = 2 if case == 'generation_retry' else 1
        g = summary['generation']
        events = [json.loads(line) for line in (directory / 'generation-ledger.jsonl').read_text().splitlines()]
        admitted = [e for e in events if e['event'] == 'admitted']
        finished = [e for e in events if e['event'] == 'finish']
        count_events = [json.loads(line) for line in (directory / 'count-journal.jsonl').read_text().splitlines()]
        begins = [e for e in count_events if e['event'] == 'count_begin']
        ends = [e for e in count_events if e['event'] == 'count_end']
        checks = {
            'count_attempts_exact': len(counts) == expected_count == summary['count_attempts'],
            'durable_count_pairs': len(begins) == len(ends) == expected_count,
            'durable_count_ids': [e['count_attempt'] for e in begins] == [e['count_attempt'] for e in ends] == list(range(1, expected_count + 1)),
            'durable_count_generation_hash': all(e['generation_sha256'] == sha(BODY) for e in begins),
            'durable_count_response_hashes': [e['raw_response_sha256'] for e in ends] == [c['response_sha256'] for c in counts],
            'durable_before_each_transport_call': all(c['durable_before_call'] for c in fixture.calls),
            'generation_attempts_exact': len(generations) == expected_gen == g['accepted_attempts'],
            'exact_generation_bytes': all(c['request_sha256'] == sha(BODY) for c in generations),
            'count_projection_exact': all(json.loads((directory / f'transport-{fixture.calls.index(c)}-request.bin').read_bytes()) == PROJECTION for c in counts),
            'reservations_retained': g['reserved_tokens'] == expected_gen * 24,
            'within_budget': g['reserved_tokens'] <= (23 if case == 'budget_denial' else 96),
            'no_pending_attempts': g['pending_attempts'] == 0,
            'durable_admits_exact': len(admitted) == expected_gen,
            'durable_finishes_exact': len(finished) == expected_gen,
            'all_fixture_counts_bound': all(e['input_bound'] == 8 and e['max_output_tokens'] == 16 for e in admitted),
            'count_journal_preserved': (directory / 'count-journal.jsonl').stat().st_size > 0,
            'gates_still_unpassed': g['complete_agent_accounting_pass'] is False and g['product_promotion_pass'] is False,
        }
        if case in ('generation_retry', 'input_mismatch', 'missing_usage'):
            checks['unknown_usage_retained'] = g['unknown_usage_attempts'] == 1
        if case == 'input_mismatch':
            checks['mismatch_poisons'] = g['poisoned'] is True
        if case == 'output_cap':
            checks['cap_terminal_usage_counted'] = g['actual_observed_tokens'] == 24 and g['unknown_usage_attempts'] == 0
            checks['cap_remains_incomplete'] = [e['outcome'] for e in finished] == ['interrupted'] and returns[0].get('result', {}).get('outcome') == 'incomplete'
        if case in ('success', 'generation_retry', 'count_limit'):
            checks['valid_usage_exact'] = g['actual_observed_tokens'] == 11
        if case in ('count_http_error', 'count_invalid'):
            checks['failed_count_visible'] = summary['count_failures'] == 1
        cases[case] = {'checks': checks, 'fixture_diagnostic_pass': all(checks.values()),
                       'summary': summary, 'returns': returns, 'transport_receipts': fixture.calls,
                       'evidence_sha256': {str(p.relative_to(BASE)): sha(p.read_bytes())
                                           for p in sorted(directory.rglob('*')) if p.is_file()}}
    report = {'id': plan['id'], 'scope': plan['scope'], 'plan_sha256': sha(plan_path.read_bytes()),
              'implementation_hash_checks': hashes, 'cases': cases,
              'fixture_diagnostic_pass': all(c['fixture_diagnostic_pass'] for c in cases.values()),
              'live_provider_invocations': 0, 'network_requests': 0,
              'live_token_cap': 'real_provider_unverified', 'complete_agent_accounting_pass': False,
              'product_promotion_pass': False, 'agent_savings': 'unmeasured'}
    with output.open('x') as out:
        out.write(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'fixture_diagnostic_pass': report['fixture_diagnostic_pass']}))
    return 0 if report['fixture_diagnostic_pass'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.plan, args.output))
