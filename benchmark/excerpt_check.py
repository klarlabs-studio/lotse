"""Development only, no model calls: do triage-excerpts builds show the code a fix changes?

    python benchmark/excerpt_check.py t14 t15

Replays each graf_installed attempt's first triage call from the batches, in the task's prepared (faulted) workspace,
with the same text and budget, under three builds: the t15 candidate (.research/bin/graf-graph-triage) and the
graph-triage builds with triage-excerpts and with triage-tests (target/triage-excerpts, target/triage-tests). Per call
and build it scores the "Likely code" excerpts: whether one is in a faulted file, whether the first is, whether one
overlaps the faulted lines (the fault's text in the workspace), whether one is test code, and whether one is in a
hidden test's file. t14 and t15 have been tested, so this chooses a variant for an exploratory pilot and supports no
claim. Writes results/excerpt-check.json.
"""
import contextlib
import io
import json
import re
import shutil
import subprocess
import sys

from agent_trials import BASE, prepare, task_faults, tasks
from triage_replay import MCP_BUDGET, first_triage, triage

BUILDS = {"graph_triage": BASE / ".research/bin/graf-graph-triage",
          "excerpts": BASE / "target/triage-excerpts/release/graf",
          "excerpts_tests": BASE / "target/triage-tests/release/graf"}
TEST = re.compile(r"(^|/)(tests?|__tests__)/|_test\.|(^|/)test_|\.test\.|\.spec\.")


def queries(batch):
    out = {}
    for summary in sorted((BASE / ".research/trials").glob(f"{batch}-*-graf_installed-*/summary.json")):
        record = json.loads(summary.read_text())
        call = first_triage(summary.parent / "agent-stream.jsonl")
        if call:
            out.setdefault(record["task"], []).append((record["trial"], call["issue"],
                                                       int(call.get("budget", MCP_BUDGET))))
    return out


def fault_lines(root, task):
    """{path: (first, last)} 1-based lines of each fault's text in the faulted workspace."""
    lines = {}
    for fault in task_faults(task):
        if fault.get("added"):
            continue
        text = (root / fault["path"]).read_text()
        at = text.find(fault["after"])
        if at < 0:
            continue
        first = text.count("\n", 0, at) + 1
        last = first + max(fault["after"].count("\n") - (fault["after"].endswith("\n")), 0)
        lo, hi = lines.get(fault["path"], (first, last))
        lines[fault["path"]] = (min(lo, first), max(hi, last))
    return lines


def scored(units, faulted, hidden):
    return {"excerpts": len(units),
            "in_faulted_file": any(u["path"] in faulted for u in units),
            "first_in_faulted_file": bool(units) and units[0]["path"] in faulted,
            "covers_fault": any(u["path"] in faulted and u["start"] <= faulted[u["path"]][1]
                                and faulted[u["path"]][0] <= u["end"] for u in units),
            "test_excerpts": sum(bool(TEST.search(u["path"])) for u in units),
            "in_hidden_test_file": any(u["path"] in hidden for u in units)}


def replay(task, calls):
    trial = f"excerpt-check-{task['id']}"
    with contextlib.redirect_stdout(io.StringIO()):
        prepare(task, trial)
    root = BASE / ".research/trials" / trial / "workspace"
    hidden = {h["path"] for h in task.get("hidden_tests", [])}
    rows = []
    try:
        faulted = fault_lines(root, task)
        results = {}
        for name, graf in BUILDS.items():  # One build's index at a time.
            subprocess.run([str(graf), "--root", str(root), "index"], capture_output=True, check=True)
            results[name] = [triage(graf, root, issue, budget) for _, issue, budget in calls]
        for i, (attempt, _, budget) in enumerate(calls):
            rows.append({"task": task["id"], "attempt": attempt, "budget": budget,
                         **{name: {**scored(results[name][i]["units"], faulted, hidden),
                                   "tokens": results[name][i]["context_tokens"]} for name in BUILDS}})
    finally:
        shutil.rmtree(root.parent)
    return rows


def main(batches):
    rows = []
    for batch in batches:
        catalog = {t["id"]: t for t in tasks(BASE / f"benchmark/natural-tasks-{batch}.json")}
        for task_id, calls in sorted(queries(batch).items()):
            rows += [dict(r, batch=batch) for r in replay(catalog[task_id], calls)]
            print(batch, task_id, len(rows), flush=True)
    summary = {name: {key: sum(r[name][key] for r in rows) for key in rows[0][name] if key != "tokens"}
               | {"median_tokens": sorted(r[name]["tokens"] for r in rows)[len(rows) // 2]} for name in BUILDS}
    summary["calls"] = len(rows)
    (BASE / "results/excerpt-check.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1) + "\n")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
