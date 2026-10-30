"""Freeze the t12 catalog (natural_tasks_t12): fail-to-pass candidates filled by the v95 rule, newest merge first,
at most 8 per repository and 16 per language in repository_order."""
import json
from pathlib import Path

from t11_catalog import PER_LANGUAGE, PER_REPOSITORY, merge_dates

BASE = Path(__file__).resolve().parents[1]
HISTORY = BASE / ".research/history"
OUT = BASE / "benchmark/natural-tasks-t12.json"


def passing():
    """Each candidate the t12 check passed, by repository, with the runner and runtime it was checked under."""
    checked = {r["task"]: r["sanity_passed"] for r in json.loads((BASE / "results/natural-tasks-t12-f2p.json").read_text())}
    found = {}
    for task in json.loads((HISTORY / "t12-f2p-candidates.json").read_text()):
        if checked[task["id"]]:
            found.setdefault(task["repo"], []).append({k: v for k, v in task.items() if k not in ("merged_at", "pr")})
    return found


def main():
    spec = json.loads((BASE / "benchmark/preregistration.json").read_text())["agent_trials"]["natural_tasks_t12"]
    found, catalog = passing(), []
    for language, repos in spec["repository_order"].items():
        chosen = []
        for repo in repos:
            if repo in found and len(chosen) < PER_LANGUAGE:
                dates = merge_dates(repo)
                newest = sorted(found[repo], key=lambda t: dates[t["id"]], reverse=True)
                chosen += newest[:min(PER_REPOSITORY, PER_LANGUAGE - len(chosen))]
        catalog += chosen
        print(language, len(chosen))
    issues = [t["issue"] for t in catalog]
    if len(set(issues)) != len(issues):
        raise SystemExit("Two selected tasks share an issue")
    if len(catalog) < spec["minimum_tasks"]:
        raise SystemExit(f"Only {len(catalog)} tasks; natural_tasks_t12 requires {spec['minimum_tasks']}; nothing written")
    OUT.write_text(json.dumps(catalog, indent=2) + "\n")
    print(len(catalog), "tasks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
