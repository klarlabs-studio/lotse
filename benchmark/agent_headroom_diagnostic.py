"""Post-hoc diagnostic of how much of an agent attempt's cost its code-search payloads cause; no model calls.

    python benchmark/agent_headroom_diagnostic.py --batches t5 t6 t7 t8 t9

Exploratory and not preregistered. A tool result enters the context once (a cache write) and is re-read on every
later model call (cache reads), so its cost is its size times the write price plus its size times the read price for
each later call. Sizes come from the model's own usage: each call's new cache tokens are split between the tool
results that arrived since the previous call in proportion to their cl100k sizes; the remainder is the agent's own
carried history. Search and read results before the first edit bound what any code-search tool could save.
Writes results/agent-headroom-diagnostic.json.
"""
import argparse
import collections
import json
from pathlib import Path
import statistics
import sys

from retrieval import tokens

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "results/agent-headroom-diagnostic.json"
PRICE = {"input": 0.1e-6, "output": 0.5e-6, "cache_write_5m": 0.125e-6, "cache_write_1h": 0.2e-6, "cache_read": 0.01e-6}
SEARCH_TOOLS = {"search", "read", "retrieve", "outline", "find",
                "Grep", "Glob", "Read",  # Claude Code built-ins (installed arms, t10).
                "triage", "units", "locate", "expand",  # graf as installed.
                "Bash"}  # graphify_installed allows Bash only as Bash(graphify:*).
EDIT_TOOLS = {"edit", "Edit", "Write", "MultiEdit"}


def calls_and_results(stream):
    """Model calls in order (usage deduplicated by message id) and the tool results that followed each call,
    with each result's tool arguments and text alongside."""
    calls, names, arguments, seen, output = [], {}, {}, set(), 0
    for line in stream.read_text().splitlines():
        record = json.loads(line)
        if record.get("type") == "result":
            output += record["usage"]["output_tokens"]
        elif record.get("type") == "assistant":
            message = record["message"]
            for block in message["content"]:
                if block.get("type") == "tool_use":
                    names[block["id"]] = block["name"].rsplit("__", 1)[-1]
                    arguments[block["id"]] = block.get("input") or {}
            if message["id"] not in seen:
                seen.add(message["id"])
                calls.append({"usage": message["usage"], "results": [], "arguments": [], "texts": []})
        elif record.get("type") == "user" and calls and isinstance(record["message"]["content"], list):
            for block in record["message"]["content"]:
                if block.get("type") == "tool_result":
                    text = "".join(part.get("text", "") for part in block["content"]) \
                        if isinstance(block["content"], list) else str(block["content"])
                    calls[-1]["results"].append((names.get(block["tool_use_id"], "unknown"), len(tokens(text))))
                    calls[-1]["arguments"].append(arguments.get(block["tool_use_id"], {}))
                    calls[-1]["texts"].append(text)
    return calls, output


def write_price(usage):
    split = usage.get("cache_creation") or {}
    written = usage.get("cache_creation_input_tokens", 0)
    if not written:
        return PRICE["cache_write_1h"]
    return (split.get("ephemeral_5m_input_tokens", 0) * PRICE["cache_write_5m"]
            + split.get("ephemeral_1h_input_tokens", written) * PRICE["cache_write_1h"]) / written


def attribute(calls, output):
    """Cost by component: base context, each tool's results (all and before the first edit), history, output.

    Streamed messages carry partial output counts, so output (thinking included) comes from the final result record.
    """
    cost = collections.Counter({"output": output * PRICE["output"]})
    edited = False
    for index, call in enumerate(calls):
        usage = call["usage"]
        cost["uncached_input"] += usage.get("input_tokens", 0) * PRICE["input"]
        later_calls = len(calls) - index - 1
        if index == 0:
            cost["base_context"] += usage.get("cache_read_input_tokens", 0) * PRICE["cache_read"] \
                + usage.get("cache_creation_input_tokens", 0) * write_price(usage)
            cost["base_context"] += (usage.get("cache_read_input_tokens", 0) + usage.get("cache_creation_input_tokens", 0)) \
                * PRICE["cache_read"] * later_calls
        previous = calls[index - 1] if index else None
        if previous is None:
            continue
        new = usage.get("cache_creation_input_tokens", 0) + usage.get("input_tokens", 0)
        measured = sum(size for _, size in previous["results"])
        scale = min(1.0, new / measured) if measured else 0.0
        per_token = write_price(usage) + PRICE["cache_read"] * later_calls
        carried = new
        for name, size in previous["results"]:
            share = size * scale
            carried -= share
            cost[f"tool:{name}"] += share * per_token
            if not edited and name in SEARCH_TOOLS:
                cost["search_before_first_edit"] += share * per_token
            if name in EDIT_TOOLS:
                edited = True
        cost["history"] += max(carried, 0) * per_token
    return cost


def attempt(directory):
    summary = json.loads((directory / "summary.json").read_text())
    calls, output = calls_and_results(directory / "agent-stream.jsonl")
    cost = attribute(calls, output)
    search = sum(v for k, v in cost.items() if k.startswith("tool:") and k[5:] in SEARCH_TOOLS)
    first_edit = next((i + 1 for i, call in enumerate(calls) if any(name in EDIT_TOOLS for name, _ in call["results"])),
                      len(calls))
    return {"trial": directory.name, "task": summary["task"], "arm": summary["arm"], "passed": summary["passed"],
            "calls": len(calls), "calls_to_first_edit": first_edit, "cost_usd": summary["audit"]["cost_usd"],
            "attributed_usd": round(sum(v for k, v in cost.items() if k != "search_before_first_edit"), 6),
            "search_payload_usd": round(search, 6), **{k: round(v, 6) for k, v in sorted(cost.items())}}


def arm_summary(rows):
    cost = sum(r["cost_usd"] for r in rows)
    components = collections.Counter()
    for row in rows:
        components.update({k: v for k, v in row.items() if k in ("output", "uncached_input", "base_context", "history",
                                                                     "search_before_first_edit") or k.startswith("tool:")})
    return {"attempts": len(rows), "passed": sum(r["passed"] for r in rows), "cost_usd": round(cost, 4),
            "attribution_coverage": round(sum(r["attributed_usd"] for r in rows) / cost, 3) if cost else None,
            "search_payload_share": round(sum(r["search_payload_usd"] for r in rows) / cost, 3) if cost else None,
            "search_before_first_edit_share": round(components["search_before_first_edit"] / cost, 3) if cost else None,
            "median_calls": statistics.median(r["calls"] for r in rows),
            "median_calls_to_first_edit": statistics.median(r["calls_to_first_edit"] for r in rows),
            "calls_to_first_edit_share": round(sum(r["calls_to_first_edit"] for r in rows) / sum(r["calls"] for r in rows), 3),
            "shares": {k: round(v / cost, 3) for k, v in sorted(components.items())} if cost else {}}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--batches", nargs="+", required=True)
    args = parser.parse_args()
    rows, report = [], {}
    for batch in args.batches:
        own = [attempt(path.parent) for path in sorted((BASE / ".research/trials").glob(f"{batch}-*/summary.json"))
               if (path.parent / "agent-stream.jsonl").is_file()
               and json.loads(path.read_text()).get("audit", {}).get("cost_usd") is not None]
        rows.extend({"batch": batch, **row} for row in own)
        by_arm = collections.defaultdict(list)
        for row in own:
            by_arm[row["arm"]].append(row)
        report[batch] = {arm: arm_summary(arm_rows) for arm, arm_rows in sorted(by_arm.items())}
    OUT.write_text(json.dumps({"exploratory": True, "prices_per_token": PRICE, "batches": report, "rows": rows},
                              indent=2) + "\n")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
