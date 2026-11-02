"""Development only, no model calls: does `graf failure` point a failed test run at the code a fix changes?

    python benchmark/failure_check.py t14 t15

Replays every failed test run of the batches' attempts (all arms; screens excluded): the run's output, from the
transcript, goes to `graf failure` (target/failure, a graph-triage build with the command) in the task's prepared
workspace, the view the agent starts from, with triage's MCP budget. Per failure it scores the answer as
benchmark/excerpt_check.py scores excerpts (in a faulted file, first in one, overlapping the faulted lines) and keeps
the look-up calls the agent made before its next edit (benchmark/debug_phase.py). The workspace is not the agent's
edited one, so line numbers can drift. t14 and t15 have been tested: this decides whether a pilot is worth running and
supports no claim. Writes results/failure-check.json.
"""
import contextlib
import io
import json
import shutil
import subprocess
import sys

from agent_trials import BASE, prepare, task_faults, tasks
from debug_phase import attempt_rows, test_output
from excerpt_check import fault_lines, scored
from loop_phases import ARMS, calls
from triage_replay import MCP_BUDGET

GRAF = BASE / "target/failure/release/graf"


def failures(batch):
    """{task: [(trial, arm, failure number, output, look-up calls before the next edit)]}."""
    catalog = {t["id"]: t for t in tasks(BASE / f"benchmark/natural-tasks-{batch}.json")}
    out = {}
    for summary in sorted((BASE / ".research/trials").glob(f"{batch}-*/summary.json")):
        directory = summary.parent
        arm = next((a for a in ARMS if f"-{a}-" in directory.name), None)
        if arm is None or directory.name.startswith(f"{batch}-screen-"):
            continue
        task = json.loads(summary.read_text())["task"]
        seq = calls(directory / "agent-stream.jsonl")
        faulted = {f["path"] for f in task_faults(catalog[task])}
        outputs = [o for n, _, t in seq if n == "test" for p, o in [test_output(t)] if p is False]
        for k, (output, row) in enumerate(zip(outputs, attempt_rows(seq, faulted))):
            out.setdefault(task, []).append((directory.name, arm, k + 1, output, row["relocate_calls"]))
    return catalog, out


def replay(task, runs):
    trial = f"failure-check-{task['id']}"
    with contextlib.redirect_stdout(io.StringIO()):
        prepare(task, trial)
    root = BASE / ".research/trials" / trial / "workspace"
    hidden = {h["path"] for h in task.get("hidden_tests", [])}
    rows = []
    try:
        faulted = fault_lines(root, task)
        subprocess.run([str(GRAF), "--root", str(root), "index"], capture_output=True, check=True)
        for trial_id, arm, k, output, relocate in runs:
            answer = json.loads(subprocess.run([str(GRAF), "--root", str(root), "failure", output.replace("\0", ""), "--budget",
                                                str(MCP_BUDGET)], capture_output=True, text=True, check=True).stdout)
            rows.append({"task": task["id"], "trial": trial_id, "arm": arm, "failure": k, "relocate_calls": relocate,
                         "tests_found": len(answer["tests"]), "tokens": answer["context_tokens"],
                         **scored(answer["units"], faulted, hidden)})
    finally:
        shutil.rmtree(root.parent)
    return rows


def summarize(rows):
    keys = ("in_faulted_file", "first_in_faulted_file", "covers_fault")
    tokens = sorted(r["tokens"] for r in rows)
    return {"failures": len(rows), "empty": sum(r["excerpts"] == 0 for r in rows),
            "with_tests_found": sum(r["tests_found"] > 0 for r in rows),
            **{k: sum(r[k] for r in rows) for k in keys},
            "median_tokens": tokens[len(tokens) // 2] if tokens else 0,
            "relocate_calls": sum(r["relocate_calls"] for r in rows),
            "relocate_calls_after_covering_answers": sum(r["relocate_calls"] for r in rows if r["covers_fault"]),
            "relocate_calls_after_answers_in_a_faulted_file": sum(r["relocate_calls"] for r in rows
                                                                  if r["in_faulted_file"])}


def main(batches):
    rows = []
    for batch in batches:
        catalog, runs = failures(batch)
        for task_id in sorted(runs):
            rows += [dict(r, batch=batch) for r in replay(catalog[task_id], runs[task_id])]
            print(batch, task_id, len(rows), flush=True)
    summary = {"all": summarize(rows), **{arm: summarize([r for r in rows if r["arm"] == arm]) for arm in ARMS},
               "first_failure": summarize([r for r in rows if r["failure"] == 1])}
    (BASE / "results/failure-check.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1) + "\n")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
