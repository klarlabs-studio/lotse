"""Validate the Rust CLI on frozen exploratory requests, including actual BPE budgets.

This integrity diagnostic always follows with the required paired Graphify run.
Its source-anchor workload alone is not a matched comparison: source filters and
token splitting differ. Timings include startup, index/tokenizer loading,
source validation, and serialization.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

BASE = Path(__file__).resolve().parents[1]
os.environ["TIKTOKEN_CACHE_DIR"] = str(BASE / ".research/tokenizer")
import tiktoken


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=BASE / "target/release/graf")
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    binary = args.binary.resolve()
    encoder = tiktoken.get_encoding("cl100k_base")
    queries = json.loads((BASE / "benchmark/queries.json").read_text())
    result = {
        "workload": "Rust CLI exploratory validation; not a matched language comparison",
        "timing_scope": "whole subprocess wall time, warm OS cache uncontrolled",
        "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "source_hashes": {
            str(p.relative_to(BASE)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [BASE / "Cargo.toml", BASE / "Cargo.lock", *sorted((BASE / "src").rglob("*.rs"))]
        },
        "platform": platform.platform(), "repositories": {}, "rows": [],
    }

    def invoke(root, *command):
        start = time.perf_counter()
        output = subprocess.check_output([str(binary), "--root", str(root), *map(str, command)], text=True)
        return json.loads(output), (time.perf_counter() - start) * 1000

    for repo in ("cobra", "flask", "fastify"):
        root = BASE / ".research/corpora" / repo
        # --rebuild explicitly ignores previous chunks; OS page caches are uncontrolled.
        built, build_ms = invoke(root, "index", "--rebuild")
        noop, noop_ms = invoke(root, "index")
        result["repositories"][repo] = {"fresh_rebuild": built, "build_ms": build_ms, "noop": noop, "noop_ms": noop_ms}
        for query in (q for q in queries if q["repo"] == repo):
            for budget in (2000, 8000):
                samples = []
                response = None
                for _ in range(args.repeats):
                    value, elapsed = invoke(root, "search", query["query"], "--budget", budget, "--limit", 1000)
                    if response is not None:
                        assert value == response, "Non-deterministic ranking/output"
                    response = value
                    samples.append(elapsed)
                exact = len(encoder.encode(response["context"], disallowed_special=()))
                assert exact == response["context_tokens"] <= budget
                sections = []
                for excerpt in response["excerpts"]:
                    source_path = root / excerpt["path"]
                    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == excerpt["source_hash"]
                    body = "\n".join(source_path.read_text().splitlines()[excerpt["start"] - 1:excerpt["end"]])
                    # Rust serde_json prints Unicode and standard JSON string escapes.
                    header = json.dumps(excerpt["path"], ensure_ascii=False, separators=(",", ":"))
                    sections.append(f'{header}:{excerpt["start"]}-{excerpt["end"]}\n{body}')
                assert "\n\n".join(sections) == response["context"]
                hit_count = sum(any(
                    e["path"] == anchor["path"] and e["start"] <= anchor["start"] and e["end"] >= anchor["end"]
                    for e in response["excerpts"]
                ) for anchor in query["evidence"])
                total = len(query["evidence"])
                result["rows"].append({
                    "id": query["id"], "repo": repo, "category": query["category"], "budget": budget,
                    "anchor_recall": hit_count / total if total else None,
                    "all_anchors": hit_count == total if total else None,
                    "latency_ms": samples, "median_ms": statistics.median(samples), "response": response,
                })
        print(f"{repo}: {built['stats']['files']} files, {built['stats']['chunks']} chunks; validated", flush=True)
    result["summary"] = {}
    for budget in (2000, 8000):
        rows = [r for r in result["rows"] if r["budget"] == budget]
        positive = [r for r in rows if r["anchor_recall"] is not None]
        result["summary"][str(budget)] = {
            "positive_queries": len(positive),
            "mean_anchor_recall": statistics.mean(r["anchor_recall"] for r in positive),
            "all_anchors": sum(r["all_anchors"] for r in positive),
            "median_subprocess_ms": statistics.median(r["median_ms"] for r in rows),
            "negative_queries_returning_context": sum(bool(r["response"]["context"]) for r in rows if r["anchor_recall"] is None),
        }
    output = BASE / "results/rust-pilot.json"
    result["graphify_comparison"] = {"status": "pending"}
    output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["summary"], indent=2), flush=True)
    subprocess.run([sys.executable, str(BASE / "benchmark/compare_graphify.py"),
                    "--binary", str(binary), "--repeats", str(args.repeats)], check=True)
    subprocess.run([sys.executable, str(BASE / "benchmark/check_comparison.py")], check=True)
    comparison = BASE / "results/graphify-comparison.json"
    result["graphify_comparison"] = {"status": "complete", "path": str(comparison.relative_to(BASE)),
                                     "sha256": hashlib.sha256(comparison.read_bytes()).hexdigest()}
    output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
