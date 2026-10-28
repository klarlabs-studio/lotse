"""Local injected-backend broker fixtures; no solver or retrieval comparison."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys

from agent_backend import QUERY_ARGS, query_command
from probe_claude_subscription_v2 import save, sha
from provider_ledger import _parse_request
from retrieval import tokens

ROOT = Path(__file__).resolve().parents[1]
CASES = ['success', 'refusals', 'stale_source', 'backend_error', 'native_output_overrun', 'stdio']
SOURCE = 'def add(a, b):\n    return a + b\n'
SOURCES = ['benchmark/readonly_broker.py', 'benchmark/test_readonly_broker.py',
           'benchmark/probe_readonly_broker.py', 'benchmark/test_probe_readonly_broker.py',
           'benchmark/readonly_broker_stdio.py',
           'benchmark/agent_backend.py', 'benchmark/retrieval.py',
           'benchmark/provider_ledger.py', 'benchmark/probe_claude_subscription_v2.py']


def tokenizer_identity():
    from retrieval import ENCODING
    digest = hashlib.sha256()
    for piece, rank in sorted(ENCODING._mergeable_ranks.items()):
        digest.update(len(piece).to_bytes(4, 'big') + piece + rank.to_bytes(4, 'big'))
    digest.update(json.dumps(ENCODING._special_tokens, sort_keys=True).encode())
    return {'name': ENCODING.name, 'version': importlib.metadata.version('tiktoken'),
            'vocabulary_sha256': digest.hexdigest(),
            'count_scope': 'local serialized MCP payload diagnostic, not provider usage'}


def request(identifier, name, arguments):
    return {'jsonrpc': '2.0', 'id': identifier, 'method': 'tools/call',
            'params': {'name': name, 'arguments': arguments,
                       '_meta': {'claudecode/toolUseId': f'fixture-{identifier}'}}}


def fixture(plan, arm, case, work):
    from readonly_broker import Broker
    root = work / 'source'
    (root / 'src').mkdir(parents=True)
    source = root / 'src/math.py'
    source.write_text(SOURCE, encoding='utf-8')
    graph = work / 'graph.json'
    save(graph, {'diagnostic': 'not a real graph or retrieval result'})
    local = dict(plan)
    local['source_manifest'] = {'src/math.py': sha(source)}
    calls = []
    native = 'hint ' * 300 if case == 'native_output_overrun' else 'src/math.py:1-2\n'

    def runner(argv, **kwargs):
        calls.append({'argv': argv, 'timeout': kwargs.get('timeout')})
        if case == 'backend_error':
            return subprocess.CompletedProcess(argv, 7, 'partial fixture output\n', 'synthetic failure\n')
        return subprocess.CompletedProcess(argv, 0, native, '')

    if case == 'stdio':
        return stdio_fixture(local, arm, root, graph, work)
    broker = Broker(local, arm, root, graph, work / 'journal.jsonl', max_actions=4, runner=runner)
    exchanges = []

    def send(message):
        reply = broker.handle(message)
        exchanges.append({'request': message, 'reply': reply})
        return reply

    def call(identifier, name, arguments):
        return send(request(identifier, name, arguments))

    def failed(reply):
        return reply.get('result', {}).get('isError') is True or 'error' in reply

    initialized = send({'jsonrpc': '2.0', 'id': 0, 'method': 'initialize',
                        'params': {'protocolVersion': '2025-06-18'}})
    inventory = send({'jsonrpc': '2.0', 'id': 'tools', 'method': 'tools/list'})
    checks = {'tools_only': [t['name'] for t in inventory['result']['tools']] == ['retrieve', 'read'],
              'initialization': initialized.get('result', {}).get('capabilities') == {'tools': {}}}
    if case in ('success', 'native_output_overrun'):
        budget = 1 if case == 'native_output_overrun' else 64
        reply = call(1, 'retrieve', {'query': 'add arithmetic', 'budget': budget})
        checks['retrieve_success'] = not failed(reply)
        delivered_backend = json.loads(reply['result']['content'][0]['text'])
        checks['native_output_preserved'] = delivered_backend.get('stdout') == native
        checks['exact_argv'] = len(calls) == 1 and calls[0]['argv'] == query_command(
            local, arm, root, graph, 'add arithmetic', budget, ROOT)
        if case == 'success':
            reply = call(2, 'read', {'path': 'src/math.py', 'start': 1, 'lines': 2, 'budget': 64})
            checks['read_success'] = not failed(reply) and 'return a + b' in json.dumps(reply)
        else:
            checks['fixture_exceeds_budget'] = len(tokens(native)) > budget
    elif case == 'refusals':
        checks['unknown_tool'] = failed(call(1, 'shell', {}))
        checks['bool_budget'] = failed(call(2, 'retrieve', {'query': 'add', 'budget': True}))
        checks['traversal'] = failed(call(3, 'read', {'path': '../outside.py'}))
        checks['last_allowed_read'] = not failed(call(4, 'read', {'path': 'src/math.py'}))
        checks['action_limit'] = failed(call(5, 'read', {'path': 'src/math.py'}))
        checks['no_backend_dispatch'] = not calls
    elif case == 'stale_source':
        source.write_text(SOURCE + '# changed after manifest freeze\n', encoding='utf-8')
        checks['changed_source_refused'] = failed(call(1, 'read', {'path': 'src/math.py'}))
        checks['changed_source_retrieval_refused'] = failed(call(2, 'retrieve', {'query': 'add'}))
        checks['no_backend_dispatch'] = not calls
    elif case == 'backend_error':
        checks['backend_error'] = failed(call(1, 'retrieve', {'query': 'add'}))
        checks['later_retrieve_refused'] = failed(call(2, 'retrieve', {'query': 'add'}))
        checks['one_backend_dispatch'] = len(calls) == 1
    save(work / 'exchanges.json', exchanges)
    save(work / 'backend-invocations.json', calls)
    journal = [json.loads(line) for line in (work / 'journal.jsonl').read_text().splitlines()]
    # Source text, tool envelopes and backend observations are retained locally.
    checks['nonempty_journal'] = bool(journal)
    begins = [e for e in journal if e.get('kind') == 'begin']
    replies = [e for e in journal if e.get('kind') == 'reply' and 'action' in e]
    tool_exchanges = [e for e in exchanges if e['request']['method'] == 'tools/call']
    checks['all_tool_attempts_journaled'] = len(begins) == len(replies) == len(tool_exchanges)
    checks['exact_request_reply_objects'] = ([e['request'] for e in begins] ==
        [e['request'] for e in tool_exchanges] and [e['reply'] for e in replies] ==
        [e['reply'] for e in tool_exchanges])
    checks['wire_diagnostic_counts'] = all(
        json.loads(e['wire_line']) == e[key] and e['wire_line'].endswith('\n') and
        e['wire_bytes'] == len(e['wire_line'].encode('utf-8')) and
        e['diagnostic_tokens'] == len(tokens(e['wire_line']))
        for key, records in [('request', begins), ('reply', replies)] for e in records)
    broker.close()
    return {'arm': arm, 'case': case, 'checks': checks, 'diagnostic_pass': all(checks.values()),
            'backend_invocations': len(calls), 'journal_records': len(journal)}


def stdio_fixture(plan, arm, root, graph, work):
    plan = dict(plan, max_actions=4)
    plan.setdefault('sources', {name: sha(ROOT / name) for name in SOURCES})
    save(work / 'server-plan.json', plan)
    command = [sys.executable, str(ROOT / 'benchmark/readonly_broker_stdio.py'),
               '--plan', str(work / 'server-plan.json'), '--arm', arm, '--root', str(root),
               '--graph', str(graph), '--journal', str(work / 'journal.jsonl')]
    messages = [b'{malformed}\n', json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}).encode() + b'\n',
                json.dumps(request(2, 'read', {'path': 'src/math.py', 'budget': 64})).encode() + b'\n']
    raw = b''.join(messages)
    (work / 'stdin.jsonl').write_bytes(raw)
    completed = subprocess.run(command, input=raw, capture_output=True, timeout=15, check=False)
    (work / 'stdout.jsonl').write_bytes(completed.stdout)
    (work / 'stderr.txt').write_bytes(completed.stderr)
    save(work / 'command.json', command)
    replies = [_parse_request(line) for line in completed.stdout.splitlines()]
    records = [_parse_request(line) for line in (work / 'journal.jsonl').read_bytes().splitlines()]
    journal_replies = [e['wire_line'].encode() for e in records if 'reply' in e and 'wire_line' in e]
    incoming = [e for e in records if e.get('kind') == 'raw_input']
    checks = {'server_exit': completed.returncode == 0, 'three_replies': len(replies) == 3,
              'malformed_preserved': replies[0].get('error', {}).get('code') == -32700,
              'read_success': replies[-1].get('result', {}).get('isError') is False,
              'all_raw_inputs': len(incoming) == 3,
              'exact_delivered_stdout': b''.join(journal_replies) == completed.stdout}
    return {'arm': arm, 'case': 'stdio', 'checks': checks, 'diagnostic_pass': all(checks.values()),
            'backend_invocations': 0, 'journal_records': len(records)}


def run(plan_path):
    plan_path = Path(plan_path).resolve()
    plan = _parse_request(plan_path.read_bytes())
    if (plan.get('id') != 'readonly-broker-local-v1' or plan.get('cases') != CASES or
            plan.get('fixture_source') != SOURCE or plan.get('arm_order') != list(QUERY_ARGS) or
            plan.get('comparison', {}).get('shared_output_budget') != 64 or
            [a.get('id') for a in plan.get('arms', [])] != list(QUERY_ARGS)):
        raise ValueError('unsupported_plan')
    if set(plan['sources']) != set(SOURCES) or any(sha(ROOT / p) != h for p, h in plan['sources'].items()):
        raise ValueError('source_pin_mismatch')
    if any(sha(ROOT / a['binary']) != a['binary_sha256'] or a['query_args'] != QUERY_ARGS[a['id']] for a in plan['arms']):
        raise ValueError('backend_contract_pin_mismatch')
    if sha(Path(sys.executable).resolve()) != plan['python_sha256']:
        raise ValueError('python_pin_mismatch')
    if plan['tokenizer'] != tokenizer_identity():
        raise ValueError('tokenizer_pin_mismatch')
    directory = plan_path.parent / 'run'
    directory.mkdir(mode=0o700, exist_ok=False)
    report = {'id': plan['id'], 'plan_sha256': sha(plan_path), 'scope': 'injected_backend_contract_diagnostic',
              'solver_model_calls': 0, 'real_backend_invocations': 0, 'cases': [],
              'agent_savings': 'unmeasured', 'complete_provider_accounting': False,
              'product_promotion_pass': False, 'graphify_comparison': 'unchanged'}
    stop = False
    for arm in plan['arm_order']:
        for case in CASES:
            work = directory / f'{arm}-{case}'
            work.mkdir(mode=0o700)
            try:
                result = fixture(plan, arm, case, work)
            except Exception as error:
                result = {'arm': arm, 'case': case, 'diagnostic_pass': False, 'error_type': type(error).__name__}
            report['cases'].append(result)
            if not result['diagnostic_pass']:
                stop = True
                break
        if stop:
            break
    passed = len(report['cases']) == len(CASES) * len(plan['arm_order']) and not stop
    report.update(status='completed' if passed else 'failed', exit_code=0 if passed else 1)
    report['raw_artifacts'] = {str(p.relative_to(plan_path.parent)): sha(p) for p in sorted(directory.rglob('*')) if p.is_file()}
    save(plan_path.parent / 'report.json', report)
    print(json.dumps({'status': report['status'], 'exit_code': report['exit_code'], 'cases': len(report['cases'])}))
    return report['exit_code']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True)
    raise SystemExit(run(parser.parse_args().plan))
