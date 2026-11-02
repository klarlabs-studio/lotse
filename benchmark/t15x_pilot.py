"""Exploratory pilot t15x (preregistration v151), descriptive only: graf with triage-excerpts against batch t15.

    python benchmark/t15x_pilot.py

Per arm on the 44 t15 tasks: t15x's graf_installed (1 attempt each) and t15's graf_installed and builtin (attempts 1 and
2). Reports total and per-attempt mean cost_usd, passes, median turns and tool calls, the share of attempts whose first
test run passes, loop phases as benchmark/loop_phases.py, and the v151 criterion for proposing a confirmatory batch.
t15's arms ran earlier, so this is not a concurrent or randomized comparison and supports no claim.
Writes results/t15x-pilot.json.
"""
import json
import statistics
import sys

from loop_phases import calls, phases
from agent_trials import BASE

ARMS = {"t15x_graf_excerpts": ("t15x", "graf_installed"), "t15_graf_graph_triage": ("t15", "graf_installed"),
        "t15_builtin": ("t15", "builtin")}


def first_test_passed(seq):
    """Whether the first test run passed; None if the attempt never ran test."""
    result = next((text for name, _, text in seq if name == "test"), None)
    if result is None:
        return None
    return '\\"verification_passed\\": true' in result or '"verification_passed": true' in result


def attempt(directory):
    summary = json.loads((directory / "summary.json").read_text())
    seq = calls(directory / "agent-stream.jsonl")
    return {"trial": directory.name, "task": summary["task"], "passed": bool(summary["passed"]),
            "cost_usd": (summary["audit"] or {}).get("cost_usd") or 0.0, "turns": summary["num_turns"],
            "calls": len(seq), "first_test_passed": first_test_passed(seq), **phases(seq)}


def arm_summary(rows):
    tested = [r for r in rows if r["first_test_passed"] is not None]
    return {"attempts": len(rows), "total_cost_usd": round(sum(r["cost_usd"] for r in rows), 4),
            "mean_cost_usd": round(statistics.mean(r["cost_usd"] for r in rows), 5),
            "median_cost_usd": round(statistics.median(r["cost_usd"] for r in rows), 5),
            "passes": sum(r["passed"] for r in rows), "pass_rate": round(sum(r["passed"] for r in rows) / len(rows), 3),
            "median_turns": statistics.median(r["turns"] for r in rows),
            "median_calls": statistics.median(r["calls"] for r in rows),
            "first_test_passed": sum(bool(r["first_test_passed"]) for r in tested), "ran_test": len(tested),
            **{f"median_{k}": statistics.median(r[k] for r in rows) for k in ("locate", "compose", "debug")}}


def main():
    trials = BASE / ".research/trials"
    rows = {arm: [attempt(s.parent) for s in sorted(trials.glob(f"{batch}-*-{name}-*/summary.json"))
                  if not s.parent.name.startswith(f"{batch}-screen-")]
            for arm, (batch, name) in ARMS.items()}
    report = {arm: arm_summary(r) for arm, r in rows.items()}
    pilot = report["t15x_graf_excerpts"]
    others = [report["t15_graf_graph_triage"], report["t15_builtin"]]
    report["v151_criterion"] = {
        "cost_at_least_5pct_below_both": all(pilot["mean_cost_usd"] <= 0.95 * o["mean_cost_usd"] for o in others),
        "pass_rate_within_0.10_of_both": all(pilot["pass_rate"] >= o["pass_rate"] - 0.10 for o in others)}
    report["v151_criterion"]["propose_confirmatory_batch"] = all(report["v151_criterion"].values())
    (BASE / "results/t15x-pilot.json").write_text(json.dumps({"summary": report, "rows": rows}, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
