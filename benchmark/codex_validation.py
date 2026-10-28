"""Descriptive solver readiness on development tasks; never product acceptance."""
from collections import Counter


def evaluate(spec, summaries):
    rule = spec['solver_validation']
    expected = {(t, k, arm) for t in spec['task_ids']
                for k in range(1, spec['attempts'] + 1) for arm in spec['arms']}
    counts = Counter((s['task'], s['attempt'], s['arm']) for s in summaries)
    complete = set(counts) == expected and all(n == 1 for n in counts.values())
    protocol = lambda s: (s.get('reported_usage_complete') is True and s.get('error') is None
                          and s.get('infrastructure_error') is False and s.get('agent_called_finish') is True
                          and (not spec.get('require_tool_discovery', False)
                               or s.get('tool_discovery', {}).get('valid') is True))
    arms = {}
    for arm in spec['arms']:
        runs = [s for s in summaries if s['arm'] == arm]
        by_task = {t: sum(s.get('passed') is True for s in runs if s['task'] == t)
                   for t in spec['task_ids']}
        arms[arm] = dict(completed=len(runs), protocol_complete=sum(protocol(s) for s in runs),
                         verifier_passes=sum(by_task.values()), task_passes=by_task)
    protocol_pass = complete and all(protocol(s) for s in summaries)
    behavioral_pass = complete and all(
        arm['verifier_passes'] >= rule['min_passes_per_arm'] and
        all(n >= rule['min_passes_per_task_arm'] for n in arm['task_passes'].values())
        for arm in arms.values())
    losses = []
    unique = {key: next(s for s in summaries if (s['task'], s['attempt'], s['arm']) == key)
              for key, n in counts.items() if n == 1}
    for task in spec['task_ids']:
        for k in range(1, spec['attempts'] + 1):
            graf = unique.get((task, k, 'graf_current_orient'))
            if graf is not None and graf.get('passed') is False:
                for arm in spec['arms']:
                    other = unique.get((task, k, arm))
                    if arm != 'graf_current_orient' and other and other.get('passed') is True:
                        losses.append(dict(task=task, attempt=k, comparator=arm))
    return dict(expected_trials=len(expected), complete_unique_workload=complete, arms=arms,
                protocol_validation_pass=protocol_pass, behavioral_validation_pass=behavioral_pass,
                solver_readiness_pass=protocol_pass and behavioral_pass, severe_quality_losses=losses,
                product_promotion_pass=False, agent_efficiency_acceptance_pass=False,
                interpretation='Development solver validation only. Readiness does not establish superiority, '
                               'statistical significance, held-out quality or agent token savings.')
