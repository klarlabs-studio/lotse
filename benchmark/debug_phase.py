"""Exploratory, no model calls: what an attempt does after a failed test run, and what the failure output already said.

    python benchmark/debug_phase.py t14 t15

For every batch attempt (screens excluded) and each failed test run, the "re-locate" calls are the Read, Grep, Glob and
graf calls before the next edit. Per failure it records how many there were; the source files the failure output names
(traceback frames and path:line mentions, test files excluded); whether a faulted file is among them; and how many of
the re-locate Reads opened a file the output named. It sizes a tool that maps a failure to the code it exercises: calls
it could replace are re-locate calls, and it can only help where the output names, or leads to, the faulted code.
Writes results/debug-phase.json. Diagnostic only: it motivates a hypothesis, it supports no claim.
"""
import json
import re
import statistics
import sys

from agent_trials import BASE, task_faults, tasks
from loop_phases import ARMS, calls, relative

EDITS = ("Edit", "Write", "MultiEdit")
LOOKS = ("Read", "Grep", "Glob", "triage", "outline", "units", "search", "context", "symbols", "callers")
SOURCE = re.compile(r"\.(py|go|js|mjs|cjs|ts|tsx)$")
TEST = re.compile(r"(^|/)(tests?|__tests__|testing)/|_test\.|(^|/)test_|\.test\.|\.spec\.|conftest\.py$")
MENTION = re.compile(r"([\w./@-]+\.(?:py|go|js|mjs|cjs|ts|tsx))(?:\"?, line |:)(\d+)")


def test_output(text):
    """The test tool's verdict and output, from its (possibly JSON-wrapped) result text."""
    try:
        data = json.loads(text)
    except ValueError:
        return None, ""
    for block in data if isinstance(data, list) else [{"text": text}]:
        try:
            verdict = json.loads(block.get("text", "")) if isinstance(block, dict) else None
        except ValueError:
            continue
        if isinstance(verdict, dict) and "verification_passed" in verdict:
            return bool(verdict["verification_passed"]), verdict.get("output", "")
    return None, ""


def named_sources(output, files):
    """Workspace source files (not tests) the failure output mentions with a line number."""
    named = set()
    for path, _ in MENTION.findall(output):
        path = relative(path, "").lstrip("./")
        match = next((f for f in files if f == path or f.endswith("/" + path) or path.endswith("/" + f)), None)
        if match and not TEST.search(match):
            named.add(match)
    return named


def attempt_rows(seq, faulted):
    """One row per failed test run: what the output named and what the agent looked at before its next edit."""
    rows = []
    workspace = {relative(a.get("file_path", ""), "") for n, a, _ in seq if n in ("Read", *EDITS)}
    for i, (name, _, text) in enumerate(seq):
        if name != "test":
            continue
        passed, output = test_output(text)
        if passed is not False:
            continue
        named = named_sources(output, workspace | faulted)
        following = []
        for later, args, _ in seq[i + 1:]:
            if later in EDITS or later in ("test", "finish"):
                break
            if later in LOOKS:
                following.append((later, relative(args.get("file_path", ""), "")))
        reads = [path for later, path in following if later == "Read"]
        rows.append({"relocate_calls": len(following), "named_sources": len(named),
                     "names_faulted_file": bool(named & faulted),
                     "reads": len(reads), "reads_named": sum(path in named for path in reads),
                     "reads_faulted": sum(path in faulted for path in reads)})
    return rows


def main(batches):
    rows = []
    for batch in batches:
        catalog = {t["id"]: t for t in tasks(BASE / f"benchmark/natural-tasks-{batch}.json")}
        for summary in sorted((BASE / ".research/trials").glob(f"{batch}-*/summary.json")):
            directory = summary.parent
            arm = next((a for a in ARMS if f"-{a}-" in directory.name), None)
            if arm is None or directory.name.startswith(f"{batch}-screen-"):
                continue
            task = catalog[json.loads(summary.read_text())["task"]]
            faulted = {f["path"] for f in task_faults(task)}
            for k, row in enumerate(attempt_rows(calls(directory / "agent-stream.jsonl"), faulted)):
                rows.append({"batch": batch, "trial": directory.name, "arm": arm, "failure": k + 1, **row})
    report = {}
    for arm in ARMS:
        mine = [r for r in rows if r["arm"] == arm]
        attempts = {r["trial"] for r in mine}
        report[arm] = {"attempts_with_a_failure": len(attempts), "failures": len(mine),
                       "relocate_calls": sum(r["relocate_calls"] for r in mine),
                       "mean_relocate_calls_per_failure": round(statistics.mean(r["relocate_calls"] for r in mine), 2),
                       "failures_naming_a_source_file": sum(r["named_sources"] > 0 for r in mine),
                       "failures_naming_a_faulted_file": sum(r["names_faulted_file"] for r in mine),
                       "relocate_reads": sum(r["reads"] for r in mine),
                       "relocate_reads_of_named_files": sum(r["reads_named"] for r in mine),
                       "relocate_reads_of_faulted_files": sum(r["reads_faulted"] for r in mine),
                       "relocate_calls_after_failures_naming_no_source": sum(
                           r["relocate_calls"] for r in mine if r["named_sources"] == 0)}
    (BASE / "results/debug-phase.json").write_text(json.dumps({"summary": report, "rows": rows}, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
