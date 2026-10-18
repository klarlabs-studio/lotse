"""Run pinned Graphify's native code-only extraction and resident query API.

This is a navigation baseline, not a docs-enabled Graphify evaluation.
No assistant hooks are installed and no semantic API is called.
"""
import json
import hashlib
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import time

from retrieval import BASE, load_documents, tokens


def main():
    from graphify.serve import _query_graph_text, _read_graph
    cases = json.loads((BASE / "benchmark/queries.json").read_text())
    records = []
    for repo in ["cobra", "flask", "fastify"]:
        files = load_documents(BASE / ".research/corpora" / repo)
        staged = BASE / ".research/graphify-input" / repo
        staged.mkdir(parents=True, exist_ok=True)
        for rel, text in files.items():
            dest = staged / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text)
        out = BASE / ".research/graphify-output" / repo
        if out.exists():
            shutil.rmtree(out)  # Task-owned scratch output: force a genuinely fresh graph build.
        command = [str(BASE / ".research/venv/bin/graphify"), "extract", str(staged), "--code-only", "--no-cluster", "--max-workers", "2", "--out", str(out)]
        start = time.perf_counter()
        completed = subprocess.run(command, capture_output=True, text=True, timeout=180)
        build_s = time.perf_counter() - start
        logdir = BASE / "results/graphify-logs"
        logdir.mkdir(parents=True, exist_ok=True)
        (logdir / f"{repo}-extract.txt").write_text(completed.stdout + completed.stderr)
        if completed.returncode:
            raise RuntimeError(f"Graphify extraction failed; see {logdir}")
        graph_path = out / "graphify-out/graph.json"
        start = time.perf_counter()
        graph = _read_graph(str(graph_path)).to_undirected()
        load_s = time.perf_counter() - start
        rows = []
        for case in [q for q in cases if q["repo"] == repo]:
            for budget in [2000, 8000]:
                latencies = []
                for _ in range(3):
                    start = time.perf_counter()
                    context = _query_graph_text(graph, case["query"], token_budget=budget)
                    latencies.append((time.perf_counter() - start) * 1000)
                sources = re.findall(r"\[src=(.*?) loc=(.*?) community=", context)
                valid = []
                invalid = []
                unlocated = 0
                for path, loc in sources:
                    path = path.removeprefix(str(staged) + "/")
                    match = re.match(r"L(\d+)", loc)
                    if path in files and match and 1 <= int(match.group(1)) <= len(files[path].splitlines()):
                        valid.append(path)
                    elif not path and not loc:
                        unlocated += 1  # External/unresolved symbols are not broken local citations.
                    else:
                        invalid.append(dict(path=path, location=loc))
                code_paths = {e["path"] for e in case["evidence"] if Path(e["path"]).suffix not in {".md", ".rst", ".txt", ".mdx"}}
                rows.append(dict(query_id=case["id"], budget=budget,
                                 resident_latency_ms=statistics.median(latencies),
                                 latency_samples_ms=latencies,
                                 output_tokens=len(tokens(context)), over_cl100k_budget=len(tokens(context)) > budget,
                                 cited_paths=sorted(set(valid)), invalid_source_locations=invalid,
                                 external_or_unlocated_nodes=unlocated,
                                 code_file_hint_recall=len(code_paths & set(valid)) / len(code_paths) if code_paths else None))
                (logdir / f"{case['id']}-{budget}.txt").write_text(context)
        records.append(dict(repo=repo, build_s=build_s, load_s=load_s,
                            graph_bytes=graph_path.stat().st_size, nodes=len(graph), edges=graph.number_of_edges(),
                            extraction_command=command, mode="code_only_no_clustering", queries=rows))
        print(repo, len(graph), "nodes", flush=True)
    (BASE / "results/graphify.json").write_text(json.dumps(records, indent=2) + "\n")
    (BASE / "results/graphify-provenance.json").write_text(json.dumps(dict(
        source_commit=json.loads((BASE / "benchmark/corpora.json").read_text())["repositories"][-1]["commit"],
        query_sha256=hashlib.sha256((BASE / "benchmark/queries.json").read_bytes()).hexdigest(),
        driver_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()), indent=2) + "\n")


if __name__ == "__main__":
    main()
