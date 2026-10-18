"""Paired local CLI comparison against pinned, code-only Graphify.

Both tools receive exactly the same staged inputs and query/budget pairs.
Graphify emits native navigation text; graf emits source excerpts (--text).
Shared quality metric: code-file hint recall, NOT answer/source-anchor accuracy.
"""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import time
import tomllib

BASE = Path(__file__).resolve().parents[1]
# graf arms: subcommand and flags. The first two are the accepted behavior; the
# rest are preregistered experiments (research/next-experiments.md), never
# accepted improvements until benchmark/decision.py passes on held-out queries.
GRAF_ARMS = {
    "graf": ("search",),
    "graf_locate": ("locate",),
    "graf_gap": ("search", "--stop", "gap"),
    "graf_floor": ("search", "--stop", "floor"),
    "graf_locate_gap": ("locate", "--stop", "gap"),
    "graf_locate_floor": ("locate", "--stop", "floor"),
    "graf_locate_damped": ("locate", "--file-score", "damped"),
}
EXPERIMENTAL_ARMS = tuple(GRAF_ARMS)[2:]
ARMS = (*GRAF_ARMS, "graphify")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cited_path_order(tool, context, root, files):
    if tool.startswith("graf"):
        hints = [json.loads(m.group(1)) for m in re.finditer(
            r'^("(?:[^"\\]|\\.)*"):\d+-\d+$', context, re.MULTILINE)]
    else:
        hints = [m.group(1).removeprefix(str(root) + "/") for m in re.finditer(
            r"\[src=(.*?) loc=.*? community=", context)]
    return list(dict.fromkeys(path for path in hints if path in files))


def cited_paths(tool, context, root, files):
    return sorted(cited_path_order(tool, context, root, files))


def validate_locate(context, root, index):
    """Verify text handles and preview prefixes independently of the Rust renderer."""
    seen = set()
    ids = [c["id"] for source in index["files"].values() for c in source["chunks"]]
    for section in context.split("\n\n") if context else []:
        header, handle, preview = section.splitlines()
        match = re.fullmatch(r'("(?:[^"\\]|\\.)*"):(\d+)-(\d+)', header)
        if not match:
            raise RuntimeError("Invalid locate citation header")
        path, start, end = json.loads(match[1]), int(match[2]), int(match[3])
        assert path not in seen, "Locate repeated a file"
        seen.add(path)
        assert re.fullmatch(r"id=[0-9a-f]{12,64}", handle), "Invalid expansion handle"
        prefix = handle.removeprefix("id=")
        assert sum(i.startswith(prefix) for i in ids) == 1, "Ambiguous or unknown expansion handle"
        anchor = next(c for c in index["files"][path]["chunks"] if c["id"].startswith(prefix))
        assert (start, end) == (anchor["start"], anchor["end"])
        lines = (root / path).read_text().splitlines()
        line, quoted = preview.split(": ", 1)
        line = int(line.removeprefix("L"))
        assert start <= line <= end
        value, consumed = json.JSONDecoder().raw_decode(quoted)
        source = lines[line - 1].strip()
        assert value == source[:160]
        assert quoted[consumed:] == (" [truncated]" if len(source) > 160 else "")
        hashed = hashlib.sha256((root / path).read_bytes()).hexdigest()
        assert hashed == index["files"][path]["hash"]
        full = hashlib.sha256(f"{path}\0{hashed}\0{start}\0{end}".encode()).hexdigest()
        assert full == anchor["id"] and full.startswith(prefix)
        if len(prefix) < 64:
            required = next(n for n in range(12, 65)
                            if sum(i.startswith(full[:n]) for i in ids) == 1)
            assert len(prefix) == required, "Not the shortest unique handle"
    return len(seen)


def code_hint_recall(case, paths):
    expected = {e["path"] for e in case["evidence"]
                if Path(e["path"]).suffix not in {".md", ".mdx", ".rst", ".txt"}}
    return len(expected & set(paths)) / len(expected) if expected else None


def graphify_query(graph_path, query, budget):
    from graphify.serve import _query_graph_text, _read_graph
    context = _query_graph_text(_read_graph(str(graph_path)).to_undirected(), query, token_budget=budget)
    sys.stdout.write(context)  # Native text unchanged, with no wrapper metadata.


def main():
    from retrieval import load_documents, tokens
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=BASE / "target/release/graf")
    prereg = json.loads((BASE / "benchmark/preregistration.json").read_text())
    parser.add_argument("--repeats", type=int, default=prereg["latency"]["invocations_per_case"])
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    binary = args.binary.resolve()
    env = {**os.environ, "PYTHONHASHSEED": "0"}
    manifest = json.loads((BASE / "benchmark/corpora.json").read_text())
    pin = next(r for r in manifest["repositories"] if r["id"] == "graphify")
    pinned_root = BASE / ".research/corpora/graphify"
    package = tomllib.loads((pinned_root / "pyproject.toml").read_text())["project"]
    installed = importlib.metadata.version(package["name"])
    expected_version = package["version"]
    if installed != expected_version:
        raise RuntimeError("Installed Graphify differs from pinned package version; reinstall the pinned source")
    # Check installed Python sources against the frozen baseline, not just its version.
    import graphify
    graphify_root = Path(graphify.__file__).parent
    for path in graphify_root.rglob("*.py"):
        reference = pinned_root / "graphify" / path.relative_to(graphify_root)
        if not reference.is_file() or sha(path) != sha(reference):
            raise RuntimeError(f"Graphify source differs from pinned snapshot: {path.name}")
    snapshots = json.loads((BASE / "results/snapshots.json").read_text())
    cases = json.loads((BASE / "benchmark/queries.json").read_text())
    scratch = BASE / ".research/comparison"
    scratch.mkdir(parents=True, exist_ok=True)
    result = {
        "baseline": {"pin": pin, "installed_version": installed, "mode": "code_only_no_clustering",
                     "python_sources": {str(p.relative_to(graphify_root)): sha(p) for p in sorted(graphify_root.rglob("*.py"))}},
        "arms": {"graf": "Full source excerpts", "graf_locate": "One compact source hint per file with expansion ID",
                 **{arm: "Experimental: " + " ".join(GRAF_ARMS[arm]) for arm in EXPERIMENTAL_ARMS},
                 "graphify": "Native code-only navigation hints"},
        "repeats": args.repeats, "preregistration_sha256": sha(BASE / "benchmark/preregistration.json"),
        "input_receipts": snapshots,
        "driver_sha256": sha(Path(__file__)), "query_sha256": sha(BASE / "benchmark/queries.json"),
        "binary_sha256": sha(binary),
        "source_hashes": {str(p.relative_to(BASE)): sha(p) for p in
                          [BASE / "Cargo.toml", BASE / "Cargo.lock", *sorted((BASE / "src").rglob("*.rs")), BASE / "benchmark/retrieval.py", BASE / "benchmark/locate-experiment.json"]},
        "subprocess_environment": {"PYTHONHASHSEED": "0"},
        "timing_scope": "fresh subprocess per query: startup, imports, index load, query, rendering, stdout; OS caches uncontrolled",
        "quality_scope": "shared code-file discovery only; graf full excerpts and compact locate vs native Graphify hints; Graphify code-only does not index docs",
        "unmeasured": ["agent task success and total tokens/cost", "docs-enabled Graphify", "peak memory", "incremental update cost"],
        "repositories": {}, "rows": [],
    }
    for repo in ("cobra", "flask", "fastify"):
        files = load_documents(BASE / ".research/corpora" / repo)
        staged = scratch / repo / "input"
        output = scratch / repo / "graph"
        logs = scratch / repo / "logs"
        # Own scratch trees only. Recreate inputs too, so deleted files cannot linger.
        for path in (staged, output, logs):
            if path.exists():
                shutil.rmtree(path)
            path.mkdir(parents=True)
        for rel, text in files.items():
            dest = staged / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text)
        builds = {}
        build_commands = {
            "graf": [str(binary), "--root", str(staged), "index", "--rebuild"],
            "graphify": [str(Path(sys.executable).parent / "graphify"), "extract", str(staged),
                         "--code-only", "--no-cluster", "--max-workers", "2", "--out", str(output)],
        }
        for tool, command in build_commands.items():
            start = time.perf_counter()
            process = subprocess.run(command, capture_output=True, text=True, timeout=180, check=True, env=env)
            elapsed = (time.perf_counter() - start) * 1000
            (logs / f"{tool}-build.txt").write_text(process.stdout + process.stderr)
            artifact = staged / ".graf/index.json" if tool == "graf" else output / "graphify-out/graph.json"
            builds[tool] = {"elapsed_ms": elapsed, "index_bytes": artifact.stat().st_size,
                            "artifact_sha256": sha(artifact), "command": command}
        graf_index = json.loads((staged / ".graf/index.json").read_text())
        if set(graf_index["files"]) != set(files):
            raise RuntimeError(f"{repo}: staged inputs differ from graf index; fix filters before comparing")
        result["repositories"][repo] = {"files": len(files), "file_hashes": {p: hashlib.sha256(t.encode()).hexdigest() for p,t in files.items()}, "builds": builds}
        print(f"{repo}: source-set preflight and fresh builds complete", flush=True)
    # Validate/build every source set before spending time on repeated queries.
    for repo, repository in result["repositories"].items():
        staged = scratch / repo / "input"
        output = scratch / repo / "graph"
        files = repository["file_hashes"]
        graf_index = json.loads((staged / ".graf/index.json").read_text())
        for case in (c for c in cases if c["repo"] == repo):
            for budget in (2000, 8000):
                commands = {
                    **{arm: [str(binary), "--root", str(staged), command, case["query"], *flags,
                             "--budget", str(budget), "--limit", "1000", "--text"]
                       for arm, (command, *flags) in GRAF_ARMS.items()},
                    "graphify": [sys.executable, str(Path(__file__).resolve()), "--graphify-query", str(output / "graphify-out/graph.json"), case["query"], str(budget)],
                }
                measurements = {tool: {"latency_ms": [], "context": None, "command": cmd} for tool,cmd in commands.items()}
                for repeat in range(args.repeats):
                    # Rotate order so arms occupy different positions across repetitions.
                    offset = repeat % len(ARMS)
                    for tool in ARMS[offset:] + ARMS[:offset]:
                        start = time.perf_counter()
                        context = subprocess.check_output(commands[tool], text=True, timeout=90, env=env)
                        measurements[tool]["latency_ms"].append((time.perf_counter() - start) * 1000)
                        prior = measurements[tool]["context"]
                        if prior is not None and prior != context:
                            raise RuntimeError(f"Non-deterministic {tool} output")
                        measurements[tool]["context"] = context
                for tool, value in measurements.items():
                    context = value["context"]
                    paths = cited_paths(tool, context, staged, files)
                    ordered = cited_path_order(tool, context, staged, files)
                    if tool.startswith("graf_locate"):
                        value["validated_hint_count"] = validate_locate(context, staged, graf_index)
                    value.update(cited_path_order=ordered,
                                 code_file_hint_recall_at_5=code_hint_recall(case, ordered[:5]),
                                 code_file_hint_recall_at_10=code_hint_recall(case, ordered[:10]),
                                 cited_file_count=len(paths))
                    value.update(median_ms=statistics.median(value["latency_ms"]),
                                 output_tokens=len(tokens(context)),
                                 over_cl100k_budget=len(tokens(context)) > budget,
                                 cited_paths=paths, code_file_hint_recall=code_hint_recall(case, paths))
                    if tool.startswith("graf") and value["over_cl100k_budget"]:
                        raise RuntimeError("graf exceeded its exact text budget")
                result["rows"].append({"id": case["id"], "repo": repo, "category": case["category"],
                                       "budget": budget, "tools": measurements})
        print(f"{repo}: paired Graphify/graf comparison complete", flush=True)
    result["summary"] = {}
    for budget in (2000, 8000):
        result["summary"][str(budget)] = {}
        for tool in ARMS:
            values = [r["tools"][tool] for r in result["rows"] if r["budget"] == budget]
            recall = [v["code_file_hint_recall"] for v in values if v["code_file_hint_recall"] is not None]
            result["summary"][str(budget)][tool] = {
                "code_file_hint_recall": statistics.mean(recall), "scored_queries": len(recall),
                "code_file_hint_recall_at_5": statistics.mean(v["code_file_hint_recall_at_5"] for v in values if v["code_file_hint_recall_at_5"] is not None),
                "code_file_hint_recall_at_10": statistics.mean(v["code_file_hint_recall_at_10"] for v in values if v["code_file_hint_recall_at_10"] is not None),
                "median_cited_files": statistics.median(v["cited_file_count"] for v in values),
                "median_query_ms": statistics.median(v["median_ms"] for v in values),
                "median_output_tokens": statistics.median(v["output_tokens"] for v in values),
                "over_cl100k_budget": sum(v["over_cl100k_budget"] for v in values),
            }
    dest = BASE / "results/graphify-comparison.json"
    dest.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--graphify-query":
        # This branch imports only Graphify's serving stack in a fresh process.
        graphify_query(Path(sys.argv[2]), sys.argv[3], int(sys.argv[4]))
    else:
        main()
