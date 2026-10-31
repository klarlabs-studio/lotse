"""Scoping for a large-repository workload, no model calls. Writes results/large-repo-scope.json.

    python benchmark/large_repo_scope.py

Exploratory, chosen after t10 to t13 were seen: hypothesis generation for a new preregistration, never evidence.
Per natural task of those batches, the snapshot's size (code files and bytes, outside dependency and output
folders) and how much searching builtin needed (tool calls before a faulted file is first read, from the
t12_diagnostic stream reader), against graf_installed's per-task cost difference from builtin. t10 and t11 ran
graf-t10, t12 and t13 graf-triage15. Also every snapshot under .research/corpora ranked by size.
"""
import json
from pathlib import Path
import statistics

from t12_diagnostic import attempt

BASE = Path(__file__).resolve().parents[1]
CORPORA = BASE / ".research/corpora"
BATCHES = ("t10", "t11", "t12", "t13")
CODE = {".go", ".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".svelte", ".vue", ".rs", ".java", ".rb"}
SKIPPED = {".git", "node_modules", "vendor", "dist", "build", "graphify-out", ".graf", "__pycache__"}


def size(root):
    """Code files and bytes under a snapshot, skipping dependency, build and tool output folders."""
    files = [p for p in root.rglob("*") if p.suffix in CODE and p.is_file()
             and not SKIPPED & set(p.relative_to(root).parts)]
    return {"code_files": len(files), "code_bytes": sum(p.stat().st_size for p in files)}


def ranks(values):
    order = sorted(range(len(values)), key=values.__getitem__)
    result = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            result[order[k]] = (i + j) / 2
        i = j + 1
    return result


def spearman(xs, ys):
    rx, ry = ranks(xs), ranks(ys)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    return cov / (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5


def split_half(attempts_by_task):
    """builtin's tool calls in one attempt against graf's saving over builtin's other attempt, both ways round.

    Grouping and comparison then use different builtin attempts, so a task where builtin was unlucky once does
    not make graf look cheaper. Each task contributes two pairs; they are not independent.
    """
    pairs = []
    for builtin, graf in attempts_by_task:
        if len(builtin) == 2:
            graf_cost = statistics.fmean(r["cost_usd"] for r in graf)
            pairs += [(builtin[0]["tool_calls"], builtin[1]["cost_usd"] - graf_cost),
                      (builtin[1]["tool_calls"], builtin[0]["cost_usd"] - graf_cost)]
    ordered = sorted(pairs)
    third = len(ordered) // 3
    return {"pairs": len(pairs), "spearman": spearman([c for c, _ in pairs], [s for _, s in pairs]),
            "terciles": {name: {"builtin_tool_calls_range": [g[0][0], g[-1][0]],
                                "mean_graf_saving_usd": statistics.fmean(s for _, s in g),
                                "graf_cheaper_pairs": sum(s > 0 for _, s in g), "pairs": len(g)}
                         for name, g in (("low", ordered[:third]), ("middle", ordered[third:-third]),
                                         ("high", ordered[-third:]))}}


def tasks(sizes, paired):
    rows = []
    for batch in BATCHES:
        catalog = {t["id"]: t for t in json.loads((BASE / f"benchmark/natural-tasks-{batch}.json").read_text())}
        attempts = {}
        for summary in sorted((BASE / ".research/trials").glob(f"{batch}-*/summary.json")):
            task = json.loads(summary.read_text())["task"]
            row = attempt(summary, {f["path"] for f in catalog[task]["faults"]})
            attempts.setdefault((task, row["arm"]), []).append(row)
        for task_id, task in catalog.items():
            builtin, graf = attempts[(task_id, "builtin")], attempts[(task_id, "graf_installed")]
            paired.append((builtin, graf))
            reads = [r["calls_before_faulted_read"] for r in builtin if r["calls_before_faulted_read"] is not None]
            rows.append({"batch": batch, "task": task_id, "repo": task["repo"], **sizes[task["repo"]],
                         "builtin_calls_before_faulted_read": statistics.median(reads) if reads else None,
                         "builtin_tool_calls": statistics.fmean(r["tool_calls"] for r in builtin),
                         "builtin_cost_usd": statistics.fmean(r["cost_usd"] for r in builtin),
                         "graf_cost_usd": statistics.fmean(r["cost_usd"] for r in graf)})
    for row in rows:
        row["graf_saving_usd"] = row["builtin_cost_usd"] - row["graf_cost_usd"]
    return rows


def by_tercile(rows, key):
    ordered = sorted(rows, key=lambda r: r[key])
    third = len(ordered) // 3
    groups = {"low": ordered[:third], "middle": ordered[third:-third], "high": ordered[-third:]}
    return {name: {"tasks": len(g), f"{key}_range": [g[0][key], g[-1][key]],
                   "mean_graf_saving_usd": statistics.fmean(r["graf_saving_usd"] for r in g),
                   "graf_cheaper_tasks": sum(r["graf_saving_usd"] > 0 for r in g)}
            for name, g in groups.items()}


def main():
    sizes = {p.name: size(p) for p in sorted(CORPORA.iterdir())
             if p.is_dir() and not p.name.startswith("graphify")}
    paired = []
    rows = tasks(sizes, paired)
    searched = [r for r in rows if r["builtin_calls_before_faulted_read"] is not None]
    result = {
        "exploratory": "chosen after t10 to t13 were seen; for scoping a new preregistration only",
        "tasks": len(rows),
        "spearman_graf_saving": {
            "code_files": spearman([r["code_files"] for r in rows], [r["graf_saving_usd"] for r in rows]),
            "builtin_tool_calls": spearman([r["builtin_tool_calls"] for r in rows],
                                           [r["graf_saving_usd"] for r in rows]),
            "builtin_calls_before_faulted_read": spearman(
                [r["builtin_calls_before_faulted_read"] for r in searched],
                [r["graf_saving_usd"] for r in searched])},
        "terciles": {key: by_tercile(rows, key) for key in ("code_files", "builtin_tool_calls")},
        "split_half_builtin_tool_calls": split_half(paired),
        "used_repositories": sorted({r["repo"] for r in rows}),
        "snapshots_by_size": sorted(({"repo": k, **v} for k, v in sizes.items()),
                                    key=lambda s: -s["code_files"]),
        "per_task": rows}
    (BASE / "results/large-repo-scope.json").write_text(json.dumps(result, indent=1) + "\n")
    print(json.dumps({k: result[k] for k in ("tasks", "spearman_graf_saving", "terciles",
                                             "split_half_builtin_tool_calls")}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
