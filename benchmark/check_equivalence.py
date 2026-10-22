"""Check that two graf binaries produce byte-identical results on the frozen corpora.

Use this to guard refactors and performance changes that must not alter ranking,
packing, handles, or the serialized index. It is a diagnostic, not a benchmark:
it records no timings and supports no comparative efficiency claim. The index is
compared in the schema 1 JSON layout (`graf export-index` for schema 2 builds), and
`index_bytes` in build reports and stats is reported separately, since a storage
change alters it by design.

    python benchmark/check_equivalence.py OLD_BINARY NEW_BINARY --corpora DIR

DIR must contain cobra/, flask/, and fastify/ snapshot copies. Each binary gets
its own fresh copy of every repository, so neither reuses the other's index.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from retrieval import graf_index

BASE = Path(__file__).resolve().parents[1]
REPOS = ("cobra", "flask", "fastify")
BUDGETS = (0, 50, 2000, 8000)


def run(binary, root, *args):
    result = subprocess.run([binary, "--root", str(root), *args], capture_output=True)
    return {"args": list(args), "code": result.returncode, "stdout": result.stdout, "stderr": result.stderr}


def normalized_index(binary, root):
    index = graf_index(binary, root)
    index["root"] = "<root>"
    return index


def without_index_bytes(result):
    """A result with `index_bytes` removed from build-report and stats JSON."""
    if result["code"] or result["args"][0] not in ("index", "stats"):
        return result, None
    body = json.loads(result["stdout"])
    stats = body.get("stats", body)
    size = stats.pop("index_bytes", None)
    return {**result, "stdout": json.dumps(body, sort_keys=True).encode()}, size


def requests(queries, repo):
    for case in (q for q in queries if q["repo"] == repo):
        for budget in BUDGETS:
            for command in ("search", "locate"):
                yield command, case["query"], "--budget", str(budget)
                yield command, case["query"], "--budget", str(budget), "--text"
        for kind in ("code", "docs", "test"):
            yield "search", case["query"], "--kind", kind
            yield "locate", case["query"], "--kind", kind, "--limit", "5"
    yield "search", "   "
    yield "search", "x", "--limit", "0"
    yield "locate", "x", "--limit", "1001"
    yield "map", "--limit", "1000"
    yield "map", "--offset", "3", "--limit", "7", "--kind", "docs"
    yield "stats",


def outputs(binary, root, queries, repo):
    """Run every request, then expand the IDs and handles the requests returned."""
    results = [run(binary, root, "index", "--rebuild")]
    ids = []
    for args in requests(queries, repo):
        result = run(binary, root, *args)
        results.append(result)
        if result["code"] == 0 and args[0] in ("search", "locate") and "--text" not in args:
            body = json.loads(result["stdout"])
            ids += [e["id"] for e in body.get("excerpts", body.get("hints", []))[:3]]
    for item in dict.fromkeys(ids):
        results.append(run(binary, root, "expand", item, "--before", "5", "--after", "30"))
        results.append(run(binary, root, "expand", item, "--budget", "40", "--text"))
    results.append(run(binary, root, "expand", "0" * 11))
    return results


def compare(old, new, corpora):
    queries = json.loads((BASE / "benchmark/queries.json").read_text())
    failures, checked, sizes = [], 0, {}
    with tempfile.TemporaryDirectory() as tmp:
        for repo in REPOS:
            roots = {}
            for arm in ("old", "new"):
                roots[arm] = Path(tmp) / arm / repo
                shutil.copytree(corpora / repo, roots[arm], ignore=shutil.ignore_patterns(".graf"))
            old_results = outputs(old, roots["old"], queries, repo)
            new_results = outputs(new, roots["new"], queries, repo)
            # Arm directory names have equal length, so index_bytes in build reports is comparable.
            for a, b in zip(old_results, new_results, strict=True):
                checked += 1
                (a, old_size), (b, new_size) = without_index_bytes(a), without_index_bytes(b)
                if old_size is not None:
                    sizes.setdefault(repo, {"old": old_size, "new": new_size})
                if a != b:
                    failures.append({"repo": repo, "args": a["args"]})
            if normalized_index(old, roots["old"]) != normalized_index(new, roots["new"]):
                failures.append({"repo": repo, "args": ["serialized index"]})
            checked += 1
            print(repo, "checked", len(old_results), "outputs", flush=True)
    print("index bytes", json.dumps(sizes), flush=True)
    return checked, failures


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("old", type=Path)
    parser.add_argument("new", type=Path)
    parser.add_argument("--corpora", type=Path, default=BASE / ".research/corpora")
    args = parser.parse_args()
    checked, failures = compare(args.old.resolve(), args.new.resolve(), args.corpora.resolve())
    for failure in failures[:20]:
        print("DIFFERS:", failure["repo"], *failure["args"], file=sys.stderr)
    print(f"{checked - len(failures)}/{checked} identical")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
