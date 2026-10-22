"""Check that `graf triage` lists the prototype's file_graph_ids files and ast_graph_ids units.

    python benchmark/check_triage_equivalence.py [--limit N] [--split SPLIT] [--binary PATH]

For each task: materialize the pre-fix snapshot, run `graf triage` (JSON), and compare its
files with the prototype's first eight file_graph_ids files, and its code section with the
prototype's ast_graph_ids payload at the budget triage had left. A diagnostic; results go
to results/triage-equivalence.json.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from localization import (GRAF_FLAGS, WEIGHT, call_edges, catalog_for, code_units, file_propagate, pack_units,
                          propagate, rewrite_query, snapshot, write_json_atomic, FLOOR)
from retrieval import BASE, BM25, tokens

BUDGET = 1500
FILES = 8


def prototype(root, files, issue, binary):
    query = rewrite_query(issue)
    units = [u for p in sorted(files) for u in code_units(p, files[p], typescript=True)]
    edges = call_edges(units)
    located = json.loads(subprocess.run([str(binary), "--root", str(root), "locate", "--budget", "2000", "--limit",
                                         "1000", *GRAF_FLAGS["graf_floor_ids"], "--", query], check=True,
                                        capture_output=True, text=True).stdout)
    ranked = file_propagate({h["path"]: h["score"] for h in located["hints"]}, units, edges, WEIGHT)[:FILES]
    context = "Likely files:\n" + "".join(f"- {p}\n" for p in ranked) + "\n" if ranked else ""
    if len(tokens(context)) > BUDGET:
        context, ranked = "", []
    remaining = max(BUDGET - len(tokens(context)) - len(tokens("Likely code:\n")), 0)
    scores = propagate(list(BM25([u["text"] for u in units]).score(query)), edges, WEIGHT)
    _, code = pack_units(units, scores, remaining, FLOOR) if remaining else ([], "")
    return ranked, context + ("Likely code:\n" + code if code else "")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--split", default="file_screen")
    parser.add_argument("--binary", type=Path, default=BASE / "target/release/graf")
    args = parser.parse_args()
    tasks = [t for t in json.loads(catalog_for(args.split).read_text())["tasks"] if t["split"] == args.split]
    rows = []
    for task in tasks[:args.limit]:
        root, files = snapshot(task)
        subprocess.run([str(args.binary), "--root", str(root / "input"), "index", "--rebuild"], check=True,
                       capture_output=True)
        actual = json.loads(subprocess.run([str(args.binary), "--root", str(root / "input"), "triage", "--budget",
                                            str(BUDGET), "--", task["query"]], check=True, capture_output=True,
                                           text=True).stdout)
        expected_files, expected_context = prototype(root / "input", files, task["query"], args.binary)
        rows.append({"task": task["id"], "files_identical": actual["files"] == expected_files,
                     "context_identical": actual["context"] == expected_context})
        shutil.rmtree(root, ignore_errors=True)
        print(task["id"], rows[-1]["files_identical"], rows[-1]["context_identical"], flush=True)
        write_json_atomic(BASE / "results/triage-equivalence.json",
                          {"split": args.split, "budget": BUDGET, "tasks": len(rows),
                           "identical": sum(r["files_identical"] and r["context_identical"] for r in rows),
                           "rows": rows})
    same = sum(r["files_identical"] and r["context_identical"] for r in rows)
    print(f"{same}/{len(rows)} identical")
    return 0 if same == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
