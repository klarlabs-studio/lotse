"""Render measured results from JSON. Keep scientific inference in research/report.md."""
import json
from pathlib import Path
import statistics

BASE = Path(__file__).resolve().parents[1]


def main():
    data = json.loads((BASE / "results/summary.json").read_text())
    lines = ["# Measured retrieval pilot", "", "These are exploratory source-anchor measurements, not answer accuracy or agent token savings.", "",
             "## Source context retrieval", "", "| Method | Budget | Anchor recall | All anchors / 27 | Annotated chunk precision | Median output tokens | Median resident ms |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for g in data["groups"]:
        lines.append(f"| {g['method']} | {g['budget']} | {g['macro_anchor_recall']:.1%} | {g['all_anchors_queries']} | {g['macro_annotated_chunk_precision']:.1%} | {g['median_output_tokens']:.0f} | {g['median_latency_ms']:.2f} |")
    native = json.loads((BASE / "results/ripgrep.json").read_text())["queries"]
    for budget in [2000, 8000]:
        rows = [r for r in native if r["budget"] == budget]
        positive = [r for r in rows if r["anchor_recall"] is not None]
        lines.append(f"| native ripgrep windows | {budget} | {statistics.mean(r['anchor_recall'] for r in positive):.1%} | {sum(r['all_anchors'] for r in positive)} | {statistics.mean(r['annotated_chunk_precision'] for r in positive):.1%} | {statistics.median(r['output_tokens'] for r in rows):.0f} | {statistics.median(r['latency_ms'] for r in rows):.2f} |")
    rows = [r for repo in ["cobra", "flask", "fastify"] for r in json.loads((BASE / f"results/{repo}.json").read_text())["queries"]]
    lines += ["", "## Category variation", "", "Macro anchor recall at 2K tokens. Each positive category has only three or six authored queries.", "",
              "| Category | grep | bm25 | hierarchy | semantic | graph | hybrid |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for category in ["exact", "conceptual", "architecture", "impact", "trace", "discrepancy"]:
        values = []
        for method in ["grep", "bm25", "hierarchy", "semantic", "graph", "hybrid"]:
            group = [r for r in rows if r["category"] == category and r["budget"] == 2000 and r["method"] == method]
            values.append(f"{statistics.mean(r['anchor_recall'] for r in group):.1%}")
        lines.append("| " + " | ".join([category] + values) + " |")
    lines += ["", "## Shared index costs", "", "Model downloads are excluded. Cold/warm status follows the recorded embedding cache misses. Peak process memory includes shared runtime/model and retained indexes.", "",
              "| Repository | Files | Chunks | Index s | Cold embedding misses | Edited rebuild s | Edited misses | No-op rebuild s | Peak combined MiB |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for m in data["indexes"]:
        lines.append(f"| {m['repo']} | {m['source_files']} | {m['chunks']} | {m['index_s']:.2f} | {m['embedding_cache_misses']} | {m['update_s']:.2f} | {m['update_embedding_misses']} | {m['noop_rebuild_s']:.2f} | {m['peak_combined_process_rss_bytes']/2**20:.1f} |")
    audit = json.loads((BASE / "results/corpus-audit.json").read_text())
    lines += ["", "## Semantic input truncation", "", "This resource-constrained control truncates most input chunks. Its scores must not be generalized to full-context or code-specialized embeddings.", "",
              "| Repository | Documentation files | Test files | Median full model tokens per chunk | Chunks over 128 tokens |", "| --- | ---: | ---: | ---: | ---: |"]
    for a in audit:
        lines.append(f"| {a['repo']} | {a['documentation_files']} | {a['test_files']} | {a['median_model_input_tokens']:.0f} | {a['fraction_chunks_over_128']:.1%} |")
    graphify = json.loads((BASE / "results/graphify.json").read_text())
    lines += ["", "## Graphify code navigation baseline", "", "These code-file hints are not source excerpts and are not directly comparable to anchor recall above. Docs and semantic extraction were disabled, as was clustering.", "",
              "| Repository | Build s | Graph KiB | Nodes | Budget | Code file hint recall | Median resident ms | Outputs over cl100k budget / 10 |", "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for g in graphify:
        for budget in [2000, 8000]:
            group = [r for r in g["queries"] if r["budget"] == budget]
            positives = [r["code_file_hint_recall"] for r in group if r["code_file_hint_recall"] is not None]
            lines.append(f"| {g['repo']} | {g['build_s']:.2f} | {g['graph_bytes']/1024:.1f} | {g['nodes']} | {budget} | {statistics.mean(positives):.1%} | {statistics.median(r['resident_latency_ms'] for r in group):.2f} | {sum(r['over_cl100k_budget'] for r in group)} |")
    lines += ["", "## Interpretation limits", "", "- An anchor is a minimal line reference, not enough evidence to answer a question. Labels are incomplete and author-selected; annotated precision can undercount useful context.",
              "- All approaches return snippets or hints for at least some insufficient-evidence questions. No calibrated abstention or generated-answer factuality evaluation is implemented.",
              "- General-English embeddings, regex declarations, fixed windows, and a fixed heuristic graph are controls, not the strongest available versions of their families.",
              "- Index timings include this machine's resource contention. Query timings are resident microbenchmarks; they do not include a full coding session or paid-model inference.",
              "- Inspect first and subsequent latency samples in raw JSON; repeated resident calls are not controlled cold-cache runs.",
              "- Synthetic task sanity checks validate fixtures, not agent repairs. End-to-end agent/interface arms are unrun.", ""]
    (BASE / "research/measured-results.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
