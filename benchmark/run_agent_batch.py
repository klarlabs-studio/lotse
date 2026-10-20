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
from pathlib import Path
import random
import sys

from agent_trials import BASE, tasks
from run_agent_trial import run

def plan(task_ids, trials, arms, seed):
    """Every (task, trial, arm), shuffled deterministically."""
    entries = [(task, k, arm) for task in task_ids for k in range(1, trials + 1) for arm in arms]
    random.Random(seed).shuffle(entries)
    return entries


def trial_id(batch, task, k, arm):
    return f"{batch}-{task}-{arm}-{k}"


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--batch", help="batch id in agent_trials.batches (default: latest)")
    parser.add_argument("--limit", type=int, default=None, help="run at most N pending trials")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    prereg = json.loads((BASE / "benchmark/preregistration.json").read_text())["agent_trials"]
    batch_id = args.batch or list(prereg["batches"])[-1]
    batch = prereg["batches"][batch_id]
    catalog = (BASE / batch.get("catalog", "benchmark/agent-tasks.json")).resolve()
    by_id = {t["id"]: t for t in tasks(catalog)}
    natural = prereg["natural_tasks"]
    minimum = natural["minimum_tasks"] if batch.get("catalog") == natural["catalog"] else prereg["minimum_tasks"]
    if len(by_id) < minimum:
        raise SystemExit(f"Only {len(by_id)} tasks; preregistration requires {minimum}")
    if batch.get("catalog") == natural["catalog"]:
        digest = hashlib.sha256(catalog.read_bytes()).hexdigest()
        if digest != natural["frozen_catalog_sha256"]:
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
        audit = summary["audit"] or {}
        print(json.dumps({"trial": identifier, "passed": summary["passed"],
                          "subtype": summary["result_subtype"], "tokens": audit.get("total_tokens"),
                          "cost_usd": audit.get("cost_usd")}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
