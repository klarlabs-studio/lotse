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

from agent_decision import attempts
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


def keychain_secret(service):
    found = subprocess.run(["security", "find-generic-password", "-s", service, "-w"], capture_output=True, text=True)
    return found.stdout.strip() if found.returncode == 0 else ""


def route_solver(endpoint):
    """Send the solver to a gateway with its own key; an empty API key stops a fallback to a login."""
    scheme, _, service = endpoint["credential"].partition(":")
    secret = keychain_secret(service) if scheme == "keychain" else ""
    if not secret:
        raise SystemExit(f"No credential for the solver endpoint ({endpoint['credential']})")
    os.environ.update({"ANTHROPIC_BASE_URL": endpoint["base_url"], "ANTHROPIC_AUTH_TOKEN": secret,
                       "ANTHROPIC_API_KEY": ""})


def configure_solver(batch):
    """Pass a batch's frozen model, price table and spend cap to its trials; clear stale ones."""
    settings = {"SOLVER_MODEL": batch.get("solver_model"),
                "SOLVER_BUDGET_USD": batch.get("solver_budget_usd"),
                "SOLVER_MAX_TURNS": batch.get("solver_max_turns"),
                "SOLVER_PRICES": batch.get("solver_prices_per_million")}
    for name, value in settings.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = json.dumps(value) if isinstance(value, dict) else str(value)


INFRASTRUCTURE_RERUNS = 2


def run_with_reruns(identifier, attempt, trials, aside):
    """Run one trial; an infrastructure abort is kept aside with a note and rerun from scratch.

    The preregistered completeness rule: "infrastructure aborts are rerun from scratch and their
    partial runs are kept outside the plan". After INFRASTRUCTURE_RERUNS reruns the batch stops.
    """
    for number in range(1, INFRASTRUCTURE_RERUNS + 2):
        summary = attempt()
        if not summary["infrastructure_error"]:
            return summary
        target = aside / f"{identifier}-infrastructure-{number}"
        aside.mkdir(parents=True, exist_ok=True)
        (trials / identifier).rename(target)
        (target / "aside.json").write_text(json.dumps({
            "trial": identifier, "reason": "infrastructure abort (broker error, gateway refusal, or a tool call left "
            "without a reply at the trial timeout); rerun from scratch under the preregistered completeness rule"}, indent=2) + "\n")
        print(json.dumps({"trial": identifier, "set_aside": str(target.relative_to(aside.parent))}), flush=True)
    raise SystemExit(f"{identifier}: infrastructure abort persisted after {INFRASTRUCTURE_RERUNS} reruns; batch stopped")


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
    entries = plan(sorted(by_id), attempts(prereg, batch), batch["arms"], batch["seed"])
    done = [e for e in entries
            if (BASE / ".research/trials" / trial_id(batch_id, *e) / "summary.json").is_file()]
    pending = [e for e in entries if e not in done][:args.limit]
    print(f"batch {batch_id}: {len(entries)} planned, {len(done)} done, running {len(pending)}", flush=True)
    if pending and not args.dry_run:
        if batch.get("solver_endpoint"):
            route_solver(batch["solver_endpoint"])
        configure_solver(batch)
    for task, k, arm in pending:
        identifier = trial_id(batch_id, task, k, arm)
        if args.dry_run:
            print(identifier)
            continue
        if (BASE / ".research/trials" / identifier).exists():
            raise SystemExit(f"{identifier} exists without a summary; inspect it before rerunning")
        summary = run_with_reruns(identifier, lambda: run(by_id[task], identifier, arm, catalog),
                                  BASE / ".research/trials", BASE / ".research/trials-aside")
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
