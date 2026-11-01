"""The t15 screen (natural_tasks_t15.screen_v148): as the t14 screen (benchmark/t14_screen.py), one builtin attempt per
fail-to-pass candidate in the recorded fill order, keeping a candidate whose attempt made 15 or more tool calls.

    python benchmark/t15_screen.py [--dry-run]

The pool is the t15 fail-to-pass passes and the 13 t14 pool candidates the t14 screen never attempted (pool.existing).
Writes benchmark/natural-tasks-t15-screen.json (the ordered candidates) and results/t15-screen.json.
"""
import json
import sys

from agent_trials import BASE
from t11_catalog import merge_dates
import t14_screen
from t14_screen import passing

SCREEN_CATALOG = BASE / "benchmark/natural-tasks-t15-screen.json"
OUT = BASE / "results/t15-screen.json"


def existing():
    """The t14 pool candidates its screen never attempted (their repository or language was already full)."""
    screened = json.loads((BASE / "results/t14-screen.json").read_text())["candidates"]
    return [t for t in json.loads((BASE / "benchmark/natural-tasks-t14-screen.json").read_text())
            if t["id"] not in screened]


def ordered(spec):
    """{language: [tasks of each repository in fill order, newest merge first]}."""
    pool = {}
    for task in existing() + passing("results/natural-tasks-t15-f2p.json", "t15-f2p-candidates.json"):
        pool.setdefault(task["repo"], []).append(task)
    order = {}
    for language, repos in spec["fill_order"].items():
        order[language] = []
        for repo in repos:
            dates = merge_dates(repo)
            order[language] += sorted(pool.pop(repo, []), key=lambda t: dates[t["id"]], reverse=True)
    if pool:
        raise SystemExit(f"Candidates outside the fill order: {sorted(pool)}")
    return order


if __name__ == "__main__":
    sys.exit(t14_screen.main("t15", "screen_v148", ordered, SCREEN_CATALOG, OUT))
