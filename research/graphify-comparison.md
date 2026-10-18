# Paired graf / Graphify comparison

All three query arms received identical filtered Cobra, Flask, and Fastify inputs, 30 frozen requests, and 2K/8K requested budgets. Each case runs three times per tool, rotating sequential order with a fixed Python hash seed. This is an exploratory code-file discovery comparison; the labels are author-selected and incomplete.

Graphify uses the pinned code-only, unclustered configuration. Its outputs are native navigation hints. graf search returns source excerpts; graf locate returns one compact hint per file with an expansion ID. The two graf modes share the same BM25 index and scores. The [fixed locate policy](../benchmark/locate-experiment.json) was recorded before this run; the query set is reused from earlier experiments. The shared quality metric is **code-file hint recall**, not source-anchor recall or answer correctness.

| Budget | Tool | Code-file hint recall | Scored queries | Median process query (ms) | Median text tokens | Outputs over cl100k budget |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2000 | graf | 72.2% | 27 | 215.1 | 1990 | 0 / 30 |
| 2000 | graf_locate | 94.4% | 27 | 202.0 | 1985 | 0 / 30 |
| 2000 | graphify | 96.3% | 27 | 678.5 | 1808 | 0 / 30 |
| 8000 | graf | 94.4% | 27 | 446.4 | 7976 | 0 / 30 |
| 8000 | graf_locate | 98.1% | 27 | 193.3 | 3776 | 0 / 30 |
| 8000 | graphify | 96.3% | 27 | 673.9 | 6844 | 11 / 30 |

| Budget | Tool | Code-file recall in first 5 files | In first 10 files | Median cited files |
| --- | --- | ---: | ---: | ---: |
| 2000 | graf | 64.8% | 72.2% | 6 |
| 2000 | graf_locate | 70.4% | 85.2% | 49 |
| 2000 | graphify | 70.4% | 85.2% | 24 |
| 8000 | graf | 70.4% | 85.2% | 16 |
| 8000 | graf_locate | 70.4% | 85.2% | 100 |
| 8000 | graphify | 70.4% | 85.2% | 52 |

At 2K, compact locate reaches 94.4% selected code-file recall, compared with 72.2% for full excerpts and 96.3% for Graphify. At 8K, locate reaches 98.1% while listing a median of 100 files, versus 52 for Graphify. Wider coverage alone does not establish better ordering or agent efficiency.

First-file metrics deduplicate paths in delivery order and count the code portion of the same annotations. They help expose broad listings; they do not establish full relevance precision. Hints may also include documentation files.

Query times include fresh-process startup, imports, index loading, querying, rendering, and stdout. Graphify runs its pinned serving API in a Python helper; graf runs the Rust CLI with `--text`. These are not resident MCP measurements, and they do not establish agent task latency. OS caches and other system activity are uncontrolled. The earlier [resident Graphify control](measured-results.md) records a different serving condition and remains separate. Token counts cover complete text outputs from these arms; no JSON metadata or agent conversation is delivered in this experiment.

Native Graphify budgets use its own convention. Overruns here mean its unchanged output exceeds the shared cl100k measurement, not a blanket defect claim. Shorter outputs are not sufficient evidence of task-token savings.

| Repository | Shared input files | Tool | Fresh build (ms) | Serialized index (MiB) |
| --- | ---: | --- | ---: | ---: |
| cobra | 54 | graf | 376.4 | 1.66 |
| cobra | 54 | graphify | 2374.7 | 0.74 |
| flask | 175 | graf | 193.0 | 3.08 |
| flask | 175 | graphify | 4057.9 | 1.30 |
| fastify | 352 | graf | 460.2 | 7.75 |
| fastify | 352 | graphify | 9080.4 | 1.43 |

Build measurements are one sample per repository/tool. Both graf query arms share the displayed graf index. Fresh builds discard earlier indexes but do not flush OS caches. Graphify stores graph nodes/edges; graf stores source windows and postings. Different stored functionality limits interpretation of disk and build differences.

Unmeasured: agent task success and total tokens/cost; docs-enabled Graphify; peak memory; incremental update cost. Full agent comparisons must include Graphify plus source reads with the same solver and all-attempt usage accounting. In particular, locate IDs and previews are hints, not full source coverage. No superiority or agent-token-saving claim follows from this run.

Graphify pin: `86c57be8204673c2a1b9849a3292a7dd3ab373d8`, package version `0.9.92`. The runner checks installed Python source files against that snapshot.

Raw commands, outputs, input hashes, source receipts, and timing samples: [comparison JSON](../results/graphify-comparison.json).
Result SHA-256: `799c4cf9d7af1c1eee83b0799c5364aa4ab8d4df24209828c0a0836b53f60370`.
