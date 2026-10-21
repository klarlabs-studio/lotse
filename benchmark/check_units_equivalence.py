"""Check that `graf units` returns the ast_graph prototype's payload byte for byte.

    python benchmark/check_units_equivalence.py [--limit N] [--binary PATH]

For each localization-screen task: materialize the pre-fix snapshot, run the prototype
(benchmark/ast_retrieve.py, the agent backend) and `graf units --text` with the issue
text at each budget, and compare. A diagnostic, not a benchmark: no timings, no claims.
Results go to results/units-equivalence.json.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from ast_retrieve import index as prototype_index, query as prototype_query
from localization import CATALOG, WORK, snapshot, write_json_atomic
from retrieval import BASE

BUDGETS = (500, 2000)


def compare(prototype, port):
    """Equal, or the first differing line for inspection."""
    if prototype == port:
        return None
    a, b = prototype.splitlines(), port.splitlines()
    line = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return {"line": line + 1, "prototype": a[line] if line < len(a) else None,
            "port": b[line] if line < len(b) else None}


def check_task(task, binary):
    root, files = snapshot(task)
    out = root / "units-prototype"
    prototype_index(root / "input", out)
    env = {**os.environ, "PYTHONHASHSEED": "0"}
    subprocess.run([str(binary), "--root", str(root / "input"), "index", "--rebuild"], check=True,
                   capture_output=True, env=env)
    indexed = set(json.loads((root / "input/.graf/index.json").read_text())["files"])
    rows = []
    for budget in BUDGETS:
        expected = prototype_query(out, task["query"], budget)
        actual = subprocess.run([str(binary), "--root", str(root / "input"), "units", "--budget", str(budget),
                                 "--text", "--", task["query"]], check=True, capture_output=True,
                                text=True).stdout
        rows.append({"budget": budget, "difference": compare(expected, actual)})
    shutil.rmtree(root, ignore_errors=True)
    return {"task": task["id"], "same_file_set": indexed == set(files), "budgets": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--binary", type=Path, default=BASE / "target/release/graf")
    args = parser.parse_args()
    tasks = json.loads(CATALOG.read_text())["tasks"][:args.limit]
    out = BASE / "results/units-equivalence.json"
    rows = []
    for task in tasks:
        rows.append(check_task(task, args.binary))
        same = sum(r["difference"] is None for row in rows for r in row["budgets"])
        print(task["id"], [r["difference"] is None for r in rows[-1]["budgets"]], flush=True)
        write_json_atomic(out, {"binary": str(args.binary), "tasks": len(rows), "identical": same,
                                "outputs": 2 * len(rows), "rows": rows})
    total = 2 * len(rows)
    print(f"{same}/{total} identical")
    return 0 if same == total else 1


if __name__ == "__main__":
    sys.exit(main())
