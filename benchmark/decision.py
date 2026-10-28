"""Apply the preregistered decision rule (benchmark/preregistration.json).

The rule replaces per-case zero regression with paired, query-level tests:
superiority on the primary efficiency metric, non-inferiority on every quality
guardrail (Holm-corrected), and a cap on the share of queries with severe losses.
Every case is still reported. Only a frozen held-out workload can pass, and a
retrieval pass never authorizes promotion: agent trials remain required.

    python benchmark/decision.py [--workload development|held_out] [--arm graf_locate ...]

Reads results/graphify-comparison.json (development) or
results/heldout-comparison.json (frozen held-out workload), writes
results/decision.json, and exits nonzero unless the rule passes.
"""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import subprocess
import sys

from acceptance import number
from paired_stats import holm, non_inferiority_p, required_n, sign_flip_p, wilson
from reports import write_decision

BASE = Path(__file__).resolve().parents[1]


def _measurements(rows, expected, candidate, quality):
    """Index valid rows by (query, budget); collect structural errors."""
    table, errors = {}, []
    for row in rows:
        key = (row.get("id"), row.get("budget"))
        if key not in expected or key in table:
            errors.append(f"Unexpected or duplicate case: {key}")
            continue
        tools = row.get("tools", {})
        pair = (tools.get(candidate, {}), tools.get("graphify", {}))
        metrics = ["output_tokens", *(quality if expected[key] else [])]
        bad = [m for m in metrics for side in pair
               if not number(side.get(m)) or (m in quality and side[m] > 1)]
        if bad:
            errors.append(f"Missing or invalid {sorted(set(bad))} for {key}")
        table[key] = pair
    errors += [f"Missing case: {key}" for key in sorted(set(expected) - set(table))]
    return table, errors


def _cases(table, quality):
    return [{"id": q, "budget": b,
             "metrics": {m: {"candidate": c.get(m), "graphify": g.get(m)}
                         for m in (*quality, "output_tokens", "median_ms")}}
            for (q, b), (c, g) in sorted(table.items())]


def _primary(table, prereg, family):
    spec = {**prereg["primary"], "alpha": prereg["primary"]["alpha"] / family, "family": family}
    diffs = [g[spec["metric"]] - c[spec["metric"]]
             for (_, b), (c, g) in sorted(table.items()) if b == spec["budget"]]
    p = sign_flip_p(diffs) if diffs else 1.0
    return {**spec, "queries": len(diffs), "mean_reduction": statistics.fmean(diffs) if diffs else None,
            "p_value": p, "passed": bool(diffs) and p < spec["alpha"]}


def _guardrails(table, expected, prereg):
    spec = prereg["guardrails"]
    results = []
    for metric in spec["metrics"]:
        for budget in spec["budgets"]:
            diffs = [c[metric] - g[metric] for (q, b), (c, g) in sorted(table.items())
                     if b == budget and expected[(q, b)]]
            results.append({"metric": metric, "budget": budget, "queries": len(diffs),
                            "mean_difference": statistics.fmean(diffs) if diffs else None,
                            "sd_difference": statistics.pstdev(diffs) if diffs else None,
                            "p_value": non_inferiority_p(diffs, spec["margin"]) if diffs else 1.0})
    rejected = holm([g["p_value"] for g in results], spec["alpha"])
    for guardrail, ok in zip(results, rejected):
        guardrail["non_inferior"] = ok
    return results


def _tail(table, expected, prereg):
    spec = prereg["tail"]
    scored = {q for (q, b) in table if expected[(q, b)]}
    severe = sorted({q for (q, b), (c, g) in table.items() if expected[(q, b)]
                     and c["code_file_hint_recall"] == 0 and g["code_file_hint_recall"] > 0})
    low, high = wilson(len(severe), len(scored), spec["confidence"]) if scored else (0.0, 1.0)
    return {**spec, "queries": len(scored), "severe_queries": severe,
            "wilson_interval": [low, high], "passed": bool(scored) and high <= spec["max_wilson_upper"]}


def _planning(guardrails, prereg):
    margin = prereg["guardrails"]["margin"]
    floor = prereg["workloads"]["held_out"]["minimum_queries"]
    sizes = {f"{g['metric']}@{g['budget']}": required_n(g["sd_difference"], margin)
             for g in guardrails if g["sd_difference"] is not None}
    return {"method": "paired one-sided non-inferiority, normal approximation, true difference 0",
            "per_guardrail": sizes, "required_queries": max([floor, *sizes.values()])}


def evaluate(rows, expected, candidate, prereg, role, family=1):
    """Apply the rule to one arm; family is the number of arms sharing the primary alpha."""
    quality = prereg["guardrails"]["metrics"]
    table, errors = _measurements(rows, expected, candidate, quality)
    if errors:
        return {"status": "not_passed", "candidate": candidate, "workload": role,
                "product_promotion_allowed": False, "blockers": errors,
                "cases": _cases(table, quality)}
    primary = _primary(table, prereg, family)
    guardrails = _guardrails(table, expected, prereg)
    tail = _tail(table, expected, prereg)
    held = prereg["workloads"]["held_out"]
    minimum = held["minimum_queries"]
    planned = max(minimum, held.get("pilot_required_queries", {}).get(candidate, minimum))
    blockers = []
    if role != "held_out":
        blockers.append(f"{role} workload: the decision rule applies only to the frozen held-out set")
    if primary["queries"] < minimum:
        blockers.append(f"Only {primary['queries']} queries; preregistered minimum is {minimum}")
    if any(g["queries"] < planned for g in guardrails):
        blockers.append(f"Insufficient scored queries: frozen pilot/minimum requires {planned} per guardrail")
    if not primary["passed"]:
        blockers.append("No significant reduction in the primary metric")
    blockers += [f"Not non-inferior: {g['metric']} at {g['budget']}"
                 for g in guardrails if not g["non_inferior"]]
    if not tail["passed"]:
        blockers.append("Too many queries with severe quality losses")
    return {"status": "not_passed" if blockers else "passed", "candidate": candidate,
            "workload": role, "product_promotion_allowed": False,
            "promotion_requires": "preregistered agent trials with complete usage accounting",
            "blockers": blockers, "primary": primary, "guardrails": guardrails, "tail": tail,
            "sample_size": {"minimum_queries": minimum, "required_scored_queries": planned},
            "planning": _planning(guardrails, prereg), "cases": _cases(table, quality)}


def rescore(rows, cases, judgments):
    """Recompute code-file recall against author labels plus judged-relevant pooled files."""
    from pool_judgments import relevant_files
    rescored = []
    for row in rows:
        relevant = relevant_files(cases[row["id"]], judgments)
        tools = {}
        for name, value in row["tools"].items():
            def recall(paths):
                return len(relevant & set(paths)) / len(relevant) if relevant else None
            tools[name] = {**value, "code_file_hint_recall": recall(value["cited_paths"]),
                           "code_file_hint_recall_at_5": recall(value["cited_path_order"][:5]),
                           "code_file_hint_recall_at_10": recall(value["cited_path_order"][:10])}
        rescored.append({**row, "tools": tools})
    return rescored


def _workload(prereg, requested):
    """The requested workload; held out only when its file matches the frozen hash."""
    if requested == "development":
        return "development", BASE / prereg["workloads"]["development"]["queries"]
    held = prereg["workloads"]["held_out"]
    path = BASE / held["queries"]
    if not (held["sha256"] and path.is_file()
            and hashlib.sha256(path.read_bytes()).hexdigest() == held["sha256"]):
        raise RuntimeError("Held-out queries are missing or differ from the frozen hash")
    return "held_out", path


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    from compare_graphify import GRAF_ARMS, code_hint_recall
    parser.add_argument("--arm", action="append", choices=tuple(GRAF_ARMS))
    parser.add_argument("--workload", choices=("development", "held_out"), default="development")
    parser.add_argument("--comparison", type=Path,
                        help="Saved comparison for this workload (default: standard workload path)")
    args = parser.parse_args()
    prereg_path = BASE / "benchmark/preregistration.json"
    prereg = json.loads(prereg_path.read_text())
    role, query_path = _workload(prereg, args.workload)
    queries = json.loads(query_path.read_text())
    budgets = sorted({prereg["primary"]["budget"], *prereg["guardrails"]["budgets"]})
    expected = {(q["id"], b): code_hint_recall(q, []) is not None for q in queries for b in budgets}
    source = (args.comparison.resolve() if args.comparison else BASE /
              ("results/heldout-comparison.json" if role == "held_out"
               else "results/graphify-comparison.json"))
    # Authenticate saved outputs against their source receipts before evaluating.
    audit = subprocess.run([sys.executable, str(BASE / "benchmark/check_comparison.py"),
                            "--comparison", str(source)], capture_output=True, text=True)
    if audit.returncode:
        result = {"status": "invalid_evidence", "product_promotion_allowed": False, "workload": role,
                  "comparison_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                  "audit_stdout": audit.stdout, "audit_stderr": audit.stderr}
        write_decision(result)
        print(json.dumps({"status": result["status"], "audit_stderr": audit.stderr[-500:]}, indent=2))
        return 1
    data = json.loads(source.read_text())
    if BASE / data.get("queries", "benchmark/queries.json") != query_path:
        raise RuntimeError(f"{source.name} was not produced from {query_path.name}")
    rows, judging = data["rows"], None
    if role == "held_out":
        from pool_judgments import JUDGMENTS, POOL, check_judgments
        held = prereg["workloads"]["held_out"]
        pool_path = BASE / held["pool"] if "pool" in held else POOL
        judgments_path = BASE / held["judgments"] if "judgments" in held else JUDGMENTS
        if not (pool_path.is_file() and judgments_path.is_file()):
            raise RuntimeError("Pooled blind judgments are required before a held-out decision")
        pool_data = json.loads(pool_path.read_text())
        if "run_plan" in data and (pool_data.get("comparison_sha256") != hashlib.sha256(source.read_bytes()).hexdigest()
                                   or pool_data.get("query_sha256") != data["query_sha256"]):
            raise RuntimeError("Blind pool does not authenticate this comparison and question set")
        pool = {q: v["files"] for q, v in pool_data["queries"].items()}
        judgments = json.loads(judgments_path.read_text())
        if errors := check_judgments(pool, judgments):
            raise RuntimeError(f"Incomplete judgments: {errors[:3]}")
        rows = rescore(rows, {q["id"]: q for q in queries}, judgments)
        judging = {"pool_sha256": hashlib.sha256(pool_path.read_bytes()).hexdigest(),
                   "judgments_sha256": hashlib.sha256(judgments_path.read_bytes()).hexdigest()}
    experimental = prereg["arms"]["experimental"]
    arms = [evaluate(rows, expected, arm, prereg, role,
                     family=len(experimental) if arm in experimental else 1)
            for arm in args.arm or [a for a in data["arms"] if a in GRAF_ARMS]]
    result = {"status": "passed" if all(a["status"] == "passed" for a in arms) else "not_passed",
              "product_promotion_allowed": False, "workload": role,
              "preregistration_sha256": hashlib.sha256(prereg_path.read_bytes()).hexdigest(),
              "comparison_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "judging": judging,
              "arms": arms}
    dest = write_decision(result)
    print(json.dumps({"status": result["status"], "workload": role, "report": str(dest),
                      "arms": [{"candidate": a["candidate"], "blockers": a["blockers"]} for a in arms]},
                     indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
