"""List batch t12's judgment candidates (natural_tasks_t12, v128); no solver, no test run.

    python benchmark/t12_candidates.py

Per repository in repository_order: candidates that met the automatic criteria, newest merge first, in no earlier
catalog; the first 14 go to the issue-only bug judgment. Writes .research/history/t12-judgment-candidates.json.
"""
import ast
import json
from pathlib import Path

from pointer_free_screen import used_ids
from t11_candidates import PER_REPOSITORY, shown_issue

BASE = Path(__file__).resolve().parents[1]
HISTORY = BASE / ".research/history"
OUT = HISTORY / "t12-judgment-candidates.json"


def judgment_candidates(repository_order):
    """Automatic-criteria candidates per repository in no earlier catalog, newest merge first; the first 14 are judged."""
    used, rows, counts = used_ids(), [], {}
    for language, order in repository_order.items():
        for repo in order:
            path = HISTORY / f"{repo}-candidates.json"
            records = json.loads(path.read_text())["candidates"] if path.is_file() else []
            seen, kept = set(), []
            for record in records:
                if record.get("outcome") != "automatic criteria met":
                    continue
                task = record["task"] if isinstance(record["task"], dict) else ast.literal_eval(record["task"])
                if task["id"] in used or task["id"] in seen:  # Duplicate issues keep the newest merge.
                    continue
                seen.add(task["id"])
                kept.append(task)
            counts[repo] = len(kept)
            rows += [{"id": t["id"], "repo": repo, "language": language, "shown_issue": shown_issue(t["issue"]), "task": t}
                     for t in kept[:PER_REPOSITORY]]
    return counts, rows


def main():
    spec = json.loads((BASE / "benchmark/preregistration.json").read_text())["agent_trials"]["natural_tasks_t12"]
    counts, rows = judgment_candidates(spec["repository_order"])
    OUT.write_text(json.dumps({"automatic_candidates": counts, "candidates": rows}, indent=1) + "\n")
    print(json.dumps(counts), len(rows), "to judge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
