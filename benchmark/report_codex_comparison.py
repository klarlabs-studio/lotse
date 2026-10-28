"""Summarize saved matched solver trials without new model or retrieval calls."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from agent_trials import BASE


def comparison(report, spec):
    if report['batch'] != spec['id']:
        raise ValueError('Report and frozen spec batch IDs differ')
    arms = spec['arms']
    planned = {(task, attempt, arm) for task in spec['task_ids']
               for attempt in range(1, spec['attempts'] + 1) for arm in arms}
    counts = Counter((s['task'], s['attempt'], s['arm']) for s in report['cases'])
    if any(n != 1 for n in counts.values()) or not set(counts).issubset(planned):
        raise ValueError('Duplicate or unplanned trial; cannot summarize as matched evidence')
    indexed = {(s['task'], s['attempt'], s['arm']): s for s in report['cases']}
    groups = {(task, attempt) for task, attempt, _ in planned
              if all((task, attempt, arm) in indexed for arm in arms)}
    totals = {}
    for arm in arms:
        cases = [s for (task, attempt, a), s in indexed.items()
                 if a == arm and (task, attempt) in groups]
        usage_known = bool(cases) and all(s.get('reported_usage_complete') is True
            and isinstance((s.get('audit') or {}).get('total_tokens'), (int, float)) for s in cases)
        totals[arm] = dict(attempts=len(cases), verifier_passes=sum(s['passed'] is True for s in cases),
            reported_tokens=sum(s['audit']['total_tokens'] for s in cases) if usage_known else None,
            recorded_native_tool_calls=sum(s['native_mcp_calls'] for s in cases),
            recorded_cli_invocations=sum(s['invocations'] for s in cases),
            descriptive_wall_s=sum(s['wall_s'] for s in cases),
            attempts_with_errors=sum(s.get('error') is not None for s in cases),
            attempts_without_agent_finish=sum(s.get('agent_called_finish') is not True for s in cases))
    # Retain all planned cases, including incomplete groups; never make missing a fail or zero cost.
    cases = []
    for task, attempt, arm in sorted(planned):
        summary = indexed.get((task, attempt, arm))
        cases.append(dict(task=task, attempt=attempt, arm=arm,
                          included_in_shared_totals=(task, attempt) in groups,
                          status='recorded' if summary is not None else 'missing', summary=summary))
    return dict(batch=spec['id'], planned_trials=len(planned), recorded_trials=len(indexed),
        missing_trials=len(planned - set(indexed)), complete_matched_groups=len(groups),
        shared_totals=totals, cases=cases,
        solver_validation=report.get('solver_validation'),
        frozen_configuration={key:spec.get(key) for key in ('model', 'reasoning_effort', 'solver_version',
            'max_actions_per_attempt', 'token_stop_threshold_per_attempt', 'token_stop_threshold_batch',
            'require_test_before_finish', 'show_action_budget', 'backend_sha256')},
        product_promotion_pass=False, agent_efficiency_acceptance_pass=False,
        limitations='Development tasks only. Shared totals include only groups with every planned arm; '
            'failed and protocol-invalid attempts remain included. Batches are separate configurations, '
            'not pooled or causal controls. Reported tokens include cached input. Provider internal request '
            'counts and charges are unknown; agent savings remain unmeasured. Wall times are descriptive; '
            'warm shared OS/dependency caches and uncontrolled provider caches prevent a speed claim. '
            'Graphify is the frozen code-only baseline plus source reads, not documentation evaluation.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--batches', nargs='+', default=['codex-v1', 'codex-v2'])
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    reports, hashes = [], {}
    for batch in args.batches:
        if Path(batch).name != batch or batch in ('.', '..'):
            parser.error('Batch must be a name, not a path')
        paths = [BASE / 'results' / f'{batch}.json', BASE / '.research/evidence' / batch / 'spec.json']
        documents = []
        for path in paths:
            content = path.read_bytes()
            hashes[str(path.relative_to(BASE))] = hashlib.sha256(content).hexdigest()
            documents.append(json.loads(content))
        reports.append(comparison(*documents))
    output = dict(mode='saved_evidence_summary', model_calls=0, batches=reports,
                  source_sha256=hashes, generator_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  product_promotion_pass=False, agent_efficiency_acceptance_pass=False)
    args.output.write_text(json.dumps(output, indent=2, allow_nan=False) + '\n')
    print(json.dumps([{k:v for k,v in r.items() if k in ('batch', 'missing_trials', 'shared_totals')}
                      for r in reports], indent=2))


if __name__ == '__main__':
    main()
