"""Apply the preregistered agent-trial rule (preregistration.json agent_trials.rule).

    python benchmark/agent_decision.py [--batch ID]

Reads every .research/trials/ID-*/summary.json of one preregistered batch, pairs
each candidate and control with the comparator by task, and writes
results/agent-decision-ID.json. A candidate passes only if it costs significantly
less across all attempts and its verifier success is non-inferior within the
margin; alphas are split across tested arms. Controls are reported but never pass
a batch. Missing trials or incomplete usage fail closed.
Exits nonzero unless the rule passes.
"""
import argparse
import json
from math import comb
from pathlib import Path
import statistics
import sys

from agent_trials import BASE, tasks
from paired_stats import non_inferiority_p, sign_flip_p, wilson


def pass_hat_k(successes, n, k):
    """Unbiased probability that k attempts drawn without replacement all pass."""
    return comb(successes, k) / comb(n, k)


def _index(summaries, task_ids, trials, arms):
    planned = {(t, a, k) for t in task_ids for a in arms for k in range(1, trials + 1)}
    table, reasons = {}, []
    for s in summaries:
        key = (s.get("task"), s.get("arm"), int(s["trial"].rsplit("-", 1)[1]))
        if key not in planned or key in table:
            reasons.append(f"unplanned or duplicate trial {s.get('trial')}")
            continue
        audit = s.get("audit") or {}
        if s.get("infrastructure_error") is not False:
            reasons.append(f"infrastructure error or unaudited backend in {s['trial']}")
        if not s.get("usage_complete") or audit.get("unmeasured_fields") or audit.get("cost_usd") is None:
            reasons.append(f"incomplete usage for {s['trial']}")
        table[key] = s
    reasons += [f"missing trial {t}/{a}/{k}" for t, a, k in sorted(planned - set(table))]
    for arm in arms:
        hashes = {s.get("tool_binary_sha256") for (_, a, _), s in table.items() if a == arm}
        if len(hashes) > 1 or None in hashes:
            reasons.append(f"{arm} backend binary missing from or changed within the batch")
    return table, reasons


def _arm_task(rows, trials):
    passes = sum(r["passed"] is True for r in rows)
    audits = [r.get("audit") or {} for r in rows]
    return {"attempts": len(rows), "passes": passes,
            "mean_cost_usd": statistics.fmean(a.get("cost_usd") or 0 for a in audits) if rows else None,
            "mean_tokens": statistics.fmean(a.get("total_tokens") or 0 for a in audits) if rows else None,
            "pass_hat_k": [pass_hat_k(passes, len(rows), k) for k in range(1, len(rows) + 1)]
            if len(rows) == trials else None}


def _arm_summary(per_task, arm, trials):
    attempts = sum(t[arm]["attempts"] for t in per_task)
    passes = sum(t[arm]["passes"] for t in per_task)
    complete = [t[arm]["pass_hat_k"] for t in per_task if t[arm]["pass_hat_k"]]
    return {"attempts": attempts, "passes": passes,
            "success_rate": passes / attempts if attempts else None,
            "success_wilson95": wilson(passes, attempts) if attempts else None,
            "pass_hat_k": [statistics.fmean(c[k] for c in complete) for k in range(trials)]
            if complete else None,
            "total_cost_usd": sum(t[arm]["mean_cost_usd"] * t[arm]["attempts"] for t in per_task
                                  if t[arm]["attempts"]),
            "total_tokens": sum(t[arm]["mean_tokens"] * t[arm]["attempts"] for t in per_task
                                if t[arm]["attempts"])}


def _compare(per_task, arm, comparator, rule, family):
    paired = [t for t in per_task if t[arm]["attempts"] and t[comparator]["attempts"]]
    cost = [t[comparator]["mean_cost_usd"] - t[arm]["mean_cost_usd"] for t in paired]
    tokens = [t[comparator]["mean_tokens"] - t[arm]["mean_tokens"] for t in paired]
    success = [t[arm]["passes"] / t[arm]["attempts"] - t[comparator]["passes"] / t[comparator]["attempts"]
               for t in paired]
    primary_p = sign_flip_p(cost) if cost else 1.0
    success_p = non_inferiority_p(success, rule["success"]["margin"]) if success else 1.0
    primary = {"metric": "cost_usd", "mean_reduction": statistics.fmean(cost) if cost else None,
               "p": primary_p, "alpha": rule["primary"]["alpha"] / family}
    guard = {"metric": "success_rate", "mean_difference": statistics.fmean(success) if success else None,
             "margin": rule["success"]["margin"], "p": success_p, "alpha": rule["success"]["alpha"] / family}
    for test in (primary, guard):
        test["passed"] = test["p"] <= test["alpha"]
    return {"primary": primary, "success": guard, "passed": primary["passed"] and guard["passed"],
            "cheaper_tasks": sum(d > 0 for d in cost),
            "secondary": {"mean_token_reduction": statistics.fmean(tokens) if tokens else None,
                          "token_reduction_p": sign_flip_p(tokens) if tokens else None}}


def evaluate(summaries, task_ids, trials, rule, batch):
    """Compare every candidate and control with each comparator; only a candidate can pass, and only
    by passing against every comparator (intersection-union, so comparators do not split alpha)."""
    arms = batch["arms"]
    comparators = batch.get("comparators") or [batch["comparator"]]
    table, reasons = _index(summaries, task_ids, trials, arms)
    per_task = []
    for task in sorted(task_ids):
        row = {"task": task}
        for arm in arms:
            row[arm] = _arm_task([table[(task, arm, k)] for k in range(1, trials + 1)
                                  if (task, arm, k) in table], trials)
        per_task.append(row)
    tested = [*batch["candidates"], *batch["controls"]]
    comparisons = {}
    for arm in tested:
        versus = {comp: _compare(per_task, arm, comp, rule, len(tested)) for comp in comparators}
        comparisons[arm] = {"role": "candidate" if arm in batch["candidates"] else "control",
                            "passed": all(v["passed"] for v in versus.values()), "versus": versus}
    if not any(comparisons[arm]["passed"] for arm in batch["candidates"]):
        reasons.append("no candidate passed both the cost and success tests")
    return {"passed": not reasons, "reasons": reasons, "comparators": comparators,
            "comparisons": comparisons,
            "arms": {arm: _arm_summary(per_task, arm, trials) for arm in arms},
            "tasks": per_task, "scope": rule["scope"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--batch", help="batch id in agent_trials.batches (default: latest)")
    args = parser.parse_args()
    prereg = json.loads((BASE / "benchmark/preregistration.json").read_text())
    spec = prereg["agent_trials"]
    batch_id = args.batch or list(spec["batches"])[-1]
    batch = spec["batches"][batch_id]
    task_ids = [t["id"] for t in tasks(BASE / batch.get("catalog", "benchmark/agent-tasks.json"))]
    summaries = [json.loads(p.read_text())
                 for p in sorted((BASE / ".research/trials").glob(f"{batch_id}-*/summary.json"))]
    result = evaluate(summaries, task_ids, spec["minimum_paired_trials_per_task_and_arm"], spec["rule"], batch)
    result = {"preregistration_version": prereg["version"], "batch": batch_id, **result}
    out = BASE / f"results/agent-decision-{batch_id}.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"batch": batch_id, "passed": result["passed"], "reasons": result["reasons"][:10],
                      **{a: {"passes": v["passes"], "attempts": v["attempts"]}
                         for a, v in result["arms"].items()}}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
