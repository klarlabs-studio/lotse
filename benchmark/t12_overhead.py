"""Batch t12 post-hoc: graf's fixed context overhead and what it costs, no model calls. Writes results/t12-overhead.json.

    python benchmark/t12_overhead.py

Per attempt, from the recorded stream: the first prompt (input + cache write + cache read tokens of the first model
call), the model calls, and every graf tool call with its result size. The fixed overhead of an arm is its paired
per-task first-prompt excess over builtin; priced as one 1-hour cache write plus a cache read on every later call
(run_agent_trial.STREAM_PRICES). The counterfactual subtracts that price from each graf attempt and repeats the
batch's paired sign flip against builtin. Exploratory: the overhead was chosen for removal after seeing t12.
"""
import json
from pathlib import Path
import statistics

from paired_stats import sign_flip_p
from run_agent_trial import STREAM_PRICES
from t12_diagnostic import events, tool_calls

BASE = Path(__file__).resolve().parents[1]
ARMS = ("builtin", "graphify_installed", "graf_installed", "graf_installed_codefiles")
PRICE = STREAM_PRICES["claude-haiku-5-5"]["base"]


def calls(stream):
    """Usage of each model call, once per message id (a message streams one event per content block)."""
    seen = {}
    for event in events(stream):
        message = event.get("message") if isinstance(event.get("message"), dict) else {}
        if event.get("type") == "assistant" and message.get("usage"):
            seen.setdefault(message.get("id"), message["usage"])
    return list(seen.values())


def prompt(usage):
    return usage["input_tokens"] + usage["cache_creation_input_tokens"] + usage["cache_read_input_tokens"]


def attempt(summary):
    record = json.loads(summary.read_text())
    stream = summary.parent / "agent-stream.jsonl"
    usages = calls(stream)
    graf = [(name, len(result)) for _, name, _, result in tool_calls(stream)[0]
            if name in ("triage", "outline", "units", "search", "locate", "expand", "index")]
    return {"task": record["task"], "arm": record["arm"], "cost_usd": record["audit"]["cost_usd"],
            "model_calls": len(usages), "first_prompt_tokens": prompt(usages[0]) if usages else None,
            "graf_calls": graf}


def overhead_usd(tokens, model_calls):
    return tokens * (PRICE["cache_write_1h"] + (model_calls - 1) * PRICE["cache_read"]) / 1e6


def main():
    rows = [attempt(s) for s in sorted((BASE / ".research/trials").glob("t12-*/summary.json"))]
    tasks = sorted({r["task"] for r in rows})
    by = {(a, t): [r for r in rows if r["arm"] == a and r["task"] == t] for a in ARMS for t in tasks}
    mean = lambda xs: statistics.fmean(xs)
    base_prompt = {t: mean(r["first_prompt_tokens"] for r in by[("builtin", t)]) for t in tasks}
    result = {"arms": {}, "counterfactual_without_fixed_overhead": {}}
    for arm in ARMS:
        arm_rows = [r for r in rows if r["arm"] == arm]
        excess = [mean(r["first_prompt_tokens"] for r in by[(arm, t)]) - base_prompt[t] for t in tasks]
        tools = {}
        for r in arm_rows:
            for name, size in r["graf_calls"]:
                tools.setdefault(name, []).append(size)
        result["arms"][arm] = {
            "median_first_prompt_tokens": statistics.median(r["first_prompt_tokens"] for r in arm_rows),
            "median_first_prompt_excess_over_builtin": statistics.median(excess),
            "median_model_calls": statistics.median(r["model_calls"] for r in arm_rows),
            "graf_tool_calls": {n: {"calls": len(v), "median_result_chars": statistics.median(v)}
                                for n, v in sorted(tools.items())}}
        if arm.startswith("graf"):
            fixed = statistics.median(excess)
            priced = [overhead_usd(fixed, r["model_calls"]) for r in arm_rows]
            diffs, raw = [], []
            for t in tasks:
                cost = mean(r["cost_usd"] for r in by[(arm, t)])
                saved = mean(overhead_usd(fixed, r["model_calls"]) for r in by[(arm, t)])
                base = mean(r["cost_usd"] for r in by[("builtin", t)])
                raw.append(base - cost)
                diffs.append(base - (cost - saved))
            result["counterfactual_without_fixed_overhead"][arm] = {
                "fixed_overhead_tokens": fixed, "median_overhead_usd_per_attempt": statistics.median(priced),
                "share_of_arm_cost": sum(priced) / sum(r["cost_usd"] for r in arm_rows),
                "observed": {"mean_reduction_vs_builtin": mean(raw), "cheaper_tasks": sum(d > 0 for d in raw),
                             "p": sign_flip_p(raw)},
                "without_overhead": {"mean_reduction_vs_builtin": mean(diffs),
                                     "cheaper_tasks": sum(d > 0 for d in diffs), "p": sign_flip_p(diffs)}}
    (BASE / "results/t12-overhead.json").write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
