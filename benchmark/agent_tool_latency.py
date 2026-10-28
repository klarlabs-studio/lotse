"""Paired wall-clock latency of the agent-facing tools, as the broker invokes them (diagnostic).

    python benchmark/agent_tool_latency.py [--issues N] [--repeats R]

For each t4 repository (pinned snapshot) and its first N localization-replication issues,
alternate fresh-process calls of `graf triage`, `graf units`, and the Graphify query
(both at 1,500 tokens, warm caches), R times each, in rotating order. Also times one
cold build per tool and repository: `graf index` plus the first `units` call (which
fills the unit cache) against `graphify extract`. Reports medians with bootstrap 95%
intervals in results/agent-tool-latency.json.

A diagnostic under the conditions recorded in the output (machine load included). It is
not comparative efficiency evidence by itself; agent cost is measured by agent batches.
"""
import argparse
import json
import os
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

from localization import REPLICATION_CATALOG, write_json_atomic
from retrieval import BASE, load_documents

GRAF = BASE / "target/release/graf"
GRAPHIFY = BASE / ".research/venv/bin/graphify"
BUDGET = 1500
REPOS = ("gin", "urfave-cli", "click", "jinja", "express", "undici")


def timed(command):
    start = time.perf_counter()
    subprocess.run(command, check=True, capture_output=True, env={**os.environ, "PYTHONHASHSEED": "0"})
    return time.perf_counter() - start


def bootstrap(samples, rounds=2000, seed=0):
    rng = random.Random(seed)
    medians = sorted(statistics.median(rng.choices(samples, k=len(samples))) for _ in range(rounds))
    return [medians[int(0.025 * rounds)], medians[int(0.975 * rounds)]]


def stage(repo, scratch):
    """Copy the pinned snapshot's eligible files, as the broker stages a workspace."""
    root = scratch / repo
    for rel, text in load_documents(BASE / ".research/corpora" / repo).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--issues", type=int, default=10)
    parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    catalog = json.loads(REPLICATION_CATALOG.read_text())["tasks"]
    samples = {"graf_triage": [], "graf_units": [], "graphify_query": []}
    builds = {}
    with tempfile.TemporaryDirectory() as temp:
        scratch = Path(temp)
        for repo in REPOS:
            root = stage(repo, scratch)
            out = scratch / f"{repo}-graphify"
            first_issue = next(t["query"] for t in catalog if t["repo"] == repo)
            builds[repo] = {
                "graf_index_s": timed([str(GRAF), "--root", str(root), "index", "--rebuild"]),
                "graf_first_units_s": timed([str(GRAF), "--root", str(root), "units", "--budget", str(BUDGET),
                                             "--text", "--", first_issue]),
                "graphify_extract_s": timed([str(GRAPHIFY), "extract", str(root), "--code-only", "--no-cluster",
                                             "--max-workers", "2", "--out", str(out)]),
                "files": len(load_documents(root)),
            }
            graph = out / "graphify-out/graph.json"
            commands = {
                "graf_triage": lambda q: [str(GRAF), "--root", str(root), "triage", "--budget", str(BUDGET),
                                          "--text", "--", q],
                "graf_units": lambda q: [str(GRAF), "--root", str(root), "units", "--budget", str(BUDGET),
                                         "--text", "--", q],
                "graphify_query": lambda q: [sys.executable, str(BASE / "benchmark/compare_graphify.py"),
                                             "--graphify-query", str(graph), q, str(BUDGET)],
            }
            issues = [t["query"] for t in catalog if t["repo"] == repo][:args.issues]
            tools = list(commands)
            for i, issue in enumerate(issues):
                for r in range(args.repeats):
                    order = tools[(i + r) % 3:] + tools[:(i + r) % 3]
                    for tool in order:
                        samples[tool].append({"repo": repo, "seconds": timed(commands[tool](issue))})
            shutil.rmtree(root)
            shutil.rmtree(out)
            print(repo, {t: round(statistics.median(s["seconds"] for s in v if s["repo"] == repo), 3)
                         for t, v in samples.items()}, flush=True)
    summary = {}
    for tool, rows in samples.items():
        values = [r["seconds"] for r in rows]
        summary[tool] = {"median_s": statistics.median(values), "median_95ci": bootstrap(values), "n": len(values),
                         "by_repo": {repo: statistics.median(r["seconds"] for r in rows if r["repo"] == repo)
                                     for repo in REPOS}}
    write_json_atomic(BASE / "results/agent-tool-latency.json", {
        "conditions": {"budget": BUDGET, "issues_per_repo": args.issues, "repeats": args.repeats,
                       "process": "fresh process per call, as the agent broker invokes them; warm caches",
                       "loadavg_at_end": os.getloadavg(), "cpus": os.cpu_count(),
                       "graf_sha256": __import__("hashlib").sha256(GRAF.read_bytes()).hexdigest()},
        "builds": builds, "summary": summary, "samples": samples})
    print(json.dumps({t: (round(v["median_s"], 3), [round(x, 3) for x in v["median_95ci"]])
                      for t, v in summary.items()}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
