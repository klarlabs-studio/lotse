"""List the t15 candidates the fail-to-pass check runs (natural_tasks_t15): judged bugs, with the runner and runtime
the miner now assigns; a candidate whose changed tests have no runner is not checked. A re-check after a recorded
runtime repair lists only the named repositories' candidates, in t15-f2p-recheck.json.

    python benchmark/t15_f2p_candidates.py [repo ...]
"""
import json
from pathlib import Path
import sys

from mine_natural_tasks import JS_RUNTIME, PYTHON_RUNTIME, verify_command
from t11_f2p_candidates import merge_details

BASE = Path(__file__).resolve().parents[1]
HISTORY = BASE / ".research/history"
OUT = HISTORY / "t15-f2p-candidates.json"
CORPORA = BASE / ".research/corpora"


def utf8(path):
    try:
        path.read_text()
        return True
    except UnicodeDecodeError:
        return False


def main(repos=()):
    rows = json.loads((HISTORY / "t15-judgment-candidates.json").read_text())["candidates"]
    judged = json.loads((BASE / "results/natural-tasks-t15-judgments.json").read_text())["judgments"]
    checked, skipped, absent, details = [], [], [], {}
    for row in rows:
        if not judged[row["id"]]["bug"]:
            continue
        task, repo = dict(row["task"]), row["repo"]
        if any(not (CORPORA / repo / t["path"]).is_file() for t in task["hidden_tests"]):
            absent.append(row["id"])  # A hidden test file absent at the pin cannot be checked (v130).
            continue
        if any(not utf8(CORPORA / repo / t["path"]) for t in task["hidden_tests"]):
            skipped.append(row["id"])  # The natural_tasks_t10 protocol rejects binary changes (v148).
            continue
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
    if repos:
        checked = [task for task in checked if task["repo"] in repos]
    (HISTORY / "t15-f2p-recheck.json" if repos else OUT).write_text(json.dumps(checked, indent=1) + "\n")
    print(len(checked), "to check;", len(skipped), "without a runner or with binary tests:", ", ".join(skipped))
    print(len(absent), "with a hidden test file absent at the pin:", ", ".join(absent))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
