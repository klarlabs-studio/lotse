"""List batch t11's judgment candidates (natural_tasks_t11, v121); no solver, no test run.

    python benchmark/t11_candidates.py

Per repository in repository_order: candidates that met the automatic criteria, newest merge first, in no earlier
catalog, pointer-free under v120; the first 14 go to the issue-only bug judgment. A candidate judged or
fail-to-pass checked for t10 carries that outcome. Writes .research/history/t11-judgment-candidates.json.
"""
import ast
import json
from pathlib import Path

from pointer_free_screen import DICTIONARY, pointer_free, used_ids

BASE = Path(__file__).resolve().parents[1]
HISTORY = BASE / ".research/history"
OUT = HISTORY / "t11-judgment-candidates.json"
PER_REPOSITORY = 14
SHOWN = 900  # Characters of the issue body the judgment sees, after its title.


def t10_outcomes():
    judged = json.loads((BASE / "results/natural-tasks-t10-judgments.json").read_text())["judgments"]
    if isinstance(judged, str):  # Older records stored the judgments as a Python repr.
        judged = ast.literal_eval(judged)
    checked = {}
    for name in ("natural-tasks-t10-f2p.json", "natural-tasks-t10-f2p-recheck.json"):
        for row in json.loads((BASE / "results" / name).read_text()):
            checked[row["task"]] = row["sanity_passed"]  # The recheck, read last, supersedes the first check.
    return judged, checked


def shown_issue(issue):
    title, _, body = issue.partition("\n\n")
    return f"{title}\n\n{body[:SHOWN]}"


def main():
    spec = json.loads((BASE / "benchmark/preregistration.json").read_text())["agent_trials"]
    repositories = {**spec["natural_tasks_t10"]["repositories"], **spec["natural_tasks_t11"]["repositories"]}
    words = {w.strip().lower() for w in DICTIONARY.read_text().splitlines()}
    used, (judged, checked) = used_ids(), t10_outcomes()
    rows, counts = [], {}
    for language, order in spec["natural_tasks_t11"]["repository_order"].items():
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
                if pointer_free(task, repositories[repo]["language"], words)["pointer_free"]:
                    kept.append(task)
            counts[repo] = len(kept)
            for task in kept[:PER_REPOSITORY]:
                rows.append({"id": task["id"], "repo": repo, "language": language, "shown_issue": shown_issue(task["issue"]),
                             "t10_judgment": judged.get(task["id"]), "t10_fail_to_pass": checked.get(task["id"]),
                             "task": task})
    OUT.write_text(json.dumps({"pointer_free_candidates": counts, "candidates": rows}, indent=1) + "\n")
    print(json.dumps(counts), len(rows), "to judge,", sum(r["t10_judgment"] is not None for r in rows), "judged for t10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
