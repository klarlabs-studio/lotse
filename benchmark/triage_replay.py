"""Offline triage-miss screen (agent_trials.triage_miss_screen_v126), no model calls.

Replays each graf_installed attempt's first triage call from batches t10 and t11, plus each task's issue
verbatim, against a graf binary in the task's prepared workspace, and records whether a faulted file is named.
With --graphify, also runs `graphify query` on the same primary queries (code-file hint recall only).

    python benchmark/triage_replay.py --graf .research/bin/graf-t10 --label baseline [--graphify]
    python benchmark/triage_replay.py --compare baseline candidate
"""
import argparse
import contextlib
import io
import json
import os
from math import comb
from pathlib import Path
import shutil
import statistics
import subprocess

import tiktoken

from agent_trials import BASE, prepare, task_faults, tasks
from retrieval import graphify_baseline

BATCHES = ("t10", "t11")
MCP_BUDGET = 1500  # graf mcp's triage default; the agent passed none in most calls.
GRAPHIFY = BASE / ".research/venv-graphify-0.9.97/bin/graphify"
GRAPHIFY_COMMIT = "0a8cd4e1e342235fe53e881fb5d591bf0460071f"  # 0.9.97, as batches t10 and t11 froze it.


def first_triage(stream):
    for line in stream.read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_use" and block["name"].endswith("triage"):
                    return block["input"]
    return None


def recorded_queries():
    """{task: [(attempt, issue, budget)]} from each graf_installed attempt's first triage call."""
    queries = {}
    for batch in BATCHES:
        for summary in sorted((BASE / ".research/trials").glob(f"{batch}-*-graf_installed-*/summary.json")):
            record = json.loads(summary.read_text())
            call = first_triage(summary.parent / "agent-stream.jsonl")
            if call:
                queries.setdefault(record["task"], []).append(
                    (record["trial"], call["issue"], int(call.get("budget", MCP_BUDGET))))
    return queries


def named(text, faulted):
    return any(path in text for path in faulted)


def triage(graf, root, issue, budget):
    done = subprocess.run([str(graf), "--root", str(root), "triage", "--budget", str(budget), "--", issue],
                          capture_output=True, text=True, check=True)
    return json.loads(done.stdout)


def score(response, faulted):
    rank = next((i + 1 for i, path in enumerate(response["files"]) if path in faulted), None)
    units = any(unit["path"] in faulted for unit in response["units"])
    return {"hit": rank is not None or units, "file_rank": rank, "unit_hit": units,
            "context_tokens": response["context_tokens"]}


def replay(graf, label, with_graphify, index_args=()):
    if with_graphify and graphify_baseline()["commit"] != GRAPHIFY_COMMIT:
        raise SystemExit("run under .research/venv-graphify-0.9.97")
    bpe = tiktoken.get_encoding("cl100k_base")
    queries, rows = recorded_queries(), []
    catalog = {t["id"]: t for batch in BATCHES for t in tasks(BASE / f"benchmark/natural-tasks-{batch}.json")}
    for task_id in sorted(queries):
        task = catalog[task_id]
        faulted = {f["path"] for f in task_faults(task)}
        trial = f"replay-{label}-{task_id}"
        with contextlib.redirect_stdout(io.StringIO()):
            prepare(task, trial)
        root = BASE / ".research/trials" / trial / "workspace"
        try:
            subprocess.run([str(graf), "--root", str(root), "index", *index_args], capture_output=True, check=True)
            calls = [(attempt, issue, budget, "primary") for attempt, issue, budget in queries[task_id]]
            calls.append((None, task["issue"], MCP_BUDGET, "secondary"))
            for attempt, issue, budget, kind in calls:
                row = {"task": task_id, "attempt": attempt, "kind": kind, "budget": budget,
                       **score(triage(graf, root, issue, budget), faulted)}
                rows.append(row)
            if with_graphify:
                home = root.parent / "graphify-home"  # As the batches ran it: nothing written globally.
                home.mkdir()
                env = {**os.environ, "HOME": str(home)}
                subprocess.run([str(GRAPHIFY), "update", "."], cwd=root, env=env, capture_output=True, check=True)
                for row, (_, issue, _, _) in zip(rows[-len(calls):], calls):
                    if row["kind"] == "primary":
                        out = subprocess.run([str(GRAPHIFY), "query", issue], cwd=root, env=env, capture_output=True,
                                             text=True).stdout
                        row["graphify"] = {"hit": named(out, faulted), "tokens": len(bpe.encode(out))}
        finally:
            shutil.rmtree(root.parent)
        print(task_id, sum(r["hit"] for r in rows if r["task"] == task_id and r["kind"] == "primary"),
              "/", len(queries[task_id]), flush=True)
    out = BASE / f"results/triage-replay-{label}.json"
    out.write_text(json.dumps({"graf": str(graf), "index_args": list(index_args), "label": label, "rows": rows},
                              indent=1) + "\n")
    summarize(rows, label)


def summarize(rows, label):
    for kind in ("primary", "secondary"):
        r = [x for x in rows if x["kind"] == kind]
        mrr = sum(1 / x["file_rank"] for x in r if x["file_rank"]) / len(r)
        print(f"{label} {kind}: hit {sum(x['hit'] for x in r)}/{len(r)}, file MRR {mrr:.3f}, "
              f"median tokens {statistics.median(x['context_tokens'] for x in r)}")
    g = [x["graphify"] for x in rows if "graphify" in x]
    if g:
        print(f"graphify primary: hit {sum(x['hit'] for x in g)}/{len(g)}, "
              f"median tokens {statistics.median(x['tokens'] for x in g)}")


def mcnemar_one_sided(gained, lost):
    """P(at least `gained` of gained+lost discordant pairs favour the candidate) under no difference."""
    n = gained + lost
    return sum(comb(n, k) for k in range(gained, n + 1)) / 2 ** n if n else 1.0


def compare(baseline, candidate, rule="v126"):
    load = lambda label: json.loads((BASE / f"results/triage-replay-{label}.json").read_text())["rows"]
    pairs = list(zip(load(baseline), load(candidate)))
    assert all((b["task"], b["attempt"], b["kind"]) == (c["task"], c["attempt"], c["kind"]) for b, c in pairs)
    primary = [(b, c) for b, c in pairs if b["kind"] == "primary"]
    n = len(primary)
    base_hits, cand_hits = sum(b["hit"] for b, _ in primary), sum(c["hit"] for _, c in primary)
    gained = sum(c["hit"] and not b["hit"] for b, c in primary)
    lost = sum(b["hit"] and not c["hit"] for b, c in primary)
    base_tokens = statistics.median(b["context_tokens"] for b, _ in primary)
    cand_tokens = statistics.median(c["context_tokens"] for _, c in primary)
    p = mcnemar_one_sided(gained, lost)
    verdict = {"baseline_hits": base_hits, "candidate_hits": cand_hits, "calls": n, "gained": gained, "lost": lost,
               "p": p, "median_tokens": [base_tokens, cand_tokens],
               "rule": rule,
               # v126 asks for 10 more points of hits; v127 (against the 15-file triage) for more gains than losses.
               "passed": ((cand_hits - base_hits) / n >= 0.10 if rule == "v126" else gained > lost)
               and p < 0.05 and cand_tokens <= 1.10 * base_tokens and lost <= 0.05 * base_hits}
    print(json.dumps(verdict))
    return verdict


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--graf", type=Path)
    parser.add_argument("--label")
    parser.add_argument("--graphify", action="store_true")
    parser.add_argument("--compare", nargs=2)
    parser.add_argument("--rule", choices=("v126", "v127"), default="v126")
    parser.add_argument("--index-arg", action="append", default=[], help="an experimental index flag, repeatable")
    args = parser.parse_args()
    if args.compare:
        compare(*args.compare, args.rule)
    else:
        replay(args.graf.resolve(), args.label, args.graphify, args.index_arg)
