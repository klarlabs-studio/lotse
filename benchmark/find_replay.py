"""Offline find replay screen (preregistration v112): would one `graf find` call have delivered the code agents read after a search?

    SOLVER_BOUNDED_PREFIX=True python benchmark/find_replay.py

Replays every search-then-read pair before the first edit in batches t7-t9 against the pinned graf find,
on each task's faulty state prepared again without a model. Writes results/find-replay-screen.json.
"""
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys

from agent_trials import BASE, prepare
from retrieval import tokens
from task_session import bounded, sha

BATCHES = ("t7", "t8", "t9")
FIND = BASE / ".research/bin/graf-95c9106"
FIND_SHA256 = "d9dacfbe8988fb0d74b03758b9332de3f666b227dd58451ce7e40729be2800cd"
OUT = BASE / "results/find-replay-screen.json"
HIT = re.compile(r"^([^:\n]+):(\d+):", re.M)
SHOWN = re.compile(r"^L(\d+): ", re.M)
REDUNDANT_RECALL = 0.8


def events(trial):
    return [json.loads(line) for line in (trial / "tool-events.jsonl").read_text().splitlines()]


def result(event):
    return ((event.get("response") or {}).get("result")) or {}


def pairs(trial):
    """Search calls with the reads right after them that open lines around one of its hits, before any edit."""
    found, calls = [], events(trial)
    for i, event in enumerate(calls):
        if event.get("operation") == "edit":
            break
        if event.get("operation") != "search" or not (event.get("response") or {}).get("ok"):
            continue
        hits = {(m.group(1), int(m.group(2))) for m in HIT.finditer(result(event).get("content", ""))}
        reads = []
        for later in calls[i + 1:]:
            if later.get("operation") != "read":
                break
            args = later.get("arguments") or {}
            start, count = args.get("start", 1), args.get("lines", 80)
            if (later.get("response") or {}).get("ok") and isinstance(start, int) and isinstance(count, int) \
                    and any(path == args.get("path") and start <= line < start + count for path, line in hits):
                reads.append(later)
        if reads:
            args = event.get("arguments") or {}
            found.append({"pattern": args.get("pattern"), "budget": args.get("budget", 2000),
                          "search_output": result(event).get("content", ""), "reads": reads})
    return found


def shown_lines(find_output, path):
    """Line numbers the find output shows for one path, from its blocks' L<n>: lines."""
    lines, current = set(), None
    for line in find_output.splitlines():
        header = re.match(r"^(\S+?):L\d+(-\d+)?[ :]", line)
        if header and not line.startswith("L"):
            current = header.group(1)
            compact = re.match(r"^\S+?:L(\d+): ", line)
            if compact and current == path:
                lines.add(int(compact.group(1)))
        elif current == path and (m := SHOWN.match(line)):
            lines.add(int(m.group(1)))
    return lines


def run_find(workspace, pattern, budget):
    command = [str(FIND), "--root", str(workspace), "find", "--text", "--budget", str(budget), "--", pattern]
    done = subprocess.run(command, capture_output=True, text=True, timeout=300)
    if done.returncode:
        return None, done.stderr.strip()
    return bounded(done.stdout, budget), None


def replay_task(batch, task, attempts):
    trial_id = f"findreplay-{batch}-{task['id']}"
    directory = BASE / ".research/trials" / trial_id
    if directory.exists():
        shutil.rmtree(directory)
    prepare(task, trial_id)
    workspace = directory / "workspace"
    rows = []
    try:
        for trial in attempts:
            arm = json.loads((trial / "summary.json").read_text())["arm"]
            for pair in pairs(trial):
                row = {"batch": batch, "task": task["id"], "trial": trial.name, "arm": arm,
                       "pattern": pair["pattern"], "budget": pair["budget"]}
                stale = [r for r in pair["reads"] if not (workspace / r["arguments"]["path"]).is_file()
                         or sha((workspace / r["arguments"]["path"]).read_text()) != result(r).get("source_hash")]
                if stale:
                    rows.append({**row, "skipped": "read source differs from the replay snapshot"})
                    continue
                output, error = run_find(workspace, pair["pattern"], pair["budget"])
                if output is None:
                    rows.append({**row, "invalid_pattern": error[:300]})
                    continue
                recalls = []
                for read in pair["reads"]:
                    returned = {int(m.group(1)) for m in SHOWN.finditer(result(read).get("content", ""))}
                    shown = shown_lines(output, read["arguments"]["path"])
                    recalls.append(len(returned & shown) / len(returned) if returned else 0.0)
                baseline = len(tokens(pair["search_output"])) + sum(len(tokens(result(r).get("content", ""))) for r in pair["reads"])
                rows.append({**row, "reads": len(recalls), "read_line_recall": recalls,
                             "redundant_reads": sum(r >= REDUNDANT_RECALL for r in recalls),
                             "find_tokens": len(tokens(output)), "search_and_read_tokens": baseline,
                             "token_ratio": len(tokens(output)) / baseline if baseline else None})
    finally:
        shutil.rmtree(directory)
    return rows


def summary(rows):
    scored = [r for r in rows if "read_line_recall" in r]
    reads = sum(r["reads"] for r in scored)
    redundant = sum(r["redundant_reads"] for r in scored)
    ratios = [r["token_ratio"] for r in scored if r["token_ratio"] is not None]
    invalid = sum("invalid_pattern" in r for r in rows)
    return {"pairs": len(rows), "scored_pairs": len(scored), "skipped_pairs": sum("skipped" in r for r in rows),
            "invalid_patterns": invalid, "reads": reads, "redundant_reads": redundant,
            "redundant_share": redundant / reads if reads else None,
            "mean_read_line_recall": statistics.mean(x for r in scored for x in r["read_line_recall"]) if reads else None,
            "median_token_ratio": statistics.median(ratios) if ratios else None}


def main():
    if os.environ.get("SOLVER_BOUNDED_PREFIX") != "True":
        sys.exit("set SOLVER_BOUNDED_PREFIX=True, as in batches t8 and t9")
    if sha_file(FIND) != FIND_SHA256:
        sys.exit("pinned graf find binary changed")
    prereg = json.loads((BASE / "benchmark/preregistration.json").read_text())
    rows = []
    for batch in BATCHES:
        catalog = {t["id"]: t for t in json.loads((BASE / prereg["agent_trials"]["batches"][batch]["catalog"]).read_text())}
        by_task = collections.defaultdict(list)
        for summary_path in sorted((BASE / ".research/trials").glob(f"{batch}-*/summary.json")):
            by_task[json.loads(summary_path.read_text())["task"]].append(summary_path.parent)
        for task_id, attempts in sorted(by_task.items()):
            rows.extend(replay_task(batch, catalog[task_id], attempts))
            print(f"{batch} {task_id}: {len(rows)} pairs so far", flush=True)
    all_rows = summary(rows)
    # Searches counted for the invalid-pattern share: every search call before the first edit.
    searches = sum(1 for b in BATCHES for p in (BASE / ".research/trials").glob(f"{b}-*/tool-events.jsonl")
                   for e in _before_edit(events(p.parent)) if e.get("operation") == "search")
    rule = prereg["find_replay_screen"]
    passed = (all_rows["redundant_share"] or 0) >= 0.40 and (all_rows["median_token_ratio"] or 99) <= 1.0 \
        and all_rows["invalid_patterns"] <= 0.05 * searches
    groups = collections.defaultdict(list)
    for r in rows:
        groups[f"{r['batch']}/{r['arm']}"].append(r)
    result_json = {"preregistration_version": prereg["version"], "screen": "find_replay_screen", "rule": rule["pass_rule"],
                   "binary": str(FIND.relative_to(BASE)), "binary_sha256": FIND_SHA256, "searches_before_first_edit": searches,
                   "passed": passed, "summary": all_rows, "by_batch_and_arm": {k: summary(v) for k, v in sorted(groups.items())},
                   "rows": rows}
    OUT.write_text(json.dumps(result_json, indent=2) + "\n")
    print(json.dumps({"passed": passed, "searches_before_first_edit": searches, **all_rows}))
    return 0 if passed else 1


def _before_edit(calls):
    for event in calls:
        if event.get("operation") == "edit":
            return
        yield event


def sha_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    sys.exit(main())
