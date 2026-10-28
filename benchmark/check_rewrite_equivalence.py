"""Check that `graf rewrite` returns the prototype's rewritten query for every catalogued issue.

    python benchmark/check_rewrite_equivalence.py [--binary PATH]

Covers the localization screen, replication, and confirmation catalogs. A diagnostic,
not a benchmark. Results go to results/rewrite-equivalence.json.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

from localization import SPLITS, rewrite_query, write_json_atomic
from retrieval import BASE


def issues():
    seen = {}
    for catalog, _ in SPLITS.values():
        for task in json.loads(catalog.read_text())["tasks"]:
            seen.setdefault(task["id"], task["query"])
    return seen


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--binary", type=Path, default=BASE / "target/release/graf")
    args = parser.parse_args()
    differences = []
    texts = issues()
    for task, text in texts.items():
        actual = subprocess.run([str(args.binary), "rewrite", "--", text], check=True, capture_output=True,
                                text=True).stdout.removesuffix("\n")
        if actual != rewrite_query(text):
            differences.append({"task": task, "prototype": rewrite_query(text), "port": actual})
    write_json_atomic(BASE / "results/rewrite-equivalence.json",
                      {"issues": len(texts), "identical": len(texts) - len(differences), "differences": differences})
    print(f"{len(texts) - len(differences)}/{len(texts)} identical")
    return 0 if not differences else 1


if __name__ == "__main__":
    sys.exit(main())
