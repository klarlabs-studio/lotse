"""Pool cited files for blind relevance judging, and merge the judgments.

    python benchmark/pool_judgments.py pool     # results/heldout-pool.json
    python benchmark/pool_judgments.py check    # validates results/heldout-judgments.json

Pooling takes the first POOL_DEPTH unique code files that every arm and Graphify
cited for each answerable held-out query, removes files the author already
labeled, and shuffles the rest with a fixed seed. The pool names no system, so a
judge cannot tell who cited a file. Relevance then counts the author's labels
plus every pooled file judged relevant (see research/protocol.md).
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import sys

BASE = Path(__file__).resolve().parents[1]
POOL_DEPTH = 10
SEED = 20261018
DOC_SUFFIXES = {".md", ".mdx", ".rst", ".txt"}
COMPARISON = BASE / "results/heldout-comparison.json"
POOL = BASE / "results/heldout-pool.json"
JUDGMENTS = BASE / "results/heldout-judgments.json"


def is_code(path):
    return Path(path).suffix not in DOC_SUFFIXES


def labeled_code(case):
    return {e["path"] for e in case["evidence"] if is_code(e["path"])}


def build_pool(rows, cases):
    """Map query id to the shuffled, unlabeled code files cited near the top by any system."""
    rng = random.Random(SEED)
    pool = {}
    for query_id in sorted({row["id"] for row in rows}):
        case = cases[query_id]
        labeled = labeled_code(case)
        if not labeled:
            continue  # No code evidence: excluded from quality scoring, so nothing to judge.
        cited = set()
        for row in (r for r in rows if r["id"] == query_id):
            for tool in row["tools"].values():
                cited.update([p for p in tool["cited_path_order"] if is_code(p)][:POOL_DEPTH])
        files = sorted(cited - labeled)
        rng.shuffle(files)
        pool[query_id] = files
    return pool


def relevant_files(case, judgments):
    """Author-labeled code files plus pooled files judged relevant."""
    judged = judgments.get(case["id"], {})
    return labeled_code(case) | {path for path, verdict in judged.items() if verdict["relevant"]}


def check_judgments(pool, judgments):
    errors = []
    for query_id, files in pool.items():
        judged = judgments.get(query_id, {})
        missing = set(files) - set(judged)
        extra = set(judged) - set(files)
        if missing or extra:
            errors.append(f"{query_id}: missing {sorted(missing)[:3]} extra {sorted(extra)[:3]}")
        for path, verdict in judged.items():
            if not isinstance(verdict.get("relevant"), bool) or not verdict.get("reason"):
                errors.append(f"{query_id}:{path}: needs boolean relevant and a reason")
    errors += [f"{q}: not in pool" for q in set(judgments) - set(pool)]
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("pool", "check"))
    parser.add_argument("--comparison", type=Path, default=COMPARISON)
    parser.add_argument("--pool", type=Path, default=POOL)
    parser.add_argument("--judgments", type=Path, default=JUDGMENTS)
    args = parser.parse_args()
    comparison = json.loads(args.comparison.read_text())
    cases = {c["id"]: c for c in json.loads((BASE / comparison["queries"]).read_text())}
    if args.action == "pool":
        pool = build_pool(comparison["rows"], cases)
        if args.pool.exists():
            raise ValueError("Refusing to overwrite an existing blind relevance pool")
        args.pool.write_text(json.dumps({"depth": POOL_DEPTH, "seed": SEED,
            "comparison_sha256": hashlib.sha256(args.comparison.read_bytes()).hexdigest(),
            "query_sha256": comparison["query_sha256"], "queries": {
            q: {"repo": cases[q]["repo"], "query": cases[q]["query"], "files": files}
            for q, files in pool.items()}}, indent=1) + "\n")
        print(f"{len(pool)} queries, {sum(map(len, pool.values()))} files to judge -> {args.pool}")
        return 0
    pool = {q: v["files"] for q, v in json.loads(args.pool.read_text())["queries"].items()}
    errors = check_judgments(pool, json.loads(args.judgments.read_text()))
    print("\n".join(errors[:20]) or "judgments complete")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
