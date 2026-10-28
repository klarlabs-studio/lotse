"""Run the separately frozen Codex diagnostic sequentially, preserving every attempt.

No changes to the Claude batches, their pins or their acceptance rules. The
structured-action protocol is a new solver harness and cannot be pooled with
Claude results. --limit only paces the frozen order, never selects tasks.
"""
import argparse
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
from codex_solver import invoke, usage_event
from run_agent_trial import issue_prompt, orient, preflight, system_prompt
import task_session
from task_session import prepare_session, tool_definitions
from retrieval import load_documents


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def entries(spec):
    result = [(task, k, arm) for task in spec['task_ids']
              for k in range(1, spec['attempts'] + 1) for arm in spec['arms']]
    random.Random(spec['seed']).shuffle(result)
    return result


def trial(spec, task, attempt, arm):
    identifier = f"{spec['id']}-{task['id']}-{arm}-{attempt}"
    with contextlib.redirect_stdout(io.StringIO()):
        session = prepare_session(task, identifier, arm)
    directory = session.directory
    initial = {p: hashlib.sha256(t.encode()).hexdigest() for p, t in load_documents(session.root).items()}
    (directory / 'initial-source-hashes.json').write_text(json.dumps(initial, sort_keys=True, indent=2) + '\n')
    if hashlib.sha256(json.dumps(initial, sort_keys=True).encode()).hexdigest() != spec['source_manifest_sha256'][task['id']]:
        raise ValueError('Prepared source differs from the frozen shared source manifest')
    cwd = directory / 'agent-cwd'
    cwd.mkdir()
    summaries, error = [], None
    started = time.perf_counter()
    transcript = {'instructions': system_prompt(arm), 'tools': tool_definitions(arm),
                  'issue': issue_prompt(task['issue'], orient(session)), 'history': []}
    allowed = {d['name'] for d in tool_definitions(arm)}
    for step in range(1, spec['max_actions_per_attempt'] + 1):
        remaining = spec['timeout_seconds_per_attempt'] - (time.perf_counter() - started)
        if remaining <= 0:
            error = 'attempt timeout'
            break
        try:
            operation, arguments, usage = invoke(
                directory / f'call-{step:03}', cwd, json.dumps(transcript, ensure_ascii=False),
                spec['model'], spec['reasoning_effort'], allowed,
                min(remaining, spec['timeout_seconds_per_invocation']))
            summaries.append(usage)
            # Usage is recorded before dispatch, even when the budget stops execution.
            if sum(e['input_tokens'] + e['output_tokens'] for e in summaries) > spec['token_stop_threshold_per_attempt']:
                error = 'observed token stop threshold reached; final invocation may overshoot'
                break
            response = session.call(operation, arguments)
            transcript['history'].append({'operation': operation, 'arguments': arguments, 'response': response})
            if session.closed or session.infrastructure_failed:
                break
        except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
            # An invalid action is still a paid/allowance-consuming invocation.
            raw_path = directory / f'call-{step:03}' / 'reported-usage.json'
            if raw_path.is_file() and len(summaries) < step:
                raw = json.loads(raw_path.read_text())
                if len(raw) == 1:
                    try:
                        summaries.append(usage_event(raw[0].get('usage', {}), spec['model']))
                    except ValueError:
                        pass  # Preserve unknown usage explicitly.
            error = f'{type(exc).__name__}: {exc}'
            break
    agent_finished = session.closed
    if not agent_finished:
        if error is None:
            error = 'action limit reached'
        session.call('finish', {})
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
               'model': spec['model'], 'error': error,
               'passed': len(verdicts) == 1 and verdicts[0]['passed'] is True,
               'agent_called_finish': agent_finished, 'wall_s': time.perf_counter() - started,
               'invocations': len(invocation_dirs), 'completed_usage_records': len(summaries),
               'reported_usage_complete': usage_complete,
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
    return {'batch': spec['id'], 'planned_trials': len(planned), 'completed_trials': len(summaries),
            'arms': by_arm, 'cases': summaries, 'product_promotion_pass': False,
            'agent_efficiency_acceptance_pass': False,
            'blockers': ['Reused three-task diagnostic, insufficient for promotion or statistical inference',
                         'Internal provider request counts and subscription charges unmeasured',
                         *(['Missing trials'] if len(summaries) != len(planned) else []),
                         *(['Incomplete invocation usage'] if any(not s['reported_usage_complete'] for s in summaries) else [])]}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    os.environ['RIPGREP_CONFIG_PATH'] = '/dev/null'
    spec = json.loads((BASE / 'benchmark/preregistration.json').read_text())['codex_diagnostic']
    catalog = BASE / spec['catalog']
    if digest(catalog) != spec['catalog_sha256']:
        raise SystemExit('Catalog differs from freeze')
    for name, expected in spec['implementation_sha256'].items():
        if digest(BASE / name) != expected:
            raise SystemExit(f'Implementation differs from freeze: {name}')
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
