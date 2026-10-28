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

from retrieval import graf_artifact, graf_index, graphify_baseline

BASE = Path(__file__).resolve().parents[1]
GRAPHIFY_ADAPTERS = ("library-v1", "native-cli-v2")
# graf arms: (index flags, query subcommand and flags). The first two are the
# accepted behavior; the rest are preregistered experiments
# (research/next-experiments.md), never accepted improvements until
# benchmark/decision.py passes on held-out queries.
GRAF_ARMS = {
    "graf": ((), ("search",)),
    "graf_locate": ((), ("locate",)),
    "graf_gap": ((), ("search", "--stop", "gap")),
    "graf_floor": ((), ("search", "--stop", "floor")),
    "graf_locate_gap": ((), ("locate", "--stop", "gap")),
    "graf_locate_floor": ((), ("locate", "--stop", "floor")),
    "graf_locate_damped": ((), ("locate", "--file-score", "damped")),
    "graf_locate_english": (("--stopwords", "english"), ("locate",)),
    "graf_locate_nostop": (("--stopwords", "none"), ("locate",)),
    "graf_locate_fullid": (("--stopwords", "english", "--full-identifiers"), ("locate",)),
    "graf_locate_path3": (("--path-weight", "3"), ("locate",)),
    "graf_locate_damped_gap": ((), ("locate", "--file-score", "damped", "--stop", "gap")),
    "graf_locate_damped_floor": ((), ("locate", "--file-score", "damped", "--stop", "floor")),
    "graf_units": ((), ("units",)),
    "graf_units_focus": ((), ("units", "--focus")),
    "graf_units_symbols": ((), ("units", "--focus", "--symbols")),
    "graf_units_retain_body": ((), ("units", "--focus", "--symbols", "--retain-body")),
    "graf_units_diverse": ((), ("units", "--focus", "--symbols", "--retain-body", "--file-diversity")),
    "graf_units_linked": ((), ("units", "--focus", "--symbols", "--retain-body", "--file-diversity", "--linked")),
    "graf_units_tail": ((), ("units", "--focus", "--symbols", "--retain-body", "--file-diversity", "--linked-tail")),
    "graf_evidence": ((), ("evidence",)),
    "graf_evidence_units": ((), ("evidence-units",)),
    "graf_evidence_linked": ((), ("evidence-linked",)),
    "graf_evidence_bundles": ((), ("evidence-bundles",)),
    "graf_evidence_coverage": ((), ("evidence-coverage",)),
    "graf_evidence_families": ((), ("evidence-families",)),
    "graf_evidence_passages": ((), ("evidence-passages",)),
    "graf_evidence_complement": ((), ("evidence-complement",)),
    "graf_evidence_stratified": ((), ("evidence-stratified",)),
    "graf_evidence_fused": ((), ("evidence-fused",)),
    "graf_evidence_referenced": ((), ("evidence-referenced",)),
    "graf_evidence_rescue": ((), ("evidence-rescue",)),
    "graf_evidence_callees": ((), ("evidence-callees",)),
    "graf_evidence_selective": ((), ("evidence-selective",)),
}
EXPERIMENTAL_ARMS = tuple(GRAF_ARMS)[2:]
ARMS = (*GRAF_ARMS, "graphify")
INDEX_VARIANTS = tuple(dict.fromkeys(flags for flags, _ in GRAF_ARMS.values()))


def describe(arm):
    flags, query = GRAF_ARMS[arm]
    return " ".join(query) + (f" (index {' '.join(flags)})" if flags else "")


def graf_query_command(binary, root, arm, query, budget):
    _, (command, *flags) = GRAF_ARMS[arm]
    limit = [] if command == "units" else ["--limit", "20" if arm in ("graf_evidence", "graf_evidence_units", "graf_evidence_linked", "graf_evidence_bundles", "graf_evidence_coverage", "graf_evidence_families", "graf_evidence_passages", "graf_evidence_complement", "graf_evidence_stratified", "graf_evidence_fused", "graf_evidence_referenced", "graf_evidence_rescue", "graf_evidence_callees", "graf_evidence_selective") else "1000"]
    return [str(binary), "--root", str(root), command, query, *flags,
            "--budget", str(budget), *limit, "--text"]


def variant_name(flags):
    """Build-record key: "graf" for the accepted index, else graf plus its flags."""
    return "graf" + "".join("-" + flag.strip("-") for flag in flags)


def variant_root(repo_scratch, flags):
    return repo_scratch / ("input" if not flags else "input-" + variant_name(flags)[5:])


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def cited_path_order(tool, context, root, files):
    if tool == "graf_units":
        # The historical unit renderer uses unquoted paths. Restrict headers to
        # actual staged paths; the focus arm uses normal JSON-quoted citations.
        hints = re.findall(r"^(.*):\d+-\d+$", context, re.MULTILINE)
    elif tool.startswith("graf"):
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


def validate_focused_units(context, root, physical=False):
    """Authenticate every delivered line against its exact quoted source range."""
    headers = list(re.finditer(r'^("(?:[^"\\]|\\.)*"):(\d+)-(\d+)\n', context, re.MULTILINE))
    if not context:
        return 0
    assert headers and headers[0].start() == 0
    for i, header in enumerate(headers):
        path, start, end = json.loads(header[1]), int(header[2]), int(header[3])
        source = (root / path).read_text()
        if physical:
            source = (root / path).read_bytes().decode('utf-8')
            rows = source.split('\n')
            lines = [line.removesuffix('\r') if j < len(rows) - 1 else line for j, line in enumerate(rows)]
            if source.endswith('\n'): lines.pop()
        # Rust ranges count physical rows, unlike legacy Python splitlines.
        else:
            lines = ([line.removesuffix('\r') for line in source.split('\n')]
                 if path.endswith('.rs') else source.splitlines())
        assert 1 <= start <= end <= len(lines)
        stop = headers[i + 1].start() if i + 1 < len(headers) else len(context)
        assert context[header.end():stop] == '\n'.join(lines[start - 1:end]) + '\n\n'
    return len(headers)


def graphify_query(graph_path, query, budget):
    from graphify.serve import _query_graph_text, _read_graph
    context = _query_graph_text(_read_graph(str(graph_path)).to_undirected(), query, token_budget=budget)
    sys.stdout.write(context)  # Native text unchanged, with no wrapper metadata.


def graphify_command(graph_path, query, budget, adapter="library-v1"):
    """Keep the historical invocation explicit; v2 executes the pinned CLI itself."""
    if adapter not in GRAPHIFY_ADAPTERS:
        raise ValueError(f"Unknown Graphify adapter: {adapter}")
    if adapter == "native-cli-v2":
        return [sys.executable, "-m", "graphify", "query", query,
                "--graph", str(graph_path), "--budget", str(budget)]
    return [sys.executable, str(Path(__file__).resolve()), "--graphify-query",
            str(graph_path), query, str(budget)]


def validate_adapter_freeze(prereg, adapter):
    if adapter == "native-cli-v2":
        expected = {"id": adapter, "driver_sha256": sha(Path(__file__)),
                    "manifest_sha256": sha(BASE / "benchmark/graphify-adapters.json")}
        if prereg.get("graphify_adapter") != expected:
            raise ValueError("native-cli-v2 requires a new preregistration freezing its ID, "
                             "driver_sha256 and manifest_sha256 before comparative runs")


def main():
    from retrieval import load_documents, tokens
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, default=BASE / "target/release/graf")
    parser.add_argument("--queries", type=Path, default=BASE / "benchmark/queries.json")
    parser.add_argument("--output", type=Path, default=BASE / "results/graphify-comparison.json")
    parser.add_argument("--graphify-adapter", choices=GRAPHIFY_ADAPTERS, default="library-v1")
    parser.add_argument("--run-plan", type=Path,
                        help="Authenticated workload/arm/binary plan frozen in preregistration")
    prereg = json.loads((BASE / "benchmark/preregistration.json").read_text())
    parser.add_argument("--repeats", type=int, default=prereg["latency"]["invocations_per_case"])
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    validate_adapter_freeze(prereg, args.graphify_adapter)
    plan, inputs = None, None
    arms, graf_arms, variants = ARMS, GRAF_ARMS, INDEX_VARIANTS
    if args.run_plan:
        from comparison_plan import load_plan, selected_layout
        if any(a.split("=", 1)[0] in {"--binary", "--queries", "--repeats"} for a in sys.argv[1:]):
            parser.error("Binary, queries and repeats are defined by --run-plan; do not override them")
        plan, inputs = load_plan(args.run_plan.resolve(), prereg, BASE, GRAF_ARMS)
        if args.graphify_adapter != plan["graphify_adapter"]:
            parser.error("Graphify adapter differs from frozen run plan")
        args.binary = BASE / plan["index_binary"]["path"]
        args.queries = BASE / plan["queries"]["path"]
        args.repeats = plan["repeats"]
        planned_output = BASE / plan["output"]
        if any(a.split("=", 1)[0] == "--output" for a in sys.argv[1:]) and args.output.resolve() != planned_output.resolve():
            parser.error("Output differs from frozen run plan")
        args.output = planned_output
        if args.output.exists():
            parser.error("Refusing to overwrite a frozen-plan comparison")
        arms = tuple(plan["arms"])
        graf_arms, variants = selected_layout(arms, GRAF_ARMS)
    binary = args.binary.resolve()
    env = {**os.environ, "PYTHONHASHSEED": "0"}
    manifest = inputs["corpora"] if inputs else json.loads((BASE / "benchmark/corpora.json").read_text())
    # The interpreter's Graphify must match one pinned revision source for source (each has its own virtualenv).
    pin = graphify_baseline(manifest=manifest)
    installed = importlib.metadata.version("graphifyy")
    snapshots = inputs["snapshots"] if inputs else json.loads((BASE / "results/snapshots.json").read_text())
    queries, output_path = args.queries.resolve(), args.output.resolve()
    cases = json.loads(queries.read_text())
    # The development run keeps its historical scratch path; other workloads get their own.
    default_output = output_path == BASE / "results/graphify-comparison.json"
    scratch = BASE / ".research" / ("comparison" if default_output else f"comparison-{output_path.stem}")
    scratch.mkdir(parents=True, exist_ok=True)
    result = {
        "baseline": {"pin": pin, "installed_version": installed, "mode": "code_only_no_clustering",
                     "adapter": args.graphify_adapter,
                     "python_sources": {str(p.relative_to(graphify_root)): sha(p) for p in sorted(graphify_root.rglob("*.py"))}},
        "arms": {"graf": "Full source excerpts", "graf_locate": "One compact source hint per file with expansion ID",
                 **{arm: "Experimental: " + describe(arm) for arm in EXPERIMENTAL_ARMS},
                 "graphify": "Native code-only navigation hints"},
        "repeats": args.repeats, "preregistration_sha256": sha(BASE / "benchmark/preregistration.json"),
        "input_receipts": snapshots,
        "driver_sha256": sha(Path(__file__)), "query_sha256": sha(queries),
        "queries": str(queries.relative_to(BASE)), "scratch": str(scratch.relative_to(BASE)),
        "binary_sha256": sha(binary),
        "source_hashes": {str(p.relative_to(BASE)): sha(p) for p in
                          [BASE / "Cargo.toml", BASE / "Cargo.lock", *sorted((BASE / "src").rglob("*.rs")), BASE / "benchmark/retrieval.py", BASE / "benchmark/locate-experiment.json"]},
        "subprocess_environment": {"PYTHONHASHSEED": "0"},
        "timing_scope": "fresh subprocess per query: startup, imports, index load, query, rendering, stdout; OS caches uncontrolled",
        "quality_scope": "shared code-file discovery only; graf full excerpts and compact locate vs native Graphify hints; Graphify code-only does not index docs",
        "unmeasured": ["agent task success and total tokens/cost", "docs-enabled Graphify", "peak memory", "incremental update cost"],
        "repositories": {}, "rows": [],
    }
    if plan:
        result["arms"] = {a: result["arms"][a] for a in arms}
        result["run_plan"] = {"path": str(args.run_plan.resolve().relative_to(BASE)),
                              "sha256": sha(args.run_plan)}
        result["binary_path"] = plan["index_binary"]["path"]
        result["arm_binaries"] = plan["binaries"]
        result["source_hashes"]["benchmark/comparison_plan.py"] = sha(BASE / "benchmark/comparison_plan.py")
    repositories = ([r["id"] for r in manifest["repositories"] if r.get("role") != "baseline"]
                    if plan else ("cobra", "flask", "fastify"))
    if {c["repo"] for c in cases} != set(repositories):
        raise ValueError("Query repositories do not match comparison corpus")
    for repo in repositories:
        files = load_documents(BASE / ".research/corpora" / repo)
        if inputs:
            from comparison_plan import verify_sources
            verify_sources(inputs, repo, files)
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
            artifact = graf_artifact(staged) if tool == "graf" else output / "graphify-out/graph.json"
            builds[tool] = {"elapsed_ms": elapsed, "index_bytes": artifact.stat().st_size,
                            "artifact_sha256": sha(artifact), "command": command}
        if set(graf_index(binary, staged)["files"]) != set(files):
            raise RuntimeError(f"{repo}: staged inputs differ from graf index; fix filters before comparing")
        # Experimental analyzers index identical copies of the staged input.
        for flags in variants[1:]:
            root = variant_root(scratch / repo, flags)
            if root.exists():
                shutil.rmtree(root)
            shutil.copytree(staged, root, ignore=shutil.ignore_patterns(".graf"))
            command = [str(binary), "--root", str(root), "index", "--rebuild", *flags]
            start = time.perf_counter()
            subprocess.run(command, capture_output=True, text=True, timeout=180, check=True, env=env)
            elapsed = (time.perf_counter() - start) * 1000
            artifact = graf_artifact(root)
            if set(graf_index(binary, root)["files"]) != set(files):
                raise RuntimeError(f"{repo}: {flags} index differs from staged inputs")
            builds[variant_name(flags)] = {"elapsed_ms": elapsed, "index_bytes": artifact.stat().st_size,
                                           "artifact_sha256": sha(artifact), "command": command}
        result["repositories"][repo] = {"files": len(files), "file_hashes": {p: hashlib.sha256(t.encode()).hexdigest() for p,t in files.items()}, "builds": builds}
        print(f"{repo}: source-set preflight and fresh builds complete", flush=True)
    # Validate/build every source set before spending time on repeated queries.
    for repo, repository in result["repositories"].items():
        staged = scratch / repo / "input"
        output = scratch / repo / "graph"
        files = repository["file_hashes"]
        roots = {flags: variant_root(scratch / repo, flags) for flags in variants}
        graf_indexes = {flags: graf_index(binary, root) for flags, root in roots.items()}
        for case in (c for c in cases if c["repo"] == repo):
            for budget in (2000, 8000):
                commands = {
                    **{arm: graf_query_command(BASE / plan["binaries"][arm]["path"] if plan else binary,
                                              roots[index_flags], arm, case["query"], budget)
                       for arm, (index_flags, _) in graf_arms.items()},
                    "graphify": graphify_command(output / "graphify-out/graph.json", case["query"], budget, args.graphify_adapter),
                }
                measurements = {tool: {"latency_ms": [], "context": None, "command": cmd, "runs": []} for tool,cmd in commands.items()}
                for repeat in range(args.repeats):
                    # Rotate order so arms occupy different positions across repetitions.
                    offset = repeat % len(arms)
                    for tool in arms[offset:] + arms[:offset]:
                        start = time.perf_counter()
                        process = subprocess.run(commands[tool], capture_output=True, text=True, timeout=90, env=env)
                        elapsed = (time.perf_counter() - start) * 1000
                        context = process.stdout
                        measurements[tool]["runs"].append({"repeat": repeat, "returncode": process.returncode,
                                                          "stdout": context, "stderr": process.stderr, "elapsed_ms": elapsed})
                        # The native CLI defines exit 4 for an empty search result.
                        no_match = (tool == "graphify" and args.graphify_adapter == "native-cli-v2"
                                    and process.returncode == 4 and context == "No matching nodes found.\n")
                        if process.returncode and not no_match:
                            raise RuntimeError(f"{tool} failed: {process.returncode}: {process.stderr}")
                        measurements[tool]["latency_ms"].append(elapsed)
                        prior = measurements[tool]["context"]
                        if prior is not None and prior != context:
                            raise RuntimeError(f"Non-deterministic {tool} output")
                        measurements[tool]["context"] = context
                for tool, value in measurements.items():
                    context = value["context"]
                    paths = cited_paths(tool, context, staged, files)
                    ordered = cited_path_order(tool, context, staged, files)
                    if tool in ("graf_units_focus", "graf_units_symbols", "graf_units_retain_body", "graf_units_diverse", "graf_units_linked", "graf_units_tail"):
                        value["validated_source_units"] = validate_focused_units(context, staged)
                    if tool in ("graf_evidence", "graf_evidence_units", "graf_evidence_linked", "graf_evidence_bundles", "graf_evidence_coverage", "graf_evidence_families", "graf_evidence_passages", "graf_evidence_complement", "graf_evidence_stratified", "graf_evidence_fused", "graf_evidence_referenced", "graf_evidence_rescue", "graf_evidence_callees", "graf_evidence_selective"):
                        value["validated_source_units"] = validate_focused_units(context, staged, physical=True)
                    if tool.startswith("graf_locate"):
                        flags = GRAF_ARMS[tool][0]
                        value["validated_hint_count"] = validate_locate(context, roots[flags], graf_indexes[flags])
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
        for tool in arms:
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
    dest = output_path
    dest.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result["summary"], indent=2))


if __name__ == "__main__":
    if len(sys.argv) == 5 and sys.argv[1] == "--graphify-query":
        # This branch imports only Graphify's serving stack in a fresh process.
        graphify_query(Path(sys.argv[2]), sys.argv[3], int(sys.argv[4]))
    else:
        main()
