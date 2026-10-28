"""Hardened broker delivery, native no-match recovery, and stdio diagnostics.

Exposed development workload only; no solver or comparative efficiency inference.
"""
import argparse
import hashlib
import os
import subprocess
import json
from pathlib import Path
import sys

from agent_backend import QUERY_ARGS
from probe_claude_subscription_v2 import save, sha
from probe_readonly_broker import request, tokenizer_identity
from readonly_broker_v2 import Broker
from retrieval import graf_artifact, graf_index, tokens

BASE = Path(__file__).resolve().parents[1]
ARMS = {'graf_evidence_selective': 'graf_v61',
        'graf_evidence_rescue': 'graf_v59', 'graphify': 'graphify'}


def manifest_sha(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()


def extra_checks(plan, data, cases, specs, evidence):
    checks = []
    # Freeze an absent identifier; this is a protocol diagnostic, not a scored task.
    for repo in data['repositories']:
        row = next(r for r in data['rows'] if r['repo'] == repo and r['budget'] == 2000)
        root = BASE / data['scratch'] / repo / 'input'
        graph = root.parent / 'graph/graphify-out/graph.json'
        broker = Broker(specs[repo, 'graphify'], 'graphify', root, graph,
                        evidence / f'{repo}-no-match.jsonl', max_actions=2)
        try:
            missing = broker.handle(request(1, 'retrieve', {'query': plan['no_match_query'], 'budget': 2000}))
            found = broker.handle(request(2, 'retrieve', {'query': cases[row['id']]['query'], 'budget': 2000}))
        finally:
            broker.close()
        native = json.loads(missing['result']['content'][0]['text'])
        following = json.loads(found['result']['content'][0]['text'])
        passed = (missing['result']['isError'] is False and native.get('returncode') == 4
                  and native.get('stdout') == 'No matching nodes found.\n'
                  and missing['result'].get('outcome') == 'no_match'
                  and found['result']['isError'] is False
                  and following.get('stdout') == row['tools']['graphify']['context'])
        checks.append({'id': repo + '-no-match-recovery', 'passed': passed})
        save(evidence / f'{repo}-no-match-exchange.json', {'missing': missing, 'following': found})
    repo = sorted(data['repositories'])[0]
    row = next(r for r in data['rows'] if r['repo'] == repo and r['budget'] == 2000)
    root = BASE / data['scratch'] / repo / 'input'
    graph = root.parent / 'graph/graphify-out/graph.json'
    for arm, broker_arm in ARMS.items():
        spec_path = evidence / f'{broker_arm}-stdio-plan.json'
        save(spec_path, specs[repo, broker_arm])
        rel = sorted(specs[repo, broker_arm]['source_manifest'])[0]
        requests = [request(1, 'retrieve', {'query': cases[row['id']]['query'], 'budget': 2000}),
                    request(2, 'read', {'path': rel, 'start': 1, 'lines': 2, 'budget': 8000})]
        wire = ''.join(json.dumps(m) + '\n' for m in requests)
        argv = [sys.executable, str(BASE / 'benchmark/readonly_broker_stdio_v2.py'),
                '--plan', str(spec_path), '--arm', broker_arm, '--root', str(root),
                '--graph', str(graph), '--journal', str(evidence / f'{broker_arm}-stdio.jsonl')]
        # Deliberately conflict with the frozen child setting. The broker must override it.
        env = dict(os.environ, PYTHONHASHSEED='23')
        process = subprocess.run(argv, input=wire, capture_output=True, text=True,
                                 timeout=120, env=env)
        save(evidence / f'{broker_arm}-stdio-exchange.json', {
            'argv': argv, 'input': wire, 'stdout': process.stdout, 'stderr': process.stderr,
            'returncode': process.returncode, 'parent_hash_seed': '23'})
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        delivered = json.loads(replies[0]['result']['content'][0]['text']) if replies else {}
        passed = (process.returncode == 0 and len(replies) == 2
                  and delivered.get('stdout') == row['tools'][arm]['context']
                  and replies[1]['result'].get('isError') is False)
        checks.append({'id': broker_arm + '-stdio', 'passed': passed})
    return checks


def run(plan_path):
    plan = json.loads(plan_path.read_text())
    for name, digest in plan['sources'].items():
        if sha(BASE / name) != digest:
            raise ValueError(f'Changed frozen source: {name}')
    if tokenizer_identity() != plan['tokenizer']:
        raise ValueError('Changed tokenizer')
    comparison = BASE / plan['comparison_output']
    if sha(comparison) != plan['comparison_sha256']:
        raise ValueError('Changed comparison receipt')
    data = json.loads(comparison.read_text())
    if data['run_plan']['sha256'] != sha(BASE / plan['comparison_plan']):
        raise ValueError('Comparison plan mismatch')
    cases = {q['id']: q for q in json.loads((BASE / data['queries']).read_text())}
    evidence = BASE / plan['evidence']
    evidence.mkdir(parents=True, exist_ok=False)
    inventory, artifacts = {}, {}
    for repo, info in data['repositories'].items():
        root = BASE / data['scratch'] / repo / 'input'
        graph = root.parent / 'graph/graphify-out/graph.json'
        actual = {rel: sha(root / rel) for rel in info['file_hashes']}
        if actual != info['file_hashes']:
            raise ValueError('Source parity failed')
        index = graf_index(BASE / '.research/bin/graf-v61', root)
        if set(index['files']) != set(actual):
            raise ValueError('Index file inventory mismatch')
        for path, expected in ((graf_artifact(root), info['builds']['graf']['artifact_sha256']),
                               (graph, info['builds']['graphify']['artifact_sha256'])):
            if sha(path) != expected:
                raise ValueError('Artifact parity failed')
            artifacts[str(path.relative_to(BASE))] = expected
        inventory[repo] = actual
    rows = []
    specs = {}
    for row in data['rows']:
        root = BASE / data['scratch'] / row['repo'] / 'input'
        graph = root.parent / 'graph/graphify-out/graph.json'
        # The same deterministic source read for every arm, independent of ranking.
        rel = sorted(inventory[row['repo']])[0]
        expected_read = '\n'.join(f'{i}: {line}' for i, line in
                                  enumerate((root / rel).read_text().splitlines()[:2], 1))
        for arm, broker_arm in ARMS.items():
            binary = (BASE / '.research/venv/bin/graphify' if arm == 'graphify' else
                      BASE / data['arm_binaries'][arm]['path'])
            spec = {'comparison': {'shared_output_budget': 8000},
                    'source_manifest': inventory[row['repo']],
                    'arms': [{'id': broker_arm, 'binary': str(binary),
                              'binary_sha256': sha(binary), 'query_args': QUERY_ARGS[broker_arm]}]}
            artifact = graph if arm == 'graphify' else graf_artifact(root)
            spec['artifacts'] = {broker_arm: {'path': str(artifact), 'sha256': sha(artifact),
                'source_manifest_sha256': manifest_sha(spec['source_manifest'])}}
            spec['subprocess_environment'] = {'PYTHONHASHSEED': '0'}
            spec['max_actions'] = 2
            spec['sources'] = plan['sources']
            specs[row['repo'], broker_arm] = spec
            stem = f"{row['id']}-{row['budget']}-{broker_arm}"
            broker = Broker(spec, broker_arm, root, graph, evidence / f'{stem}.jsonl', max_actions=2)
            try:
                retrieval = broker.handle(request(1, 'retrieve', {
                    'query': cases[row['id']]['query'], 'budget': row['budget']}))
                reading = broker.handle(request(2, 'read', {
                    'path': rel, 'start': 1, 'lines': 2, 'budget': 8000}))
            finally:
                broker.close()
            save(evidence / f'{stem}-exchange.json', {'retrieve': retrieval, 'read': reading})
            payload = json.loads(retrieval['result']['content'][0]['text'])
            native = row['tools'][arm]['runs'][0]
            checks = {
                'stdout_exact': payload.get('stdout') == native['stdout'],
                'stderr_exact': payload.get('stderr') == native['stderr'],
                'status_exact': payload.get('returncode') == native['returncode'],
                'source_read_exact': reading['result'].get('source', {}).get('text') == expected_read,
                'source_read_success': reading['result'].get('isError') is False,
                'source_hash_exact': reading['result'].get('source', {}).get('sha256') == inventory[row['repo']][rel],
                'token_count_exact': retrieval['result'].get('diagnostic_tokens') == len(tokens(native['stdout'])),
                'native_status_classification': retrieval['result'].get('isError') == (
                    native['returncode'] != 0 and not (arm == 'graphify' and native['returncode'] == 4
                    and native['stdout'] == 'No matching nodes found.\n')),
            }
            rows.append({'id': row['id'], 'budget': row['budget'], 'arm': broker_arm,
                         'checks': checks, 'passed': all(checks.values()),
                         'native_returncode': native['returncode']})
    extra = extra_checks(plan, data, cases, specs, evidence)
    unchanged = all(sha(BASE / p) == digest for p, digest in artifacts.items())
    sources_unchanged = all(sha(BASE / data['scratch'] / repo / 'input' / rel) == digest
                            for repo, files in inventory.items() for rel, digest in files.items())
    report = {'scope': 'exposed development integration diagnostic; no solver calls',
              'status': 'passed' if all(r['passed'] for r in rows) and all(r['passed'] for r in extra) and unchanged and sources_unchanged else 'failed',
              'plan_sha256': sha(plan_path), 'comparison_sha256': sha(comparison),
              'rows': rows, 'extra_cases': extra, 'artifact_hashes': artifacts, 'artifacts_unchanged': unchanged,
              'sources_unchanged': sources_unchanged,
              'graphify_entrypoint': 'broker uses pinned graphify executable; comparison uses python -m graphify',
              'cache': 'after sequential comparison; OS caches uncontrolled; no latency inference',
              'limitations': ['hash verification before and after execution is not an atomic filesystem snapshot',
                              'source-manifest binding authenticates a frozen receipt, not index semantics by itself',
                              'no solver or provider request accounting',
                              'native timing comparison excludes broker verification overhead'],
              'agent_savings': 'unmeasured', 'product_promotion_allowed': False}
    save(BASE / plan['output'], report)
    print(json.dumps({'status': report['status'], 'cases': len(rows),
                      'extra_cases': len(extra), 'failed': sum(not r['passed'] for r in rows + extra)}))
    return int(report['status'] != 'passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    sys.exit(run(parser.parse_args().plan.resolve()))
