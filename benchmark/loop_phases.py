"""Exploratory, no model calls: where an attempt's tool calls go, from finished batch transcripts.

    python benchmark/loop_phases.py t14 t15

Splits each attempt's calls into phases: locate (before the first Edit), compose (from the first Edit to the first
test run), and debug (after the first test run). For graf_installed it also classifies each Read and Grep after the
first triage call: a Read of a file triage listed, and whether triage's excerpt already covered the lines read; a Grep
whose pattern names an identifier triage's output showed. Writes results/loop-phases.json. Diagnostic only: it
motivates a hypothesis, it supports no claim.
"""
import json
from pathlib import Path
import re
import statistics
import sys

BASE = Path(__file__).resolve().parents[1]
ARMS = ("builtin", "graphify_installed", "graf_installed")
EXCERPT = re.compile(r"^([\w./-]+):(\d+)-(\d+)$", re.M)
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")


def calls(stream):
    """[(name, input, result text)] in call order."""
    out, index = [], {}
    for line in stream.read_text().splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") == "tool_use":
                index[block["id"]] = len(out)
                out.append([block["name"].split("__")[-1], block.get("input") or {}, ""])
            elif block.get("type") == "tool_result" and block.get("tool_use_id") in index:
                text = block.get("content")
                out[index[block["tool_use_id"]]][2] = text if isinstance(text, str) else json.dumps(text)
    return out


def relative(path, root):
    return path.split("/workspace/", 1)[-1] if "/workspace/" in path else path.removeprefix(root)


def phases(seq):
    names = [name for name, _, _ in seq]
    edit = next((i for i, n in enumerate(names) if n in ("Edit", "Write", "MultiEdit")), len(names))
    test = next((i for i, n in enumerate(names) if n == "test"), len(names))
    return {"locate": edit, "compose": max(0, test - edit), "debug": max(0, len(names) - test - 1)}


def after_triage(seq):
    """Reads and Greps after the first triage call, classified against what triage returned."""
    first = next((i for i, (n, _, _) in enumerate(seq) if n == "triage"), None)
    if first is None:
        return None
    text = seq[first][2].replace("\\n", "\n")
    listed = set(re.findall(r"^- ([\w./-]+)$", text, re.M))
    excerpts = [(p, int(a), int(b)) for p, a, b in EXCERPT.findall(text)]
    shown = set(IDENT.findall(text))
    out = {"read_listed": 0, "read_unlisted": 0, "read_covered": 0, "grep_shown_ident": 0, "grep_other": 0,
           "outline": 0, "other_graf": 0}
    for name, args, _ in seq[first + 1:]:
        if name == "Read":
            path = relative(args.get("file_path", ""), "")
            out["read_listed" if path in listed else "read_unlisted"] += 1
            start = int(args.get("offset") or 1)
            end = start + int(args.get("limit") or 2000)
            if any(p == path and a <= start and end <= b + 1 for p, a, b in excerpts):
                out["read_covered"] += 1
        elif name == "Grep":
            words = set(IDENT.findall(args.get("pattern", "")))
            out["grep_shown_ident" if words & shown else "grep_other"] += 1
        elif name == "outline":
            out["outline"] += 1
        elif name in ("units", "search", "context", "symbols", "callers"):
            out["other_graf"] += 1
    excerpt_test = bool(excerpts) and bool(re.search(r"(_test\.|test_|\.test\.|\.spec\.|/tests?/)", excerpts[0][0]))
    return {**out, "first_excerpt_is_test": excerpt_test}


def main(batches):
    rows = []
    for batch in batches:
        for summary in sorted((BASE / ".research/trials").glob(f"{batch}-*/summary.json")):
            directory = summary.parent
            if directory.name.startswith(f"{batch}-screen-"):
                continue
            arm = next((a for a in ARMS if f"-{a}-" in directory.name), None)
            if arm is None:
                continue
            seq = calls(directory / "agent-stream.jsonl")
            row = {"batch": batch, "trial": directory.name, "arm": arm, "calls": len(seq), **phases(seq)}
            if arm == "graf_installed":
                row["after_triage"] = after_triage(seq)
            rows.append(row)
    report = {}
    for arm in ARMS:
        mine = [r for r in rows if r["arm"] == arm]
        report[arm] = {"attempts": len(mine), **{f"median_{k}": statistics.median(r[k] for r in mine)
                                                  for k in ("calls", "locate", "compose", "debug")},
                       **{f"mean_{k}": round(statistics.mean(r[k] for r in mine), 2)
                          for k in ("calls", "locate", "compose", "debug")}}
    graf = [r["after_triage"] for r in rows if r["arm"] == "graf_installed" and r["after_triage"]]
    report["graf_after_triage"] = {"attempts": len(graf),
                                   **{f"mean_{k}": round(statistics.mean(g[k] for g in graf), 2)
                                      for k in graf[0] if k != "first_excerpt_is_test"},
                                   "first_excerpt_is_test": sum(g["first_excerpt_is_test"] for g in graf)}
    (BASE / "results/loop-phases.json").write_text(json.dumps({"summary": report, "rows": rows}, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
