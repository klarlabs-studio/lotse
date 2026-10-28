"""Post-hoc diagnostic of where one finished agent batch's cost difference comes from; no model calls.

    python benchmark/agent_cost_diagnostic.py --batch t9 --candidate graf_pointer_orient --comparator graphify_orient

Exploratory and not preregistered: run only after a batch's decision is recorded. Writes one row per attempt
(turns, tool calls by operation, cost, whether the orientation names a changed file, when the agent first read one)
and the paired summaries to results/agent-cost-diagnostic-BATCH.json.
"""
import argparse
import collections
import json
from pathlib import Path
import statistics
import sys

BASE = Path(__file__).resolve().parent.parent


def attempt_row(summary, directory, gold):
    """One attempt's cost, turn and tool profile, and how directly it reached a changed file."""
    operations, reads, orientation = collections.Counter(), [], ""
    for line in (directory / "tool-events.jsonl").read_text().splitlines():
        event = json.loads(line)
        operations[event.get("operation")] += 1
        if event.get("operation") == "orient":
            orientation = ((event.get("response") or {}).get("result") or {}).get("content", "")
        if event.get("operation") == "read":
            reads.append((event.get("arguments") or {}).get("path"))
    return {"task": summary["task"], "arm": summary["arm"], "passed": summary["passed"], "turns": summary["num_turns"],
            "cost_usd": summary["audit"]["cost_usd"], "tokens": summary["audit"]["total_tokens"],
            "operations": {str(k): v for k, v in operations.items()},
            "orientation_names_changed_file": any(path in orientation for path in gold),
            "first_changed_file_read": next((i + 1 for i, path in enumerate(reads) if path in gold), None)}


def total(rows, arm, tasks=None):
    return round(sum(r["cost_usd"] for r in rows if r["arm"] == arm and (tasks is None or r["task"] in tasks)), 4)


def summarize(rows, candidate, comparator):
    """Arm totals, the split between tasks both arms always solve and the rest, and each task's paired gap."""
    by_task = collections.defaultdict(list)
    for row in rows:
        by_task[row["task"]].append(row)
    solved = {t for t, rs in by_task.items() if all(r["passed"] for r in rs)}
    gaps = sorted(((statistics.mean(r["cost_usd"] for r in rs if r["arm"] == candidate)
                    - statistics.mean(r["cost_usd"] for r in rs if r["arm"] == comparator), t) for t, rs in by_task.items()),
                  reverse=True)
    arms = {}
    for arm in (candidate, comparator):
        own = [r for r in rows if r["arm"] == arm]
        arms[arm] = {"turns_mean": round(statistics.mean(r["turns"] for r in own), 2),
                     "turns_median": statistics.median(r["turns"] for r in own),
                     "cost_per_turn": round(sum(r["cost_usd"] for r in own) / sum(r["turns"] for r in own), 6),
                     "passed_cost_mean": round(statistics.mean(r["cost_usd"] for r in own if r["passed"]), 5),
                     "failed_cost_mean": round(statistics.mean(r["cost_usd"] for r in own if not r["passed"]), 5),
                     "orientation_names_changed_file": sum(r["orientation_names_changed_file"] for r in own),
                     "first_read_is_changed_file": sum(r["first_changed_file_read"] == 1 for r in own),
                     "operations": dict(sum((collections.Counter(r["operations"]) for r in own), collections.Counter())),
                     "total_cost_usd": total(rows, arm), "always_solved_cost_usd": total(rows, arm, solved)}
    return {"arms": arms, "always_solved_tasks": len(solved), "tasks": len(by_task),
            "largest_gaps": [{"task": t, "mean_cost_gap_usd": round(g, 5)} for g, t in gaps[:5]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--batch", required=True)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--comparator", required=True)
    args = parser.parse_args()
    batch = json.loads((BASE / "benchmark/preregistration.json").read_text())["agent_trials"]["batches"][args.batch]
    catalog = {t["id"]: t for t in json.loads((BASE / batch["catalog"]).read_text())}
    rows = []
    for path in sorted((BASE / ".research/trials").glob(f"{args.batch}-*/summary.json")):
        summary = json.loads(path.read_text())
        if summary["arm"] in (args.candidate, args.comparator):
            rows.append(attempt_row(summary, path.parent, {f["path"] for f in catalog[summary["task"]]["faults"]}))
    result = {"batch": args.batch, "status": "post-hoc exploratory diagnostic, not preregistered",
              **summarize(rows, args.candidate, args.comparator), "attempts": rows}
    out = BASE / f"results/agent-cost-diagnostic-{args.batch}.json"
    out.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "attempts"}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
