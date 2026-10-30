"""Freeze the t11 catalog (natural_tasks_t11): fail-to-pass candidates filled by the v95 rule, newest merge first,
at most 8 per repository and 16 per language in repository_order."""
import ast
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
HISTORY = BASE / ".research/history"
OUT = BASE / "benchmark/natural-tasks-t11.json"
PER_REPOSITORY, PER_LANGUAGE = 8, 16


def merge_dates(repo):
    dates = {}
    for record in json.loads((HISTORY / f"{repo}-candidates.json").read_text())["candidates"]:
        if record.get("outcome") == "automatic criteria met":
            task = record["task"] if isinstance(record["task"], dict) else ast.literal_eval(record["task"])
            dates.setdefault(task["id"], record["merged_at"])
    return dates


def passing():
    """Each passing task by repository: a t10 fail-to-pass outcome carries over, else the t11 check decides."""
    rows = json.loads((HISTORY / "t11-judgment-candidates.json").read_text())["candidates"]
    checked = {r["task"]: r["sanity_passed"] for r in json.loads((BASE / "results/natural-tasks-t11-f2p.json").read_text())}
    tasks = {t["id"]: t for t in json.loads((HISTORY / "t11-f2p-candidates.json").read_text())}
    found = {}
    for row in rows:
        passed = row["t10_fail_to_pass"] if row["t10_fail_to_pass"] is not None else checked.get(row["id"], False)
        if passed:
            task = tasks.get(row["id"], row["task"])  # A t10 carry-over keeps its t10 runner and runtime.
            found.setdefault(row["repo"], []).append({k: v for k, v in task.items() if k not in ("merged_at", "pr")})
    return found


def main():
    order = json.loads((BASE / "benchmark/preregistration.json").read_text())["agent_trials"]["natural_tasks_t11"]["repository_order"]
    found, catalog = passing(), []
    for language, repos in order.items():
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
    OUT.write_text(json.dumps(catalog, indent=2) + "\n")
    print(len(catalog), "tasks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
