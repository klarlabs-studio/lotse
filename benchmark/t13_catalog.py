"""Freeze the t13 catalog (natural_tasks_t13, v133): the t12 fail-to-pass passes that t12's catalog left unused,
filled by the same v95 rule; a task sharing an issue with a t12 task is excluded."""
import json
from pathlib import Path

from t12_catalog import fill, passing

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / "benchmark/natural-tasks-t13.json"


def unused(found, used):
    ids, issues = {t["id"] for t in used}, {t["issue"] for t in used}
    return {repo: kept for repo, tasks in found.items()
            if (kept := [t for t in tasks if t["id"] not in ids and t["issue"] not in issues])}


def main():
    spec = json.loads((BASE / "benchmark/preregistration.json").read_text())["agent_trials"]["natural_tasks_t13"]
    t12 = json.loads((BASE / "benchmark/natural-tasks-t12.json").read_text())
    catalog = fill(spec["repository_order"], unused(passing(), t12))
    if len(catalog) < spec["minimum_tasks"]:
        raise SystemExit(f"Only {len(catalog)} tasks; natural_tasks_t13 requires {spec['minimum_tasks']}; nothing written")
    OUT.write_text(json.dumps(catalog, indent=2) + "\n")
    print(len(catalog), "tasks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
