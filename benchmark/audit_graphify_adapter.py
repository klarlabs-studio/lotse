"""Offline pinned Graphify CLI contract check; no model or efficiency measurements."""
import argparse
import copy
import json
import os
from pathlib import Path
import subprocess
import sys

from compare_graphify import BASE, graphify_command, sha


def fixtures():
    graph = dict(directed=False, multigraph=False,
                 nodes=[dict(id=n, label=n, source_file=f'{n}.py', source_location='L1')
                        for n in ('caller', 'callee', 'third', 'fourth')],
                 links=[dict(source=a, target=b, relation='calls', confidence='EXTRACTED')
                        for a, b in [('caller', 'callee'), ('callee', 'third'), ('third', 'fourth')]])
    marked = copy.deepcopy(graph)
    marked['links'][0].update(source='callee', target='caller', _src='caller', _tgt='callee')
    edges = copy.deepcopy(graph)
    edges['edges'] = edges.pop('links')
    depth_graph = copy.deepcopy(graph)
    depth_graph['nodes'][0]['label'] = 'origin'
    return [('direction-and-depth', depth_graph, 'origin', 2000),
            ('incoming-call', graph, 'callee', 2000),
            ('preserved-markers', marked, 'callee', 2000),
            ('edges-key', edges, 'callee', 2000),
            ('small-budget', graph, 'callee', 1),
            ('no-match', graph, 'unmatchable_xyz987', 2000)]


def probe(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, 'PYTHONHASHSEED': '0', 'GRAPHIFY_QUERY_LOG_DISABLE': '1'}
    rows = []
    for name, graph, query, budget in fixtures():
        path = (directory / f'{name}.json').resolve()
        path.write_text(json.dumps(graph, indent=2) + '\n')
        commands = {'adapter': graphify_command(path, query, budget, 'native-cli-v2'),
                    'reference': [sys.executable, '-m', 'graphify', 'query', query,
                                  '--graph', str(path), '--budget', str(budget)]}
        runs = {}
        for label, command in commands.items():
            run = subprocess.run(command, capture_output=True, text=True, timeout=30, env=env)
            for stream in ('stdout', 'stderr'):
                (directory / f'{name}.{label}.{stream}.txt').write_text(getattr(run, stream))
            runs[label] = dict(command=command, returncode=run.returncode,
                               stdout=run.stdout, stderr=run.stderr)
        output = runs['adapter']['stdout']
        semantic = True
        if name in ('incoming-call', 'preserved-markers', 'edges-key'):
            semantic = 'EDGE caller --calls [EXTRACTED]--> callee' in output
        if name == 'direction-and-depth':
            semantic = 'NODE third ' in output and 'NODE fourth ' not in output
        if name == 'small-budget':
            semantic = 'NODE callee ' in output
        if name == 'no-match':
            semantic = output == 'No matching nodes found.\n'
        semantic = semantic and runs['adapter']['returncode'] == (4 if name == 'no-match' else 0)
        rows.append(dict(id=name, fixture_sha256=sha(path), runs=runs,
                         parity_pass=runs['adapter']['returncode'] == runs['reference']['returncode']
                         and output == runs['reference']['stdout']
                         and runs['adapter']['stderr'] == runs['reference']['stderr'], semantic_pass=semantic))
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists() or args.evidence.exists():
        parser.error('Use new receipt and evidence paths; existing evidence is never overwritten')
    import graphify
    baseline = json.loads((BASE / 'results/audit-2026-10-22-comparison.json').read_text())['baseline']
    pin = next(r for r in json.loads((BASE / 'benchmark/corpora.json').read_text())['repositories']
               if r['id'] == 'graphify')
    if pin != baseline['pin']:
        parser.error('Baseline pin differs from historical receipt')
    roots = [Path(graphify.__file__).parent, BASE / '.research/corpora/graphify/graphify']
    for root in roots:
        actual = {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob('*.py'))}
        if actual != baseline['python_sources']:
            parser.error(f'Graphify sources differ from pinned receipt: {root}')
    rows = probe(args.evidence)
    result = dict(mode='offline_native_cli_contract_diagnostic', adapter='native-cli-v2',
                  pin=pin, baseline_python_files_checked=len(actual), all_baseline_python_hashes_match=True,
                  environment={'PYTHONHASHSEED': '0', 'GRAPHIFY_QUERY_LOG_DISABLE': '1'},
                  invocation=sys.argv, cases=rows,
                  native_cli_parity_pass=all(r['parity_pass'] and r['semantic_pass'] for r in rows),
                  source_sha256={str(p.relative_to(BASE)): sha(p) for p in
                     [Path(__file__).resolve(), BASE / 'benchmark/compare_graphify.py',
                      BASE / 'benchmark/graphify-adapters.json', BASE / 'benchmark/test_graphify_adapter.py']},
                  model_calls=0, product_promotion_pass=False, agent_efficiency_acceptance_pass=False,
                  limitation='Mechanical CLI contract only. No retrieval quality, latency or agent savings measured. Historical trials unchanged; new comparative runs require a new freeze.')
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: result[k] for k in ('adapter', 'native_cli_parity_pass', 'model_calls')}))
    raise SystemExit(0 if result['native_cli_parity_pass'] else 1)


if __name__ == '__main__':
    main()
