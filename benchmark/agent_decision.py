"""Apply the preregistered agent-trial rule (preregistration.json agent_trials.rule).

    python benchmark/agent_decision.py

Reads every .research/trials/*/summary.json in the planned batch, pairs the two
arms by task, and writes results/agent-decision.json. The candidate passes only
if it costs significantly less across all attempts and its verifier success is
non-inferior within the margin. Missing trials or incomplete usage fail closed.
Exits nonzero unless the rule passes.
"""
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


def evaluate(summaries, task_ids, trials, rule):
    cand, comp = rule["candidate"], rule["comparator"]
    table, reasons = _index(summaries, task_ids, trials, (cand, comp))
    per_task = []
    for task in sorted(task_ids):
        row = {"task": task}
        for arm in (cand, comp):
            row[arm] = _arm_task([table[(task, arm, k)] for k in range(1, trials + 1)
                                  if (task, arm, k) in table], trials)
        per_task.append(row)
    paired = [t for t in per_task if t[cand]["attempts"] and t[comp]["attempts"]]
    cost = [t[comp]["mean_cost_usd"] - t[cand]["mean_cost_usd"] for t in paired]
    tokens = [t[comp]["mean_tokens"] - t[cand]["mean_tokens"] for t in paired]
    success = [t[cand]["passes"] / t[cand]["attempts"] - t[comp]["passes"] / t[comp]["attempts"]
               for t in paired]
    primary_p = sign_flip_p(cost) if cost else 1.0
    success_p = non_inferiority_p(success, rule["success"]["margin"]) if success else 1.0
    primary = {"metric": "cost_usd", "mean_reduction": statistics.fmean(cost) if cost else None,
               "p": primary_p, "alpha": rule["primary"]["alpha"],
               "passed": primary_p <= rule["primary"]["alpha"]}
    guard = {"metric": "success_rate", "mean_difference": statistics.fmean(success) if success else None,
             "margin": rule["success"]["margin"], "p": success_p, "alpha": rule["success"]["alpha"],
             "passed": success_p <= rule["success"]["alpha"]}
    reasons += [f"{name} test failed" for name, test in (("primary cost", primary), ("success", guard))
                if not test["passed"]]
    return {"passed": not reasons, "reasons": reasons, "primary": primary, "success": guard,
            "secondary": {"mean_token_reduction": statistics.fmean(tokens) if tokens else None,
                          "token_reduction_p": sign_flip_p(tokens) if tokens else None},
            "arms": {arm: _arm_summary(per_task, arm, trials) for arm in (cand, comp)},
            "tasks": per_task, "scope": rule["scope"]}


def main():
    prereg = json.loads((BASE / "benchmark/preregistration.json").read_text())
    spec = prereg["agent_trials"]
    task_ids = [t["id"] for t in tasks(BASE / "benchmark/agent-tasks.json")]
    summaries = [json.loads(p.read_text())
                 for p in sorted((BASE / ".research/trials").glob("t-*/summary.json"))]
    result = evaluate(summaries, task_ids, spec["minimum_paired_trials_per_task_and_arm"], spec["rule"])
    result = {"preregistration_version": prereg["version"], **result}
    out = BASE / "results/agent-decision.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    arms = result["arms"]
    print(json.dumps({"passed": result["passed"], "reasons": result["reasons"][:10],
                      **{a: {"passes": v["passes"], "attempts": v["attempts"]} for a, v in arms.items()}}))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
