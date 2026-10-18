"""Fail-closed retrieval acceptance diagnostic; never establishes agent efficiency."""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

BASE = Path(__file__).resolve().parent.parent
QUALITY = ("code_file_hint_recall", "code_file_hint_recall_at_5",
           "code_file_hint_recall_at_10")


def number(value):
    return (type(value) in (int, float) and math.isfinite(value) and value >= 0)


def evaluate(rows, expected, candidate):
    """Check every frozen case; totals may not compensate for individual losses.

    expected maps (query ID, token budget) to whether code recall is applicable.
    Null recall is valid only for explicitly inapplicable code-recall cases.
    Output tokens measure delivered retrieval text, not total agent usage.
    """
    cases, errors, seen = [], [], set()
    candidate_total = baseline_total = 0
    for row in rows:
        key = (row.get("id"), row.get("budget"))
        if key not in expected or key in seen:
            errors.append(f"Unexpected or duplicate case: {key}")
            continue
        seen.add(key)
        entry = {"id": key[0], "budget": key[1], "regressions": [],
                 "missing_or_invalid": [], "metrics": {}}
        tools = row.get("tools", {})
        ours, theirs = tools.get(candidate, {}), tools.get("graphify", {})
        for metric in (*QUALITY, "output_tokens", "median_ms"):
            a, b = ours.get(metric), theirs.get(metric)
            entry["metrics"][metric] = {"candidate": a, "graphify": b}
            if metric in QUALITY and not expected[key]:
                if metric not in ours or metric not in theirs or a is not None or b is not None:
                    entry["missing_or_invalid"].append(metric)
                continue
            if (not number(a) or not number(b)
                    or (metric in QUALITY and (a > 1 or b > 1))
                    or (metric == "output_tokens" and (type(a) is not int or type(b) is not int))):
                entry["missing_or_invalid"].append(metric)
                continue
            if metric in QUALITY and a < b:
                entry["regressions"].append(metric)
            if metric == "output_tokens":
                candidate_total += a
                baseline_total += b
                if a > b:
                    entry["regressions"].append(metric)
            # Latency is reported, not used to compensate for quality/token losses.
        entry["passed"] = not (entry["regressions"] or entry["missing_or_invalid"])
        cases.append(entry)
    errors.extend(f"Missing case: {key}" for key in sorted(set(expected) - seen))
    if not expected:
        errors.append("Empty workload")
    strict_gain = candidate_total < baseline_total
    if not strict_gain:
        errors.append("No strict reduction in total delivered retrieval-text tokens")
    passed = not errors and all(case["passed"] for case in cases)
    return {"status": "passed" if passed else "not_passed", "candidate": candidate,
            "scope": "exploratory code-file discovery and delivered retrieval text only",
            "primary_efficiency_metric": "output_tokens",
            "quality_metrics": list(QUALITY),
            "policy_timing": "chosen after historical results; retrospective diagnostic",
            "product_promotion_allowed": False,
            "promotion_blockers": ["Not a preregistered held-out workload",
                                   "Verified agent outcomes and complete usage are unmeasured"],
            "case_count": len(cases),
            "regressing_cases": sum(bool(c["regressions"]) for c in cases),
            "invalid_cases": sum(bool(c["missing_or_invalid"]) for c in cases),
            "delivered_text_tokens": {"candidate": candidate_total, "graphify": baseline_total},
            "errors": errors, "cases": cases}


def main():
    source = BASE / "results/graphify-comparison.json"
    dest = BASE / "results/acceptance.json"
    # Authenticate saved outputs against their source receipts before evaluating.
    audit = subprocess.run([sys.executable, str(BASE / "benchmark/check_comparison.py")],
                           capture_output=True, text=True)
    if audit.returncode:
        result = {"status": "invalid_evidence", "product_promotion_allowed": False,
                  "audit_stdout": audit.stdout, "audit_stderr": audit.stderr}
    else:
        from compare_graphify import code_hint_recall
        queries = json.loads((BASE / "benchmark/queries.json").read_text())
        expected = {(q["id"], budget): code_hint_recall(q, []) is not None
                    for q in queries for budget in (2000, 8000)}
        data = json.loads(source.read_text())
        pinned = json.loads((BASE / "benchmark/corpora.json").read_text())
        pin = next(p for p in pinned["repositories"] if p["id"] == "graphify")
        from compare_graphify import GRAF_ARMS
        arms = [evaluate(data["rows"], expected, arm) for arm in GRAF_ARMS]
        pin_matches = data["baseline"]["pin"] == pin
        result = {"status": "not_passed", "product_promotion_allowed": False,
                  "baseline_pin_matches": pin_matches,
                  "comparison_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                  "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "arms": arms}
        if not pin_matches:
            result["status"] = "invalid_evidence"
    dest.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"status": result["status"], "report": str(dest),
                      "arms": [{k: a[k] for k in ("candidate", "status", "regressing_cases", "invalid_cases")}
                               for a in result.get("arms", [])]}, indent=2))
    return 1  # This exploratory workload cannot authorize product promotion.


if __name__ == "__main__":
    sys.exit(main())
