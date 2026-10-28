"""Command contract for the next agent experiment; does not run a solver.

These are the evidence commands tested in retrieval confirmation v64. The
historical agent broker's `units` command is a different retrieval arm. Keep
this contract separate so historical frozen runners remain reproducible.
"""
from pathlib import Path


QUERY_ARGS = {
    'graf_v59': ['evidence-rescue', '{query}', '--budget', '{budget}', '--limit', '20', '--text'],
    'graf_v61': ['evidence-selective', '{query}', '--budget', '{budget}', '--limit', '20', '--text'],
    'graphify': ['query', '{query}', '--graph', '{graph}', '--budget', '{budget}'],
}


def query_command(plan, arm_id, root, graph_path, query, budget, base):
    """Build argv only, rejecting silent backend changes and budget asymmetry.

    Substitutions replace whole arguments; query text is never shell code or a
    format string. Graphify's requested budget remains native and approximate.
    A future broker must log the complete native response, including overruns.
    """
    if arm_id not in QUERY_ARGS:
        raise ValueError('Unknown retrieval arm')
    if not isinstance(query, str) or not query.strip():
        raise ValueError('Query must be nonempty text')
    ceiling = plan.get('comparison', {}).get('shared_output_budget')
    if type(ceiling) is not int or type(budget) is not int or not 1 <= budget <= ceiling <= 8000:
        raise ValueError('Budget must be within the shared frozen limit')
    arms = plan.get('arms', [])
    if not isinstance(arms, list):
        raise ValueError('Arms must be a list')
    matches = [a for a in arms if isinstance(a, dict) and a.get('id') == arm_id]
    if len(matches) != 1:
        raise ValueError('Exactly one configuration per arm is required')
    arm = matches[0]
    if arm.get('query_args') != QUERY_ARGS[arm_id]:
        raise ValueError('Query command differs from the evidence contract')
    binary = arm.get('binary')
    if not isinstance(binary, str) or not binary or '\x00' in binary:
        raise ValueError('Missing backend executable')
    binary = Path(binary)
    if not binary.is_absolute():
        binary = Path(base) / binary
    values = {'{query}': query, '{budget}': str(budget), '{graph}': str(graph_path)}
    args = [values.get(value, value) for value in arm['query_args']]
    prefix = ['--root', str(root)] if arm_id.startswith('graf_') else []
    return [str(binary), *prefix, *args]
