"""Frozen development diagnostic: fresh versus reused bounded private sessions."""
import argparse
import itertools
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

from probe_snapshot_overhead import (ARMS, make_spec, expected_stdout, save_exclusive,
                                     serialized_payload_tokens, _native_runner)
from probe_claude_subscription_v2 import sha
from probe_readonly_broker import request, tokenizer_identity
from readonly_session import Session
from retrieval import tokens

BASE = Path(__file__).resolve().parents[1]


def orders(block):
    return ('fresh', 'reused') if block % 2 == 0 else ('reused', 'fresh')


def arm_order(mode_block):
    return list(itertools.permutations(ARMS))[mode_block % 6]


def pair_checks(retrieval, reading, native, arm, original_graph, staged_graph, manifest, rel, source_text):
    payload = json.loads(retrieval['result']['content'][0]['text'])
    source = reading['result'].get('source', {})
    return {'retrieve_success': retrieval['result'].get('isError') is False,
            'stdout': payload.get('stdout') == expected_stdout(native['stdout'], arm, original_graph, staged_graph),
            'stderr': payload.get('stderr') == native['stderr'], 'status': payload.get('returncode') == native['returncode'],
            'payload_tokens': retrieval['result'].get('diagnostic_tokens') == len(tokens(payload.get('stdout', ''))),
            'read_success': reading['result'].get('isError') is False,
            'source_hash': source.get('sha256') == manifest[rel], 'source_text': source.get('text') == source_text}


def run_group(plan, old, comp, cases, pairs, repo, rep, mode, arm, evidence):
    barm = ARMS[arm]
    stem = f'{repo}-r{rep}-{mode}-{barm}'
    folder = evidence / stem
    folder.mkdir(mode=0o700)
    origin = BASE / comp['scratch'] / repo / 'input'
    graph = origin.parent / 'graph/graphify-out/graph.json'
    spec = make_spec(plan, old, comp, repo, arm, origin, graph)
    manifest = spec['source_manifest']; rel = sorted(manifest)[0]
    source_text = '\n'.join(f'{i}: {line}' for i, line in enumerate((origin / rel).read_text().splitlines()[:2], 1))
    group = {'id': stem, 'repo': repo, 'repetition': rep, 'mode': mode, 'arm': barm,
             'pairs': [], 'sessions': [], 'errors': []}
    started = time.perf_counter()
    chunks = [[p] for p in pairs] if mode == 'fresh' else [pairs]
    pair_number = 0
    for chunk_index, chunk in enumerate(chunks):
        path = folder / f'session-{chunk_index}'
        path.mkdir(mode=0o700)
        samples = []
        session = None
        session_record = {'id': chunk_index, 'native_child_samples_ms': samples, 'errors': []}
        try:
            session = Session(spec, barm, origin, graph, path / 'journal.jsonl', evidence,
                              max_actions=2 * len(chunk), runner=_native_runner(samples))
            session.__enter__()
            session_record['root'] = str(session.root)
            save_exclusive(path / 'receipt.json', session.receipt)
            for row in chunk:
                req = request(2 * pair_number + 1, 'retrieve', {'query': cases[row['id']]['query'], 'budget': row['budget']})
                readreq = request(2 * pair_number + 2, 'read', {'path': rel, 'start': 1, 'lines': 2, 'budget': 8000})
                result = {'query_id': row['id'], 'budget': row['budget'], 'session': chunk_index,
                          'request': req, 'read_request': readreq, 'reply': None, 'read_reply': None,
                          'retrieve_wall_ms': None, 'read_wall_ms': None, 'checks': {}, 'errors': []}
                before = len(samples)
                try:
                    mark = time.perf_counter()
                    try: result['reply'] = session.handle(req)
                    finally: result['retrieve_wall_ms'] = (time.perf_counter() - mark) * 1000
                    mark = time.perf_counter()
                    try: result['read_reply'] = session.handle(readreq)
                    finally: result['read_wall_ms'] = (time.perf_counter() - mark) * 1000
                    result['checks'] = pair_checks(result['reply'], result['read_reply'], row['tools'][arm]['runs'][0],
                                                  arm, graph, session.graph_path, manifest, rel, source_text)
                except BaseException as exc:
                    result['errors'].append({'type': type(exc).__name__, 'message': str(exc), 'traceback': traceback.format_exc()})
                result['native_child_ms'] = sum(samples[before:])
                result['payload_tokens'] = serialized_payload_tokens(req, result['reply'], readreq, result['read_reply'])
                result['passed'] = bool(result['checks']) and all(result['checks'].values()) and not result['errors']
                group['pairs'].append(result)
                save_exclusive(path / f'pair-{pair_number}.json', result)
                pair_number += 1
        except BaseException as exc:
            session_record['errors'].append({'type': type(exc).__name__, 'message': str(exc), 'traceback': traceback.format_exc()})
        finally:
            if session is not None:
                try: session.close()
                except BaseException as exc:
                    session_record['errors'].append({'type': type(exc).__name__, 'message': str(exc)})
                session_record['timing'] = dict(session.timings)
                session_record['receipt'] = session.receipt
                session_record['cleanup'] = bool(session_record.get('root')) and not Path(session_record['root']).exists()
                if not (path / 'receipt.json').exists() and session.receipt is not None:
                    save_exclusive(path / 'receipt.json', session.receipt)
            save_exclusive(path / 'completion.json', session_record)
            group['sessions'].append(session_record)
    group['actual_wall_ms'] = (time.perf_counter() - started) * 1000
    group['setup_cleanup_ms'] = sum(value for s in group['sessions'] for value in s.get('timing', {}).values() if value is not None)
    group['pair_phase_ms'] = sum((p['retrieve_wall_ms'] or 0) + (p['read_wall_ms'] or 0) for p in group['pairs'])
    group['phase_subtotal_ms'] = group['setup_cleanup_ms'] + group['pair_phase_ms']
    group['native_child_ms'] = sum(sum(s['native_child_samples_ms']) for s in group['sessions'])
    group['passed'] = (len(group['pairs']) == 4 and all(p['passed'] for p in group['pairs']) and
                       all(not s['errors'] and s.get('cleanup') for s in group['sessions']))
    for pair in group['pairs']:
        pair['amortized_phase_ms'] = (pair['retrieve_wall_ms'] or 0) + (pair['read_wall_ms'] or 0) + group['setup_cleanup_ms'] / 4
    save_exclusive(folder / 'group.json', group)
    return group


def stdio_reference(plan, old, comp, cases, pairs, repo, evidence):
    origin = BASE / comp['scratch'] / repo / 'input'; graph = origin.parent / 'graph/graphify-out/graph.json'
    results = []
    for arm, barm in ARMS.items():
        path = evidence / ('stdio-' + barm); path.mkdir(mode=0o700)
        spec = make_spec(plan, old, comp, repo, arm, origin, graph)
        spec['sources'] = {n: h for n, h in plan['sources'].items() if n.startswith('benchmark/')}
        spec['max_actions'] = 8
        save_exclusive(path / 'plan.json', spec)
        rel = sorted(spec['source_manifest'])[0]
        source_text = '\n'.join(f'{i}: {line}' for i, line in enumerate((origin / rel).read_text().splitlines()[:2], 1))
        calls = []
        for i, row in enumerate(pairs):
            calls += [request(2*i+1, 'retrieve', {'query': cases[row['id']]['query'], 'budget': row['budget']}),
                      request(2*i+2, 'read', {'path': rel, 'start': 1, 'lines': 2, 'budget': 8000})]
        argv = [sys.executable, str(BASE/'benchmark/readonly_broker_stdio_v4.py'), '--plan',str(path/'plan.json'),
                '--arm',barm,'--root',str(origin),'--graph',str(graph),'--journal',str(path/'journal.jsonl'),
                '--snapshot-parent',str(evidence),'--receipt',str(path/'receipt.json')]
        wire = ''.join(json.dumps(x)+'\n' for x in calls)
        child = subprocess.run(argv,input=wire,capture_output=True,text=True,timeout=120,env=dict(os.environ,PYTHONHASHSEED='23'))
        save_exclusive(path/'process.json',{'argv':argv,'input':wire,'stdout':child.stdout,'stderr':child.stderr,'returncode':child.returncode})
        receipt = json.loads((path/'receipt.json').read_text()) if (path/'receipt.json').exists() else {}
        replies = [json.loads(x) for x in child.stdout.splitlines()]
        checks = []
        if len(replies)==8:
            for i,row in enumerate(pairs):
                checks.append(pair_checks(replies[2*i],replies[2*i+1],row['tools'][arm]['runs'][0],arm,graph,
                                          Path(receipt['artifact_path']),spec['source_manifest'],rel,source_text))
        results.append({'arm':barm,'checks':checks,'passed':child.returncode==0 and child.stderr=='' and len(checks)==4 and
                        all(all(x.values()) for x in checks) and not Path(receipt.get('snapshot_root','/')).exists()})
    return results


def run(path):
    plan = json.loads(path.read_text())
    for name,h in plan['sources'].items():
        if sha(BASE/name)!=h: raise ValueError('source_pin_mismatch:'+name)
    if tokenizer_identity()!=plan['tokenizer']: raise ValueError('tokenizer_mismatch')
    if sha(BASE/plan['comparison_output'])!=plan['comparison_sha256']:raise ValueError('comparison_mismatch')
    comp=json.loads((BASE/plan['comparison_output']).read_text())
    if sha(BASE/plan['comparison_plan'])!=comp['run_plan']['sha256']:raise ValueError('comparison_plan_mismatch')
    old=json.loads((BASE/plan['old_snapshot_plan']).read_text()); cases={q['id']:q for q in json.loads((BASE/plan['queries']).read_text())}
    if len(cases)!=6 or comp['repeats']!=3:raise ValueError('unsupported_workload')
    evidence=BASE/plan['evidence'];evidence.mkdir(mode=0o700,parents=True,exist_ok=False)
    groups=[]; block=mode_block=0
    for rep in range(1,4):
        for repo in sorted(comp['repositories']):
            pairs=sorted([r for r in comp['rows'] if r['repo']==repo],key=lambda r:(r['id'],r['budget']))
            if len(pairs)!=4:raise ValueError('incomplete_repo_workload')
            for mode in orders(block):
                for arm in arm_order(mode_block):
                    groups.append(run_group(plan,old,comp,cases,pairs,repo,rep,mode,arm,evidence))
                mode_block+=1
            block+=1
    repo=sorted(comp['repositories'])[0]
    pairs=sorted([r for r in comp['rows'] if r['repo']==repo],key=lambda r:(r['id'],r['budget']))
    stdio=stdio_reference(plan,old,comp,cases,pairs,repo,evidence)
    report={'scope':'Exposed lifecycle diagnostic; existing stdio already reuses snapshot; no new caching algorithm',
            'plan_sha256':sha(path),'comparison_sha256':plan['comparison_sha256'],'groups':groups,'stdio':stdio,
            'passed':all(g['passed'] for g in groups) and all(x['passed'] for x in stdio),
            'product_promotion_allowed':False,'agent_savings':'unmeasured','benchmark_solver_calls':0}
    save_exclusive(BASE/plan['output'],report)
    print(json.dumps({'passed':report['passed'],'groups':len(groups),'pairs':sum(len(g['pairs']) for g in groups),'stdio_sessions':len(stdio)}))
    return 0 if report['passed'] else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--plan',required=True,type=Path)
    sys.exit(run(p.parse_args().plan))
