"""List the t12 candidates the fail-to-pass check runs (natural_tasks_t12): judged bugs, with the runner and runtime the
miner now assigns; a candidate whose changed tests have no runner is not checked."""
import json
from pathlib import Path

from mine_natural_tasks import JS_RUNTIME, PYTHON_RUNTIME, verify_command
from t11_f2p_candidates import merge_details

BASE = Path(__file__).resolve().parents[1]
HISTORY = BASE / ".research/history"
OUT = HISTORY / "t12-f2p-candidates.json"


def main():
    rows = json.loads((HISTORY / "t12-judgment-candidates.json").read_text())["candidates"]
    judged = json.loads((BASE / "results/natural-tasks-t12-judgments.json").read_text())["judgments"]
    checked, skipped, details = [], [], {}
    for row in rows:
        if not judged[row["id"]]["bug"]:
            continue
        task, repo = dict(row["task"]), row["repo"]
        task["verify"] = verify_command(repo, sorted({t["path"] for t in task["hidden_tests"]}))
        if task["verify"] is None:
            skipped.append(row["id"])
            continue
        task.pop("runtime", None), task.pop("python_runtime", None)
        task.update({"runtime": JS_RUNTIME[repo]} if repo in JS_RUNTIME else {})
        task.update({"python_runtime": PYTHON_RUNTIME[repo]} if repo in PYTHON_RUNTIME else {})
        if repo not in details:
            details[repo] = merge_details(repo)
        checked.append({**task, **details[repo][row["id"]]})
    OUT.write_text(json.dumps(checked, indent=1) + "\n")
    print(len(checked), "to check;", len(skipped), "without a runner:", ", ".join(skipped))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
