"""Real backend delivery parity on an exposed, preregistered comparison.

No solver, timing inference, fresh quality evidence, or agent savings.
Index hashes are attested by this harness, not enforced by the broker itself.
"""
import argparse
import json
from pathlib import Path
import sys

from agent_backend import QUERY_ARGS
from probe_claude_subscription_v2 import save, sha
from probe_readonly_broker import request, tokenizer_identity
from readonly_broker import Broker
from retrieval import graf_artifact, graf_index, tokens

BASE = Path(__file__).resolve().parents[1]
ARMS = {'graf_evidence_selective': 'graf_v61',
        'graf_evidence_rescue': 'graf_v59', 'graphify': 'graphify'}


def run(plan_path):
    plan = json.loads(plan_path.read_text())
    for name, digest in plan['sources'].items():
        if sha(BASE / name) != digest:
            raise ValueError(f'Changed frozen source: {name}')
    if tokenizer_identity() != plan['tokenizer']:
        raise ValueError('Changed tokenizer')
    comparison = BASE / plan['comparison_output']
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
    unchanged = all(sha(BASE / p) == digest for p, digest in artifacts.items())
    sources_unchanged = all(sha(BASE / data['scratch'] / repo / 'input' / rel) == digest
                            for repo, files in inventory.items() for rel, digest in files.items())
    report = {'scope': 'exposed development integration diagnostic; no solver calls',
              'status': 'passed' if all(r['passed'] for r in rows) and unchanged and sources_unchanged else 'failed',
              'plan_sha256': sha(plan_path), 'comparison_sha256': sha(comparison),
              'rows': rows, 'artifact_hashes': artifacts, 'artifacts_unchanged': unchanged,
              'sources_unchanged': sources_unchanged,
              'graphify_entrypoint': 'broker uses pinned graphify executable; comparison uses python -m graphify',
              'cache': 'after sequential comparison; OS caches uncontrolled; no latency inference',
              'limitations': ['broker does not enforce index hash authentication or atomic source snapshots',
                              'no stale-index refusal test; artifact attestation is harness-only',
                              'no solver or provider request accounting'],
              'agent_savings': 'unmeasured', 'product_promotion_allowed': False}
    save(BASE / plan['output'], report)
    print(json.dumps({'status': report['status'], 'cases': len(rows),
                      'failed': sum(not r['passed'] for r in rows)}))
    return int(report['status'] != 'passed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    sys.exit(run(parser.parse_args().plan.resolve()))
