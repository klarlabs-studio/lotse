"""Run the separately frozen native Codex MCP pilot, preserving every attempt.

No changes to the Claude batches, their pins or their acceptance rules. The
native MCP protocol is a new solver harness and cannot be pooled with
Claude results. --limit only paces the frozen order, never selects tasks.
"""
import argparse
from collections import Counter
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import random
import subprocess
import time

from agent_trials import BASE, audit_trace, tasks
from codex_mcp_solver import invoke_native
from codex_validation import evaluate
from codex_discovery import discovery
from run_agent_trial import issue_prompt, orient, preflight, system_prompt
import task_session
from compare_graphify import validate_adapter_freeze
from task_session import prepare_session, tool_definitions
from retrieval import load_documents, tokens


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def entries(spec):
    if spec.get('paired_order', False):
        rng = random.Random(spec['seed'])
        pairs = [(task, k) for task in spec['task_ids'] for k in range(1, spec['attempts'] + 1)]
        rng.shuffle(pairs)
        result = []
        for task, k in pairs:
            arms = list(spec['arms'])
            rng.shuffle(arms)
            result.extend((task, k, arm) for arm in arms)
        return result
    result = [(task, k, arm) for task in spec['task_ids']
              for k in range(1, spec['attempts'] + 1) for arm in spec['arms']]
    random.Random(spec['seed']).shuffle(result)
    return result


def trial(spec, task, attempt, arm):
    identifier = f"{spec['id']}-{task['id']}-{arm}-{attempt}"
    with contextlib.redirect_stdout(io.StringIO()):
        session = prepare_session(task, identifier, arm, spec.get('graphify_adapter', {}).get('id', 'library-v1'))
    directory = session.directory
    initial = {p: hashlib.sha256(t.encode()).hexdigest() for p, t in load_documents(session.root).items()}
    (directory / 'initial-source-hashes.json').write_text(json.dumps(initial, sort_keys=True, indent=2) + '\n')
    if hashlib.sha256(json.dumps(initial, sort_keys=True).encode()).hexdigest() != spec['source_manifest_sha256'][task['id']]:
        raise ValueError('Prepared source differs from the frozen shared source manifest')
    cwd = directory / 'agent-cwd'
    cwd.mkdir()
    started = time.perf_counter()
    prompt = system_prompt(arm) + '\n\n' + issue_prompt(task['issue'], orient(session))
    allowed = {d['name'] for d in tool_definitions(arm)}
    broker_args = [str(BASE / 'benchmark/codex_broker_mcp.py'), task['id'], identifier,
                   '--arm', arm, '--catalog', str(BASE / spec['catalog']),
                   '--graf-binary', str(BASE / spec['graf_binary']),
                   '--max-actions', str(spec['max_actions_per_attempt'])]
    broker_args.extend(['--graphify-adapter', session.graphify_adapter])
    if spec.get('require_test_before_finish', False):
        broker_args.append('--require-test-before-finish')
    if spec.get('show_action_budget', False):
        broker_args.append('--show-action-budget')
    summaries, calls, error = invoke_native(directory / 'call-001', cwd, spec, prompt, broker_args, allowed)
    # Reload state written by the independent MCP process before grading.
    session = task_session.Session(task, identifier, arm)
    agent_finished = session.closed
    if not agent_finished:
        session.call('finish', {})
    requests_path = directory / 'mcp-requests.jsonl'
    requests = [json.loads(line) for line in requests_path.read_text().splitlines()] if requests_path.exists() else []
    tool_discovery = discovery(directory / 'mcp-requests.jsonl.control.jsonl', arm)
    if not tool_discovery['valid']:
        error = 'Invalid tool discovery: ' + '; '.join(tool_discovery['errors']) + (f'; native error: {error}' if error else '')
    if len(calls) != len(requests):
        error = error or 'MCP stream/broker request count mismatch'
    native_actions = Counter(json.dumps([c.get('tool'), c.get('arguments') or {}], sort_keys=True) for c in calls)
    broker_actions = Counter(json.dumps([r['request']['params'].get('name'), r['request']['params'].get('arguments') or {}], sort_keys=True) for r in requests)
    if native_actions != broker_actions:
        error = error or 'MCP stream/broker action mismatch'
    if sum(u['input_tokens'] + u['output_tokens'] for u in summaries) > spec['token_stop_threshold_per_attempt']:
        error = error or 'observed token stop threshold reached after native turn; may overshoot'
    if not requests:
        error = error or 'No native broker tools called'
    limit_hit = any(r['blocked'] for r in requests)
    if limit_hit:
        error = error or 'action limit reached'
    final = {p: hashlib.sha256(t.encode()).hexdigest() for p, t in load_documents(session.root).items()}
    (directory / 'final-source-hashes.json').write_text(json.dumps(final, sort_keys=True, indent=2) + '\n')
    tools = [json.loads(line) for line in session.events.read_text().splitlines()]
    trace = directory / 'trace.jsonl'
    trace.write_text(''.join(json.dumps(event) + '\n' for event in [*summaries, *tools]))
    invocation_dirs = list(directory.glob('call-*'))
    # Invalid/failed invocations may have usage in raw streams, but are never
    # silently treated as zero-cost successful calls in the aggregate.
    usage_complete = len(summaries) == len(invocation_dirs)
    verdicts = [event for event in tools if event['type'] == 'verification']
    summary = {'task': task['id'], 'attempt': attempt, 'trial': identifier, 'arm': arm,
               'graphify_adapter': session.graphify_adapter,
               'model': spec['model'], 'error': error, 'tool_discovery': tool_discovery,
               'passed': len(verdicts) == 1 and verdicts[0]['passed'] is True,
               'agent_called_finish': agent_finished, 'wall_s': time.perf_counter() - started,
               'invocations': len(invocation_dirs), 'completed_usage_records': len(summaries),
               'reported_usage_complete': usage_complete, 'native_mcp_calls': len(calls),
               'broker_requests': len(requests), 'blocked_broker_requests': sum(r['blocked'] for r in requests),
               'policy_rejected_requests': sum(r.get('policy_rejected', False) for r in requests),
               'failed_broker_requests': sum(r['reply'].get('result', {}).get('isError', False) for r in requests),
               'native_reply_content_tokens_cl100k': sum(len(tokens(c.get('text', ''))) for r in requests
                   for c in r['reply'].get('result', {}).get('content', []) if c.get('type') == 'text'),
               'internal_model_request_count': None, 'subscription_charge_usd': None,
               'api_equivalent_cost_usd': None, 'infrastructure_error': session.infrastructure_failed,
               'audit': audit_trace(trace) if summaries else None,
               'promotion_eligible': False}
    (directory / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
    return summary


def report(spec, summaries):
    planned = entries(spec)
    by_arm = {}
    for arm in spec['arms']:
        runs = [s for s in summaries if s['arm'] == arm]
        by_arm[arm] = {'completed_trials': len(runs), 'verifier_passes': sum(s['passed'] for s in runs),
                       'recorded_tokens': (sum((s['audit'] or {}).get('total_tokens', 0) for s in runs)
                                           if runs and all(s['reported_usage_complete'] for s in runs) else None),
                       'all_invocations_have_usage': bool(runs) and all(s['reported_usage_complete'] for s in runs)}
    result = {'batch': spec['id'], 'planned_trials': len(planned), 'completed_trials': len(summaries),
            'arms': by_arm, 'cases': summaries, 'product_promotion_pass': False,
            'agent_efficiency_acceptance_pass': False,
            'blockers': [f"{len(spec['task_ids'])} reused task(s), insufficient for promotion or statistical inference",
                         'Internal provider request counts and subscription charges unmeasured',
                         *(['Missing trials'] if len(summaries) != len(planned) else []),
                         *(['Incomplete invocation usage'] if any(not s['reported_usage_complete'] for s in summaries) else [])]}
    if 'solver_validation' in spec:
        result['solver_validation'] = evaluate(spec, summaries)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    os.environ['RIPGREP_CONFIG_PATH'] = '/dev/null'
    spec = json.loads((BASE / 'benchmark/preregistration.json').read_text())['codex_native_diagnostic']
    if spec.get('require_tool_discovery') is not True or 'benchmark/codex_discovery.py' not in spec['implementation_sha256']:
        raise SystemExit('New runs require a new freeze with require_tool_discovery and the discovery validator pinned')
    if spec.get('graphify_adapter', {}).get('id') != 'native-cli-v2':
        raise SystemExit('New native trials require an explicit native-cli-v2 adapter freeze')
    validate_adapter_freeze(spec, 'native-cli-v2')
    catalog = BASE / spec['catalog']
    if digest(catalog) != spec['catalog_sha256']:
        raise SystemExit('Catalog differs from freeze')
    for name, expected in spec['implementation_sha256'].items():
        if digest(BASE / name) != expected:
            raise SystemExit(f'Implementation differs from freeze: {name}')
    runtime = subprocess.run([spec['python'], '-c', 'import tiktoken, networkx'], capture_output=True, text=True)
    if runtime.returncode:
        raise SystemExit('Frozen broker Python lacks benchmark dependencies: ' + runtime.stderr)
    binary = BASE / spec['graf_binary']
    binaries = {'graf_current_orient': binary, 'graphify_orient': BASE / '.research/venv/bin/graphify',
                'search_only': Path(spec['search_binary'])}
    for arm, expected in spec['backend_sha256'].items():
        if preflight(arm, binaries[arm]) != expected:
            raise SystemExit(f'Backend differs from freeze: {arm}')
    if subprocess.check_output(['codex', '--version'], text=True).strip() != spec['solver_version']:
        raise SystemExit('Codex version differs from freeze')
    # Process-local override only: legacy t6 continues to use its original pin.
    task_session.GRAF_T6 = binary
    by_id = {task['id']: task for task in tasks(catalog)}
    ordered = entries(spec)
    summaries, pending = [], []
    for task_id, k, arm in ordered:
        identifier = f"{spec['id']}-{task_id}-{arm}-{k}"
        path = BASE / '.research/trials' / identifier / 'summary.json'
        if path.exists():
            summaries.append(json.loads(path.read_text()))
        else:
            pending.append((task_id, k, arm))
    if args.dry_run:
        print(json.dumps({'spec': spec, 'frozen_order': ordered, 'pending': pending}, indent=2))
        return 0
    output = BASE / 'results' / f"{spec['id']}.json"
    def save():
        output.write_text(json.dumps(report(spec, summaries), indent=2) + '\n')
    save()
    for task_id, k, arm in pending[:args.limit]:
        total = sum((s['audit'] or {}).get('total_tokens', 0) for s in summaries)
        if total >= spec['token_stop_threshold_batch']:
            print('Batch observed token stop threshold reached; remaining trials preserved as missing', flush=True)
            break
        directory = BASE / '.research/trials' / f"{spec['id']}-{task_id}-{arm}-{k}"
        if directory.exists():
            raise SystemExit(f'Partial trial exists; inspect before continuing: {directory}')
        summary = trial(spec, by_id[task_id], k, arm)
        summaries.append(summary)
        save()
        print(json.dumps(summary), flush=True)
        if summary['infrastructure_error'] or not summary['reported_usage_complete'] or (
                summary['error'] and not summary['error'].startswith(('action limit', 'observed token'))):
            raise SystemExit('Diagnostic stopped on infrastructure/provider/accounting error; artifacts retained')
    return 0


if __name__ == '__main__':
    main()
