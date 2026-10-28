"""Frozen development integrity diagnostic for private-copy retrieval isolation.

Path normalization below is solely an integrity check. Raw payloads and token
counts remain intact; no broker overhead or agent efficiency is measured.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from agent_backend import QUERY_ARGS
from probe_claude_subscription_v2 import save, sha
from probe_readonly_broker import request, tokenizer_identity
from readonly_broker_v2 import Broker, source_manifest_sha256
from readonly_broker_stdio_v4 import staged_plan, index_options
from readonly_snapshot_v2 import Snapshot
from retrieval import tokens

BASE = Path(__file__).resolve().parents[1]
ARMS = {'graf_evidence_selective': 'graf_v61',
        'graf_evidence_rescue': 'graf_v59', 'graphify': 'graphify'}


def expected_stdout(native, arm, old_graph, new_graph):
    if arm != 'graphify':
        return native
    old = old_graph.relative_to(BASE).as_posix()
    new = new_graph.relative_to(BASE).as_posix()
    prefix = 'Graph: ' + old + ' '
    if not native.startswith(prefix):
        raise ValueError('unexpected_graph_header')
    return 'Graph: ' + new + ' ' + native[len(prefix):]


def spec_for(plan, data, repo, arm, root, graph):
    broker_arm = ARMS[arm]
    binary = BASE / ('.research/venv/bin/graphify' if arm == 'graphify' else data['arm_binaries'][arm]['path'])
    artifact = graph if arm == 'graphify' else root / '.graf/index.bin'
    digest = data['repositories'][repo]['builds']['graphify' if arm == 'graphify' else 'graf']['artifact_sha256']
    manifest = data['repositories'][repo]['file_hashes']
    return {'comparison': {'shared_output_budget': 8000}, 'source_manifest': manifest,
            'arms': [{'id': broker_arm, 'binary': str(binary), 'binary_sha256': sha(binary),
                      'query_args': QUERY_ARGS[broker_arm]}],
            'artifacts': {broker_arm: {'path': str(artifact), 'sha256': digest,
                          'source_manifest_sha256': source_manifest_sha256(manifest)}},
            'subprocess_environment': {'PYTHONHASHSEED': '0'}, 'max_actions': 2,
            'sources': plan['sources'],
            'snapshot_index_build': dict(plan['index_build'], semantics_sha256=plan['index_semantics'][repo])}


def run(plan_path):
    plan = json.loads(plan_path.read_text())
    for name, digest in plan['sources'].items():
        if sha(BASE / name) != digest:
            raise ValueError('changed_frozen_source: ' + name)
    if tokenizer_identity() != plan['tokenizer']:
        raise ValueError('changed_tokenizer')
    comparison = BASE / plan['comparison_output']
    if sha(comparison) != plan['comparison_sha256']:
        raise ValueError('changed_comparison_receipt')
    data = json.loads(comparison.read_text())
    if data['run_plan']['sha256'] != sha(BASE / plan['comparison_plan']):
        raise ValueError('changed_comparison_plan')
    cases = {q['id']: q for q in json.loads((BASE / data['queries']).read_text())}
    evidence = BASE / plan['evidence']
    evidence.mkdir(parents=True, exist_ok=False)
    rows, contexts, stdio = [], [], []
    for repo, info in data['repositories'].items():
        origin = BASE / data['scratch'] / repo / 'input'
        original_graph = origin.parent / 'graph/graphify-out/graph.json'
        manifest = info['file_hashes']
        if any(sha(origin / rel) != digest for rel, digest in manifest.items()):
            raise ValueError('changed_original_source')
        for arm, broker_arm in ARMS.items():
            # Mutate only an independent live fixture, never comparison evidence.
            live = evidence / (repo + '-' + broker_arm + '-live') / 'input'
            live.mkdir(parents=True)
            for rel, digest in manifest.items():
                target = live / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((origin / rel).read_bytes())
                if sha(target) != digest:
                    raise ValueError('live_fixture_source_mismatch')
            live_graph = live.parent / 'graph/graphify-out/graph.json'
            artifact = live_graph if arm == 'graphify' else live / '.graf/index.bin'
            source_artifact = original_graph if arm == 'graphify' else origin / '.graf/index.bin'
            artifact.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_artifact, artifact)
            spec = spec_for(plan, data, repo, arm, live, live_graph)
            artifact_digest = spec['artifacts'][broker_arm]['sha256']
            if sha(artifact) != artifact_digest:
                raise ValueError('changed_original_artifact')
            rel = sorted(manifest)[0]
            read_text = '\n'.join(f'{i}: {line}' for i, line in
                                  enumerate((live / rel).read_text().splitlines()[:2], 1))
            with Snapshot(live, manifest, artifact, artifact_digest, broker_arm, evidence, **index_options(spec, broker_arm)) as snapshot:
                root = snapshot.root
                receipt = snapshot.receipt
                save(evidence / f'{repo}-{broker_arm}-snapshot-receipt.json', receipt)
                separate_inodes = all((live / p).stat().st_ino != (root / p).stat().st_ino for p in manifest)
                separate_artifact_inode = artifact.stat().st_ino != snapshot.artifact_path.stat().st_ino
                (live / rel).write_bytes((live / rel).read_bytes() + b'\nLIVE_MUTATION_DIAGNOSTIC\n')
                artifact.write_bytes(artifact.read_bytes() + b'\nLIVE_ARTIFACT_MUTATION\n')
                staged = staged_plan(spec, broker_arm, snapshot)
                for row in [r for r in data['rows'] if r['repo'] == repo]:
                    stem = f"{row['id']}-{row['budget']}-{broker_arm}"
                    broker = Broker(staged, broker_arm, root, snapshot.graph_path,
                                    evidence / (stem + '.jsonl'), max_actions=2)
                    try:
                        retrieval = broker.handle(request(1, 'retrieve', {
                            'query': cases[row['id']]['query'], 'budget': row['budget']}))
                        reading = broker.handle(request(2, 'read', {
                            'path': rel, 'start': 1, 'lines': 2, 'budget': 8000}))
                    finally:
                        broker.close()
                    save(evidence / (stem + '-exchange.json'), {'retrieve': retrieval, 'read': reading})
                    payload = json.loads(retrieval['result']['content'][0]['text'])
                    native = row['tools'][arm]['runs'][0]
                    expected = expected_stdout(native['stdout'], arm, original_graph, snapshot.graph_path)
                    checks = {'stdout_exact_except_declared_graph_path': payload.get('stdout') == expected,
                              'stderr_exact': payload.get('stderr') == native['stderr'],
                              'status_exact': payload.get('returncode') == native['returncode'],
                              'retrieval_success': retrieval['result'].get('isError') is False,
                              'source_success': reading['result'].get('isError') is False,
                              'source_bytes': reading['result'].get('source', {}).get('text') == read_text,
                              'source_hash': reading['result'].get('source', {}).get('sha256') == manifest[rel],
                              'payload_tokens': retrieval['result'].get('diagnostic_tokens') == len(tokens(payload.get('stdout', '')))}
                    rows.append({'id': row['id'], 'budget': row['budget'], 'arm': broker_arm,
                                 'checks': checks, 'passed': all(checks.values()),
                                 'raw_output_tokens': len(tokens(payload.get('stdout', '')))})
                checks = {'separate_source_inodes': separate_inodes,
                          'separate_artifact_inode': separate_artifact_inode,
                          'staged_sources_pinned': all(sha(root / p) == h for p, h in manifest.items()),
                          'staged_artifact_pinned': sha(snapshot.artifact_path) == snapshot.receipt['artifact_sha256'],
                          'original_artifact_bound': snapshot.receipt.get('original_artifact_sha256', snapshot.receipt['artifact_sha256']) == artifact_digest,
                          'live_source_changed': sha(live / rel) != manifest[rel],
                          'live_artifact_changed': sha(artifact) != artifact_digest}
            checks['cleanup'] = not root.exists()
            contexts.append({'repo': repo, 'arm': broker_arm, 'checks': checks, 'passed': all(checks.values())})
    # Exercise the actual launcher on one unchanged repository, all three arms.
    repo = sorted(data['repositories'])[0]
    origin = BASE / data['scratch'] / repo / 'input'
    graph = origin.parent / 'graph/graphify-out/graph.json'
    row = next(r for r in data['rows'] if r['repo'] == repo and r['budget'] == 2000)
    for arm, broker_arm in ARMS.items():
        spec = spec_for(plan, data, repo, arm, origin, graph)
        path = evidence / (broker_arm + '-stdio-plan.json')
        save(path, spec)
        rel = sorted(spec['source_manifest'])[0]
        calls = [request(1, 'retrieve', {'query': cases[row['id']]['query'], 'budget': 2000}),
                 request(2, 'read', {'path': rel, 'start': 1, 'lines': 2, 'budget': 8000})]
        wire = ''.join(json.dumps(call) + '\n' for call in calls)
        receipt_path = evidence / (broker_arm + '-stdio-receipt.json')
        argv = [sys.executable, str(BASE / 'benchmark/readonly_broker_stdio_v4.py'),
                '--plan', str(path), '--arm', broker_arm, '--root', str(origin),
                '--graph', str(graph), '--journal', str(evidence / (broker_arm + '-stdio.jsonl')),
                '--snapshot-parent', str(evidence), '--receipt', str(receipt_path)]
        process = subprocess.run(argv, input=wire, capture_output=True, text=True,
                                 timeout=120, env=dict(os.environ, PYTHONHASHSEED='23'))
        save(evidence / (broker_arm + '-stdio-exchange.json'), {'argv': argv, 'input': wire,
             'stdout': process.stdout, 'stderr': process.stderr, 'returncode': process.returncode})
        replies = [json.loads(line) for line in process.stdout.splitlines()]
        receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        # Find the staged graph path from its native argv, independently of receipt schema.
        payload = json.loads(replies[0]['result']['content'][0]['text']) if replies else {}
        native_argv = payload.get('argv', payload.get('command', []))
        new_graph = Path(native_argv[native_argv.index('--graph') + 1]) if arm == 'graphify' and '--graph' in native_argv else graph
        expected = expected_stdout(row['tools'][arm]['context'], arm, graph, new_graph)
        source = replies[1]['result'].get('source', {}) if len(replies) == 2 else {}
        expected_read = '\n'.join(f'{i}: {line}' for i, line in enumerate((origin / rel).read_text().splitlines()[:2], 1))
        checks = {'process_success': process.returncode == 0 and process.stderr == '',
                  'reply_count': len(replies) == 2, 'receipt_saved': bool(receipt),
                  'cleanup': bool(receipt.get('snapshot_root')) and not Path(receipt['snapshot_root']).exists(),
                  'receipt_manifest': receipt.get('source_manifest_sha256') == source_manifest_sha256(spec['source_manifest']),
                  'receipt_source_hashes': receipt.get('source_hashes') == spec['source_manifest'],
                  'receipt_phase': receipt.get('phase') == 'snapshot_ready',
                  'receipt_mode': receipt_path.exists() and receipt_path.stat().st_mode & 0o777 == 0o600,
                  'receipt_artifact': receipt.get('original_artifact_sha256', receipt.get('artifact_sha256')) == spec['artifacts'][broker_arm]['sha256'],
                  'receipt_semantics': arm == 'graphify' or receipt.get('index_semantics_sha256') == plan['index_semantics'][repo],
                  'stdout': payload.get('stdout') == expected,
                  'stderr': payload.get('stderr') == row['tools'][arm]['runs'][0]['stderr'],
                  'returncode': payload.get('returncode') == row['tools'][arm]['runs'][0]['returncode'],
                  'source_bytes': source.get('text') == expected_read,
                  'source_hash': source.get('sha256') == spec['source_manifest'][rel]}
        stdio.append({'arm': broker_arm, 'checks': checks, 'passed': all(checks.values())})
    report = {'scope': 'exposed development snapshot integrity diagnostic; zero solver calls',
              'status': 'passed' if all(r['passed'] for r in rows + contexts + stdio) else 'failed',
              'plan_sha256': sha(plan_path), 'comparison_sha256': sha(comparison),
              'rows': rows, 'contexts': contexts, 'stdio': stdio,
              'backend_invocations': len(rows) + len(stdio), 'source_reads': len(rows) + len(stdio),
              'cache': 'after sequential native comparison; OS caches uncontrolled; no broker latency inference',
              'limitations': plan['limitations'], 'agent_savings': 'unmeasured', 'product_promotion_allowed': False}
    save(BASE / plan['output'], report)
    print(json.dumps({'status': report['status'], 'cells': len(rows), 'contexts': len(contexts), 'stdio': len(stdio)}))
    return int(report['status'] != 'passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    sys.exit(run(parser.parse_args().plan.resolve()))
