"""Render the paired Graphify comparison without rerunning or relabeling it."""
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]


def main():
    source = BASE / "results/graphify-comparison.json"
    result = json.loads(source.read_text())
    assert len(result["rows"]) == 60
    lines = [
        "# Paired graf / Graphify comparison\n",
        f"All {len(result['arms'])} query arms received identical filtered Cobra, Flask, and Fastify inputs, 30 frozen requests, and 2K/8K requested budgets. Each case runs {result.get('repeats', 3)} times per tool, rotating sequential order with a fixed Python hash seed. This is an exploratory code-file discovery comparison on development queries; the labels are author-selected and incomplete. Arms marked experimental are preregistered candidates, not accepted changes.\n",
        "Graphify uses the pinned code-only, unclustered configuration. Its outputs are native navigation hints. graf search returns source excerpts; graf locate returns one compact hint per file with an expansion ID. The two graf modes share the same BM25 index and scores. The [fixed locate policy](../benchmark/locate-experiment.json) was recorded before this run; the query set is reused from earlier experiments. The shared quality metric is **code-file hint recall**, not source-anchor recall or answer correctness.\n",
        "| Budget | Tool | Code-file hint recall | Scored queries | Median process query (ms) | Median text tokens | Outputs over cl100k budget |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for budget, tools in result["summary"].items():
        for tool, value in tools.items():
            lines.append(f"| {budget} | {tool} | {value['code_file_hint_recall']:.1%} | {value['scored_queries']} | {value['median_query_ms']:.1f} | {value['median_output_tokens']:.0f} | {value['over_cl100k_budget']} / 30 |")
    lines.extend([
        "",
        "| Budget | Tool | Code-file recall in first 5 files | In first 10 files | Median cited files |",
        "| --- | --- | ---: | ---: | ---: |",
    ])
    for budget, tools in result["summary"].items():
        for tool, value in tools.items():
            lines.append(f"| {budget} | {tool} | {value['code_file_hint_recall_at_5']:.1%} | {value['code_file_hint_recall_at_10']:.1%} | {value['median_cited_files']:.0f} |")
    small, large = result["summary"]["2000"], result["summary"]["8000"]
    lines.extend([
        "",
        f"At 2K, compact locate reaches {small['graf_locate']['code_file_hint_recall']:.1%} selected code-file recall, compared with {small['graf']['code_file_hint_recall']:.1%} for full excerpts and {small['graphify']['code_file_hint_recall']:.1%} for Graphify. At 8K, locate reaches {large['graf_locate']['code_file_hint_recall']:.1%} while listing a median of {large['graf_locate']['median_cited_files']:.0f} files, versus {large['graphify']['median_cited_files']:.0f} for Graphify. Wider coverage alone does not establish better ordering or agent efficiency.\n",
        "First-file metrics deduplicate paths in delivery order and count the code portion of the same annotations. They help expose broad listings; they do not establish full relevance precision. Hints may also include documentation files.\n",
        "Query times include fresh-process startup, imports, index loading, querying, rendering, and stdout. Graphify runs its pinned serving API in a Python helper; graf runs the Rust CLI with `--text`. These are not resident MCP measurements, and they do not establish agent task latency. OS caches and other system activity are uncontrolled. The earlier [resident Graphify control](measured-results.md) records a different serving condition and remains separate. Token counts cover complete text outputs from these arms; no JSON metadata or agent conversation is delivered in this experiment.\n",
        "Native Graphify budgets use its own convention. Overruns here mean its unchanged output exceeds the shared cl100k measurement, not a blanket defect claim. Shorter outputs are not sufficient evidence of task-token savings.\n",
        "| Repository | Shared input files | Tool | Fresh build (ms) | Serialized index (MiB) |",
        "| --- | ---: | --- | ---: | ---: |",
    ])
    for repo, value in result["repositories"].items():
        for tool, build in value["builds"].items():
            lines.append(f"| {repo} | {value['files']} | {tool} | {build['elapsed_ms']:.1f} | {build['index_bytes'] / 1048576:.2f} |")
    lines.extend([
        "",
        "Build measurements are one sample per repository/tool. Both graf query arms share the displayed graf index. Fresh builds discard earlier indexes but do not flush OS caches. Graphify stores graph nodes/edges; graf stores source windows and postings. Different stored functionality limits interpretation of disk and build differences.\n",
        "Unmeasured: " + "; ".join(result["unmeasured"]) + ". Full agent comparisons must include Graphify plus source reads with the same solver and all-attempt usage accounting. In particular, locate IDs and previews are hints, not full source coverage. No superiority or agent-token-saving claim follows from this run.\n",
        f"Graphify pin: `{result['baseline']['pin']['commit']}`, package version `{result['baseline']['installed_version']}`. The runner checks installed Python source files against that snapshot.\n",
        "Raw commands, outputs, input hashes, source receipts, and timing samples: [comparison JSON](../results/graphify-comparison.json).",
        f"Result SHA-256: `{hashlib.sha256(source.read_bytes()).hexdigest()}`.\n",
    ])
    dest = BASE / "research/graphify-comparison.md"
    dest.write_text("\n".join(lines))
    print(dest)


if __name__ == "__main__":
    main()
