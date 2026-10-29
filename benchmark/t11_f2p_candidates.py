"""List the t11 candidates the fail-to-pass check runs (natural_tasks_t11, v121): judged bugs not yet checked for t10,
with the runner and runtime the miner now assigns."""
import ast
import json
from pathlib import Path

from mine_natural_tasks import JS_RUNTIME, PYTHON_RUNTIME, verify_command

BASE = Path(__file__).resolve().parents[1]
HISTORY = BASE / ".research/history"
OUT = HISTORY / "t11-f2p-candidates.json"
NO_RUNTIME = {"jquery", "chartjs"}  # natural_tasks_t10 (v116): their unit tests run in a browser after a build.


def merge_details(repo):
    records = json.loads((HISTORY / f"{repo}-candidates.json").read_text())["candidates"]
    details = {}
    for record in records:
        if record.get("outcome") == "automatic criteria met":
            task = record["task"] if isinstance(record["task"], dict) else ast.literal_eval(record["task"])
            details.setdefault(task["id"], {"merged_at": record["merged_at"], "pr": int(record["pr"])})
    return details


def main():
    rows = json.loads((HISTORY / "t11-judgment-candidates.json").read_text())["candidates"]
    judged = json.loads((BASE / "results/natural-tasks-t11-judgments.json").read_text())["judgments"]
    checked, skipped, details = [], {}, {}
    for row in rows:
        judgment = row["t10_judgment"] or judged[row["id"]]
        if not judgment["bug"] or row["t10_fail_to_pass"] is not None:
            continue  # Not a bug, or a t10 fail-to-pass outcome that carries over.
        task, repo = dict(row["task"]), row["repo"]
        task["verify"] = verify_command(repo, sorted({t["path"] for t in task["hidden_tests"]}))
        if repo in NO_RUNTIME or task["verify"] is None:
            skipped[row["id"]] = "no runtime for its tests"
            continue
        task.pop("runtime", None), task.pop("python_runtime", None)
        task.update({"runtime": JS_RUNTIME[repo]} if repo in JS_RUNTIME else {})
        task.update({"python_runtime": PYTHON_RUNTIME[repo]} if repo in PYTHON_RUNTIME else {})
        if repo not in details:
            details[repo] = merge_details(repo)
        checked.append({**task, **details[repo][row["id"]]})
    OUT.write_text(json.dumps(checked, indent=1) + "\n")
    print(len(checked), "to check;", len(skipped), "without a runtime:", ", ".join(skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
