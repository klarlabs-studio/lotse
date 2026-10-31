"""Batch t12 or t13 diagnostics, no model calls: the declared ranking secondary (natural_tasks_t12.batch_plan, v128) and
per-attempt localization. Writes results/<batch>-diagnostic.json.

    python benchmark/t12_diagnostic.py [t13]

Ranking: per task, the share of a graf arm's attempts whose first triage call lists a faulted file first;
graf_installed_codefiles against graf_installed by a one-sided paired sign flip at alpha 0.05, over tasks where both
arms called triage. Reported, not a promotion gate. Also per arm: whether the first triage call names a faulted file
(the v126 hit measure), turns, tool calls and the calls before a faulted file is first read.
"""
import json
from pathlib import Path
import statistics
import sys

from paired_stats import sign_flip_p

BASE = Path(__file__).resolve().parents[1]
ARMS = {"t12": ("builtin", "graphify_installed", "graf_installed", "graf_installed_codefiles"),
        "t13": ("builtin", "graphify_installed", "graf_installed")}
GRAF_ARMS = ("graf_installed", "graf_installed_codefiles")
ALPHA = 0.05


def events(stream):
    for line in stream.read_text().splitlines():
        try:
            yield json.loads(line)
        except ValueError:
            continue


def tool_calls(stream):
    """[(turn, name, input, result text)] in call order."""
    calls, results, turns = [], {}, set()
    for event in events(stream):
        message = event.get("message") if isinstance(event.get("message"), dict) else {}  # permission_denied: text
        content = message.get("content") if isinstance(message.get("content"), list) else []
        if event.get("type") == "assistant":
            turns.add(message.get("id"))
            calls += [(len(turns), b["id"], b["name"].split("__")[-1], b.get("input", {}))
                      for b in content if isinstance(b, dict) and b.get("type") == "tool_use"]
        elif event.get("type") == "user":
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    value = block.get("content")
                    results[block["tool_use_id"]] = value if isinstance(value, str) else json.dumps(value)
    return [(turn, name, args, results.get(use, "")) for turn, use, name, args in calls], len(turns)


def triage_files(result):
    """The files a triage result lists under "Likely files:", in order; [] when it lists none."""
    texts = [b.get("text", "") for b in _blocks(result)] or [result]
    for text in texts:
        lines = text.splitlines()
        if "Likely files:" in lines:
            listed = lines[lines.index("Likely files:") + 1:]
            return [line[2:] for line in listed[:next((i for i, l in enumerate(listed) if not l.startswith("- ")),
                                                       len(listed))]]
    return []


def _blocks(result):
    try:
        value = json.loads(result)
    except (TypeError, ValueError):
        return []
    return [b for b in value if isinstance(b, dict)] if isinstance(value, list) else []


def attempt(summary, faulted):
    calls, turns = tool_calls(summary.parent / "agent-stream.jsonl")
    record = json.loads(summary.read_text())
    touches = lambda text: any(path in text for path in faulted)
    first_read = next((i for i, (_, name, args, _) in enumerate(calls)
                       if name in ("Read", "Edit", "outline", "units") and touches(json.dumps(args))), None)
    triage = [result for _, name, _, result in calls if name == "triage"]
    files = triage_files(triage[0]) if triage else []
    return {"task": record["task"], "arm": record["arm"], "passed": record["passed"],
            "cost_usd": record["audit"]["cost_usd"], "turns": turns, "tool_calls": len(calls),
            "calls_before_faulted_read": first_read, "triage_called": bool(triage),
            "triage_names_faulted": bool(triage) and touches(triage[0]),
            "first_file_faulted": bool(files) and files[0] in faulted}


def ranking(rows):
    """Per-task first-file shares of the two graf arms and the one-sided paired sign flip."""
    share = {}
    for arm in GRAF_ARMS:
        for task in {r["task"] for r in rows}:
            called = [r for r in rows if r["arm"] == arm and r["task"] == task and r["triage_called"]]
            if called:
                share[(arm, task)] = sum(r["first_file_faulted"] for r in called) / len(called)
    tasks = sorted({t for _, t in share if all((a, t) in share for a in GRAF_ARMS)})
    diffs = [share[(GRAF_ARMS[1], t)] - share[(GRAF_ARMS[0], t)] for t in tasks]
    p = sign_flip_p(diffs) if diffs else 1.0
    return {"tasks": len(tasks), "mean_share": {a: statistics.fmean(share[(a, t)] for t in tasks) if tasks else None
                                                for a in GRAF_ARMS},
            "mean_difference": statistics.fmean(diffs) if diffs else None, "p": p, "alpha": ALPHA,
            "codefiles_ranks_better": p <= ALPHA}


def main(batch="t12"):
    catalog = {t["id"]: {f["path"] for f in t["faults"]}
               for t in json.loads((BASE / f"benchmark/natural-tasks-{batch}.json").read_text())}
    rows = [attempt(s, catalog[json.loads(s.read_text())["task"]])
            for s in sorted((BASE / ".research/trials").glob(f"{batch}-*/summary.json"))]
    per_arm = {}
    for arm in ARMS[batch]:
        r = [x for x in rows if x["arm"] == arm]
        reads = [x["calls_before_faulted_read"] for x in r if x["calls_before_faulted_read"] is not None]
        per_arm[arm] = {"attempts": len(r), "median_turns": statistics.median(x["turns"] for x in r) if r else None,
                        "median_tool_calls": statistics.median(x["tool_calls"] for x in r) if r else None,
                        "faulted_file_read": len(reads),
                        "median_calls_before_faulted_read": statistics.median(reads) if reads else None,
                        **({"triage_names_faulted": sum(x["triage_names_faulted"] for x in r),
                            "first_file_faulted": sum(x["first_file_faulted"] for x in r)} if arm in GRAF_ARMS else {})}
    result = {"ranking_secondary": ranking(rows) if set(GRAF_ARMS) <= set(ARMS[batch]) else None,
              "arms": per_arm, "attempts": rows}
    (BASE / f"results/{batch}-diagnostic.json").write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({"ranking_secondary": result["ranking_secondary"], "arms": per_arm}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(*sys.argv[1:]))
