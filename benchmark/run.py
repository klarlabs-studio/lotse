"""Run the reproducible retrieval pilot; never execute an LLM coding agent."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import resource
import statistics
import subprocess
import sys
import time

from retrieval import BASE, Index, load_documents, pack, score_evidence, tokens
from prepare_models import MODEL

METHODS = ["grep", "bm25", "hierarchy", "semantic", "graph", "hybrid"]


def validate(cases, files):
    for case in cases:
        for e in case["evidence"]:
            content = files[e["path"]]
            assert hashlib.sha256(content.encode()).hexdigest() == e["file_sha256"]
            assert e["anchor"] in content.splitlines()[e["start"] - 1]


def run_repo(repo, repeats):
    from fastembed import TextEmbedding
    root = BASE / ".research/corpora" / repo
    cases = [q for q in json.loads((BASE / "benchmark/queries.json").read_text()) if q["repo"] == repo]
    start = time.perf_counter()
    files = load_documents(root)
    validate(cases, files)
    scan_s = time.perf_counter() - start
    start = time.perf_counter()
    import onnxruntime
    onnxruntime.disable_telemetry_events()
    model_path = BASE / ".research/cpu-model"
    model = TextEmbedding(model_name=MODEL, specific_model_path=str(model_path), threads=4, local_files_only=True)
    model.model.tokenizer.enable_truncation(max_length=128)
    model_load_s = time.perf_counter() - start
    fingerprint = hashlib.sha256((BASE / "results/model-artifacts.json").read_bytes() + b"max_length=128").hexdigest()[:16]
    cache = BASE / ".research/vectors-128" / fingerprint / repo
    print(repo, len(files), "files; building indexes", flush=True)
    start = time.perf_counter()
    index = Index(files, cache, model)
    index_s = time.perf_counter() - start
    # Serializations are diagnostic component sizes, not standalone engines.
    out = BASE / ".research/indexes" / repo
    out.mkdir(parents=True, exist_ok=True)
    (out / "chunks.json").write_text(json.dumps(index.chunks))
    (out / "projections.json").write_text(json.dumps(index.projections))
    (out / "edges.json").write_text(json.dumps({p: sorted(v) for p, v in index.edges.items()}))
    results = []
    for case in cases:
        for budget in [2000, 8000]:
            for method in METHODS:
                times = []
                for _ in range(repeats):
                    start = time.perf_counter()
                    order = index.rank(case["query"], method)
                    selected, context = pack(index.chunks, order, budget)
                    times.append((time.perf_counter() - start) * 1000)
                # Ensure snippets are verbatim at their cited source locations.
                for i in selected:
                    c = index.chunks[i]
                    assert c["text"] == "\n".join(files[c["path"]].splitlines()[c["start"]-1:c["end"]])
                assert len(tokens(context)) <= budget
                row = dict(query_id=case["id"], repo=repo, category=case["category"], method=method,
                           budget=budget, latency_ms=statistics.median(times), latency_samples_ms=times,
                           output_tokens=len(tokens(context)), chunks=len(selected),
                           citations_valid=True, **score_evidence(index.chunks, selected, case["evidence"]))
                row["selected"] = [dict(path=index.chunks[i]["path"], start=index.chunks[i]["start"], end=index.chunks[i]["end"]) for i in selected]
                results.append(row)
                dest = BASE / ".research/contexts" / repo / f"{case['id']}-{method}-{budget}.txt"
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(context)
        print(repo, case["id"], flush=True)
    # Controlled appended comment: no pinned source is changed. Reuse unchanged vectors.
    edit_path = next(p for p in sorted(files) if p.endswith({"cobra": ".go", "flask": ".py", "fastify": ".js"}[repo]))
    edited = dict(files)
    prefix = "#" if repo == "flask" else "//"
    edited[edit_path] += f"\n{prefix} graf controlled incremental benchmark comment\n"
    start = time.perf_counter()
    updated = Index(edited, cache, model)
    update_s = time.perf_counter() - start
    start = time.perf_counter()
    noop = Index(edited, cache, model)
    noop_s = time.perf_counter() - start
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform != "darwin":
        rss *= 1024
    metrics = dict(repo=repo, source_files=len(files), source_bytes=sum(len(v.encode()) for v in files.values()),
                   chunks=len(index.chunks), scan_s=scan_s, model_load_s=model_load_s,
                   index_s=index_s, lexical_projection_build_s=index.lexical_build_s,
                   graph_build_s=index.graph_build_s, embedding_build_s=index.embedding_build_s,
                   embedding_cache_misses=index.embedding_misses, graph_relations=sum(map(len, index.edges.values())),
                   component_bytes={p.name: p.stat().st_size for p in out.iterdir()},
                   vector_cache_bytes=sum(p.stat().st_size for p in cache.glob("*.npy")),
                   update_s=update_s, update_embedding_misses=updated.embedding_misses, edit_path=edit_path,
                   noop_rebuild_s=noop_s, noop_embedding_misses=noop.embedding_misses,
                   peak_combined_process_rss_bytes=rss)
    (BASE / f"results/{repo}.json").write_text(json.dumps(dict(metrics=metrics, queries=results), indent=2) + "\n")


def aggregate():
    data = [json.loads((BASE / f"results/{repo}.json").read_text()) for repo in ["cobra", "flask", "fastify"]]
    groups = []
    for budget in [2000, 8000]:
        for method in METHODS:
            rows = [r for d in data for r in d["queries"] if r["method"] == method and r["budget"] == budget]
            positive = [r for r in rows if r["anchor_recall"] is not None]
            groups.append(dict(method=method, budget=budget, positive_queries=len(positive),
                               macro_anchor_recall=statistics.mean(r["anchor_recall"] for r in positive),
                               macro_annotated_chunk_precision=statistics.mean(r["annotated_chunk_precision"] for r in positive),
                               macro_file_recall=statistics.mean(r["file_recall"] for r in positive),
                               all_anchors_queries=sum(r["all_anchors"] for r in positive),
                               unsupported_queries_returning_context=sum(r["returned_context"] for r in rows if r["anchor_recall"] is None),
                               median_output_tokens=statistics.median(r["output_tokens"] for r in rows),
                               median_latency_ms=statistics.median(r["latency_ms"] for r in rows)))
    metadata = dict(python=sys.version, platform=platform.platform(), machine=platform.machine(),
                    processor=platform.processor(), tokenizer="cl100k_base", model=MODEL,
                    embedding_max_input_tokens=128, embedding_threads=4, embedding_batch_size=8,
                    embedding_variant="float32_cpu_derivative",
                    model_artifacts_sha256=hashlib.sha256((BASE / "results/model-artifacts.json").read_bytes()).hexdigest(),
                    protocol="exploratory_v1", query_sha256=hashlib.sha256((BASE / "benchmark/queries.json").read_bytes()).hexdigest(),
                    retriever_sha256=hashlib.sha256((BASE / "benchmark/retrieval.py").read_bytes()).hexdigest())
    (BASE / "results/summary.json").write_text(json.dumps(dict(environment=metadata, groups=groups, indexes=[d["metrics"] for d in data]), indent=2) + "\n")
    print(json.dumps(groups, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", choices=["cobra", "flask", "fastify"])
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    if args.repo:
        run_repo(args.repo, args.repeats)
    else:
        for repo in ["cobra", "flask", "fastify"]:
            subprocess.run([sys.executable, __file__, "--repo", repo, "--repeats", str(args.repeats)], check=True)
        aggregate()
