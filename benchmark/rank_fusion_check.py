"""Offline ranking check (agent_trials.natural_tasks_t14.rank_fusion_v144), no model calls: does fusing Graphify's
graph traversal into graf's triage ranking put a faulted file first more often than graf alone?

    python benchmark/rank_fusion_check.py --batch t14 [--tasks ID ...]

Replays each graf_installed attempt's first triage call from the batch, in the task's prepared workspace, three ways:
graf's triage file list; Graphify's `graphify query` on the same text, its files in the order the output first names
them; and the two fused by reciprocal rank (k = 60, ties kept in graf's order). Each attempt scores whether a faulted
file is ranked first. Run under the batch's Graphify virtualenv. Writes results/rank-fusion-<batch>.json.
"""
import argparse
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys

from agent_trials import BASE, prepare, task_faults, tasks
from retrieval import graphify_baseline
from triage_replay import MCP_BUDGET, first_triage, mcnemar_one_sided, triage

K = 60  # Reciprocal rank fusion's customary constant; fixed, not tuned.
SOURCE = re.compile(r"\[src=([^ \]]*)")


def graphify_files(out):
    """Files in the order a `graphify query` output first names them."""
    return list(dict.fromkeys(path for path in SOURCE.findall(out) if path))


def fused(graf, graphify):
    score = {}
    for ranking in (graf, graphify):
        for rank, path in enumerate(ranking, 1):
            score[path] = score.get(path, 0) + 1 / (K + rank)
    order = {path: i for i, path in enumerate(dict.fromkeys(graf + graphify))}
    return sorted(score, key=lambda path: (-score[path], order[path]))


def first(ranking, faulted):
    return bool(ranking) and ranking[0] in faulted


def rank(ranking, faulted):
    return next((i + 1 for i, path in enumerate(ranking) if path in faulted), None)


def calls(batch):
    """{task: [(trial, issue, budget)]} from each graf_installed attempt's first triage call."""
    out = {}
    for summary in sorted((BASE / ".research/trials").glob(f"{batch}-*-graf_installed-*/summary.json")):
        record = json.loads(summary.read_text())
        call = first_triage(summary.parent / "agent-stream.jsonl")
        if call:
            out.setdefault(record["task"], []).append((record["trial"], call["issue"], int(call.get("budget", MCP_BUDGET))))
    return out


def replay(task, queries, graf, graphify):
    faulted = {f["path"] for f in task_faults(task)}
    trial = f"rank-fusion-{task['id']}"
    with contextlib.redirect_stdout(io.StringIO()):
        prepare(task, trial)
    root = BASE / ".research/trials" / trial / "workspace"
    rows = []
    try:
        subprocess.run([str(graf), "--root", str(root), "index"], capture_output=True, check=True)
        home = root.parent / "graphify-home"  # As the batch ran it: nothing written globally.
        home.mkdir()
        env = {**os.environ, "HOME": str(home)}
        subprocess.run([str(graphify), "update", "."], cwd=root, env=env, capture_output=True, check=True)
        for attempt, issue, budget in queries:
            ours = triage(graf, root, issue, budget)["files"]
            theirs = graphify_files(subprocess.run([str(graphify), "query", issue], cwd=root, env=env,
                                                   capture_output=True, text=True).stdout)
            both = fused(ours, theirs)
            rows.append({"task": task["id"], "attempt": attempt, "budget": budget,
                         **{name: {"first": first(r, faulted), "rank": rank(r, faulted), "listed": len(r)}
                            for name, r in (("graf", ours), ("graphify", theirs), ("fused", both))}})
    finally:
        shutil.rmtree(root.parent)
    return rows


def verdict(rows, spec):
    def test(candidate, baseline):
        gained = sum(r[candidate]["first"] and not r[baseline]["first"] for r in rows)
        lost = sum(r[baseline]["first"] and not r[candidate]["first"] for r in rows)
        p = mcnemar_one_sided(gained, lost)
        return {"gained": gained, "lost": lost, "p": p, "passed": gained > lost and p < spec["alpha"]}
    return {"calls": len(rows), "tasks": len({r["task"] for r in rows}),
            "first": {name: sum(r[name]["first"] for r in rows) for name in ("graf", "graphify", "fused")},
            "fused_vs_graf": test("fused", "graf"), "fused_vs_graphify": test("fused", "graphify")}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--batch", required=True)
    parser.add_argument("--tasks", nargs="*", help="a subset, for a smoke run; writes nothing")
    args = parser.parse_args()
    spec = json.loads((BASE / "benchmark/preregistration.json").read_text())["agent_trials"]
    batch = spec["batches"][args.batch]
    if graphify_baseline()["commit"] != batch["graphify_commit"]:
        raise SystemExit(f"run under {batch['graphify_virtualenv']}")
    graf = BASE / batch["backend_binaries"]["graf_installed"]
    graphify = BASE / batch["graphify_virtualenv"] / "bin/graphify"
    catalog = {t["id"]: t for t in tasks(BASE / batch["catalog"])}
    queries, rows = calls(args.batch), []
    for task_id in args.tasks or sorted(queries):
        rows += replay(catalog[task_id], queries[task_id], graf, graphify)
        print(task_id, len(rows), flush=True)
    rule = spec["natural_tasks_t14"].get("rank_fusion_v144", {"alpha": 0.05})
    result = verdict(rows, rule)
    print(json.dumps(result, indent=1))
    if args.tasks:
        print(json.dumps(rows, indent=1))
    else:
        (BASE / f"results/rank-fusion-{args.batch}.json").write_text(
            json.dumps({**result, "rows": rows}, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
