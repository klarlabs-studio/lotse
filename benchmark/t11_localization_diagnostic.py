"""Post-hoc t11 diagnostic, exploratory, no model calls: per attempt, tool calls before a faulted file is read or named,
and whether graf's triage named one. Writes results/t11-localization-diagnostic.json.

    python benchmark/t11_localization_diagnostic.py
"""
import json, glob, collections, statistics, re
from pathlib import Path
BASE = str(Path(__file__).resolve().parents[1])
cat = {t["id"]: {f["path"] for f in t["faults"]} for t in json.load(open(f"{BASE}/benchmark/natural-tasks-t11.json"))}
ARMS = ["builtin", "graphify_installed", "graf_installed"]
rows = []
for p in sorted(glob.glob(f"{BASE}/.research/trials/t11-*/summary.json")):
    s = json.load(open(p)); d = p.rsplit("/", 1)[0]; gold = cat[s["task"]]
    uses, results, msgs = [], {}, set()
    for line in open(f"{d}/agent-stream.jsonl"):
        try: e = json.loads(line)
        except ValueError: continue
        m = e.get("message") or {}
        if e.get("type") == "assistant":
            msgs.add(m.get("id"))
            for b in m.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    uses.append((len(msgs), b["id"], b["name"].split("__")[-1], json.dumps(b.get("input", {}))))
        elif e.get("type") == "user":
            for b in m.get("content") or [] if isinstance(m.get("content"), list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_result":
                    c = b.get("content"); results[b["tool_use_id"]] = json.dumps(c) if not isinstance(c, str) else c
    def touches(text): return any(g in text for g in gold)
    first_read = next((i for i, (_, _, n, a) in enumerate(uses) if n in ("Read", "Edit", "outline", "units") and touches(a)), None)
    first_named = next((i for i, (_, tid, n, a) in enumerate(uses) if touches(results.get(tid, ""))), None)
    triage = [results.get(tid, "") for _, tid, n, _ in uses if n == "triage"]
    rows.append({"task": s["task"], "arm": s["arm"], "passed": s["passed"], "cost": s["audit"]["cost_usd"],
                 "calls": len(msgs), "tools": len(uses), "tools_before_gold_read": first_read,
                 "tools_before_gold_named": first_named,
                 "triage_names_gold": bool(triage) and touches(triage[0]),
                 "triage_tokens": len(triage[0]) // 4 if triage else 0})
json.dump(rows, open(f"{BASE}/results/t11-localization-diagnostic.json", "w"), indent=1)
for a in ARMS:
    r = [x for x in rows if x["arm"] == a]
    gr = [x["tools_before_gold_read"] for x in r if x["tools_before_gold_read"] is not None]
    gn = [x["tools_before_gold_named"] for x in r if x["tools_before_gold_named"] is not None]
    print(a, "median calls", statistics.median(x["calls"] for x in r), "tools", statistics.median(x["tools"] for x in r),
          "| gold read in", len(gr), "median tools before", statistics.median(gr),
          "| gold named median", statistics.median(gn),
          "| failed-attempt cost share", round(sum(x["cost"] for x in r if not x["passed"]) / sum(x["cost"] for x in r), 2))
g = [x for x in rows if x["arm"] == "graf_installed"]
print("triage names a faulted file:", sum(x["triage_names_gold"] for x in g), "/", len(g),
      "median triage tokens", statistics.median(x["triage_tokens"] for x in g))
hit = [x for x in g if x["triage_names_gold"]]; miss = [x for x in g if not x["triage_names_gold"]]
for name, grp in (("hit", hit), ("miss", miss)):
    print(name, len(grp), "mean cost", round(statistics.mean(x["cost"] for x in grp), 4),
          "pass", sum(x["passed"] for x in grp), "median tools before gold read",
          statistics.median([x["tools_before_gold_read"] for x in grp if x["tools_before_gold_read"] is not None] or [0]))
