"""Run the preregistered paired agent trials in a fixed random order, resumably.

    python benchmark/run_agent_batch.py [--batch ID] [--limit N] [--dry-run]

Plans every task x trial x arm of one preregistered batch (default: the latest in
agent_trials.batches), shuffles the plan with the batch's fixed seed so arms
alternate in time, and runs pending trials one at a time. A trial whose summary exists is skipped, so the batch can be paced across
usage windows. Every attempt is kept, including errors and capped runs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys

from agent_trials import BASE, tasks
from retrieval import graphify_baseline
from run_agent_trial import TOOL_BINARIES, preflight, run, solver

def plan(task_ids, trials, arms, seed):
    """Every (task, trial, arm), shuffled deterministically."""
    entries = [(task, k, arm) for task in task_ids for k in range(1, trials + 1) for arm in arms]
    random.Random(seed).shuffle(entries)
    return entries


def natural_spec(prereg, catalog):
    """The natural-task specification that freezes this catalog, if any."""
    return next((spec for key, spec in prereg.items()
                 if key.startswith("natural_tasks") and spec.get("catalog") == catalog), None)


def agent_preregistration():
    return json.loads((BASE / "benchmark/preregistration.json").read_text())["agent_trials"]


def trial_id(batch, task, k, arm):
    return f"{batch}-{task}-{arm}-{k}"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--batch", help="batch id in agent_trials.batches (default: latest)")
    parser.add_argument("--limit", type=int, default=None, help="run at most N pending trials")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    prereg = agent_preregistration()
    batch_id = args.batch or list(prereg["batches"])[-1]
    batch = prereg["batches"][batch_id]
    if not args.dry_run:
        if batch.get("solver_binary"):
            binary = BASE / batch["solver_binary"]
            if hashlib.sha256(binary.read_bytes()).hexdigest() != batch["solver_sha256"]:
                raise SystemExit("Solver binary differs from this batch's frozen copy")
            os.environ["CLAUDE_BINARY"] = str(binary)
        if batch.get("solver_version"):
            version = subprocess.check_output([solver(), "--version"], text=True).strip()
            if version != batch["solver_version"]:
                raise SystemExit("Solver version differs from this batch's frozen version")
        if batch.get("graphify_commit") and graphify_baseline()["commit"] != batch["graphify_commit"]:
            raise SystemExit("Graphify baseline differs from this batch's frozen one; run under its virtualenv")
        for arm, expected in batch.get("backend_sha256", {}).items():
            if preflight(arm, TOOL_BINARIES[arm]) != expected:
                raise SystemExit(f"{arm} differs from this batch's frozen backend")
    catalog = (BASE / batch.get("catalog", "benchmark/agent-tasks.json")).resolve()
    by_id = {t["id"]: t for t in tasks(catalog)}
    natural = natural_spec(prereg, batch.get("catalog"))
    minimum = natural["minimum_tasks"] if natural else prereg["minimum_tasks"]
    if len(by_id) < minimum:
        raise SystemExit(f"Only {len(by_id)} tasks; preregistration requires {minimum}")
    if natural:
        digest = hashlib.sha256(catalog.read_bytes()).hexdigest()
        if digest != natural.get("frozen_catalog_sha256"):
            raise SystemExit("The natural-task catalog differs from the frozen one")
    entries = plan(sorted(by_id), prereg["minimum_paired_trials_per_task_and_arm"], batch["arms"], batch["seed"])
    done = [e for e in entries
            if (BASE / ".research/trials" / trial_id(batch_id, *e) / "summary.json").is_file()]
    pending = [e for e in entries if e not in done][:args.limit]
    print(f"batch {batch_id}: {len(entries)} planned, {len(done)} done, running {len(pending)}", flush=True)
    for task, k, arm in pending:
        identifier = trial_id(batch_id, task, k, arm)
        if args.dry_run:
            print(identifier)
            continue
        if (BASE / ".research/trials" / identifier).exists():
            raise SystemExit(f"{identifier} exists without a summary; inspect it before rerunning")
        summary = run(by_id[task], identifier, arm, catalog)
        if summary["infrastructure_error"]:
            raise SystemExit(f"{identifier}: broker infrastructure error; batch stopped. Move the trial "
                             "aside with a note, fix the environment, and rerun it from scratch")
        if not summary["usage_complete"]:
            raise SystemExit(f"{identifier}: incomplete solver usage; artifacts preserved, batch stopped")
        if batch.get("stop_on_provider_error") and summary["result_subtype"] not in (
                "success", "error_max_budget_usd", "error_max_turns"):
            raise SystemExit(f"{identifier}: solver error; artifacts preserved, batch stopped")
        audit = summary["audit"] or {}
        print(json.dumps({"trial": identifier, "passed": summary["passed"],
                          "subtype": summary["result_subtype"], "tokens": audit.get("total_tokens"),
                          "cost_usd": audit.get("cost_usd")}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
