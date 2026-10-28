# Paired graf / Graphify comparison

All 14 query arms received identical filtered Cobra, Flask, and Fastify inputs, 30 frozen requests, and 2K/8K requested budgets. Each case runs 15 times per tool, rotating sequential order with a fixed Python hash seed. This is an exploratory code-file discovery comparison on development queries; the labels are author-selected and incomplete. Arms marked experimental are preregistered candidates, not accepted changes.

Graphify uses the pinned code-only, unclustered configuration. Its outputs are native navigation hints. graf search returns source excerpts; graf locate returns one compact hint per file with an expansion ID. The two graf modes share the same BM25 index and scores. The [fixed locate policy](../benchmark/locate-experiment.json) was recorded before this run; the query set is reused from earlier experiments. The shared quality metric is **code-file hint recall**, not source-anchor recall or answer correctness.

| Budget | Tool | Code-file hint recall | Scored queries | Median process query (ms) | Median text tokens | Outputs over cl100k budget |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2000 | graf | 72.2% | 27 | 38.7 | 1990 | 0 / 30 |
| 2000 | graf_locate | 94.4% | 27 | 38.8 | 1985 | 0 / 30 |
| 2000 | graf_gap | 57.4% | 27 | 31.3 | 1077 | 0 / 30 |
| 2000 | graf_floor | 63.0% | 27 | 32.0 | 1966 | 0 / 30 |
| 2000 | graf_locate_gap | 74.1% | 27 | 30.9 | 205 | 0 / 30 |
| 2000 | graf_locate_floor | 79.6% | 27 | 31.2 | 330 | 0 / 30 |
| 2000 | graf_locate_damped | 98.1% | 27 | 37.6 | 1992 | 0 / 30 |
| 2000 | graf_locate_english | 94.4% | 27 | 37.7 | 1987 | 0 / 30 |
| 2000 | graf_locate_nostop | 94.4% | 27 | 38.3 | 1989 | 0 / 30 |
| 2000 | graf_locate_fullid | 94.4% | 27 | 39.3 | 1984 | 0 / 30 |
| 2000 | graf_locate_path3 | 94.4% | 27 | 37.5 | 1987 | 0 / 30 |
| 2000 | graf_locate_damped_gap | 77.8% | 27 | 30.7 | 204 | 0 / 30 |
| 2000 | graf_locate_damped_floor | 88.9% | 27 | 31.3 | 367 | 0 / 30 |
| 2000 | graphify | 96.3% | 27 | 179.5 | 1808 | 0 / 30 |
| 8000 | graf | 94.4% | 27 | 38.6 | 7976 | 0 / 30 |
| 8000 | graf_locate | 98.1% | 27 | 38.6 | 3776 | 0 / 30 |
| 8000 | graf_gap | 57.4% | 27 | 31.0 | 1077 | 0 / 30 |
| 8000 | graf_floor | 79.6% | 27 | 32.2 | 4362 | 0 / 30 |
| 8000 | graf_locate_gap | 74.1% | 27 | 30.7 | 205 | 0 / 30 |
| 8000 | graf_locate_floor | 79.6% | 27 | 31.2 | 330 | 0 / 30 |
| 8000 | graf_locate_damped | 98.1% | 27 | 37.6 | 3778 | 0 / 30 |
| 8000 | graf_locate_english | 98.1% | 27 | 37.7 | 3831 | 0 / 30 |
| 8000 | graf_locate_nostop | 94.4% | 27 | 38.4 | 4018 | 0 / 30 |
| 8000 | graf_locate_fullid | 98.1% | 27 | 39.4 | 3834 | 0 / 30 |
| 8000 | graf_locate_path3 | 98.1% | 27 | 37.5 | 3778 | 0 / 30 |
| 8000 | graf_locate_damped_gap | 77.8% | 27 | 30.5 | 204 | 0 / 30 |
| 8000 | graf_locate_damped_floor | 88.9% | 27 | 31.2 | 367 | 0 / 30 |
| 8000 | graphify | 96.3% | 27 | 181.1 | 6844 | 11 / 30 |

| Budget | Tool | Code-file recall in first 5 files | In first 10 files | Median cited files |
| --- | --- | ---: | ---: | ---: |
| 2000 | graf | 64.8% | 72.2% | 6 |
| 2000 | graf_locate | 70.4% | 85.2% | 49 |
| 2000 | graf_gap | 57.4% | 57.4% | 3 |
| 2000 | graf_floor | 59.3% | 63.0% | 5 |
| 2000 | graf_locate_gap | 70.4% | 74.1% | 5 |
| 2000 | graf_locate_floor | 63.0% | 74.1% | 8 |
| 2000 | graf_locate_damped | 77.8% | 85.2% | 50 |
| 2000 | graf_locate_english | 53.7% | 85.2% | 49 |
| 2000 | graf_locate_nostop | 55.6% | 72.2% | 48 |
| 2000 | graf_locate_fullid | 63.0% | 85.2% | 48 |
| 2000 | graf_locate_path3 | 66.7% | 85.2% | 49 |
| 2000 | graf_locate_damped_gap | 77.8% | 77.8% | 5 |
| 2000 | graf_locate_damped_floor | 72.2% | 79.6% | 10 |
| 2000 | graphify | 70.4% | 85.2% | 24 |
| 8000 | graf | 70.4% | 85.2% | 16 |
| 8000 | graf_locate | 70.4% | 85.2% | 100 |
| 8000 | graf_gap | 57.4% | 57.4% | 4 |
| 8000 | graf_floor | 63.0% | 74.1% | 8 |
| 8000 | graf_locate_gap | 70.4% | 74.1% | 5 |
| 8000 | graf_locate_floor | 63.0% | 74.1% | 8 |
| 8000 | graf_locate_damped | 77.8% | 85.2% | 100 |
| 8000 | graf_locate_english | 53.7% | 85.2% | 100 |
| 8000 | graf_locate_nostop | 55.6% | 72.2% | 100 |
| 8000 | graf_locate_fullid | 63.0% | 85.2% | 100 |
| 8000 | graf_locate_path3 | 66.7% | 85.2% | 100 |
| 8000 | graf_locate_damped_gap | 77.8% | 77.8% | 5 |
| 8000 | graf_locate_damped_floor | 72.2% | 79.6% | 10 |
| 8000 | graphify | 70.4% | 85.2% | 52 |

At 2K, compact locate reaches 94.4% selected code-file recall, compared with 72.2% for full excerpts and 96.3% for Graphify. At 8K, locate reaches 98.1% while listing a median of 100 files, versus 52 for Graphify. Wider coverage alone does not establish better ordering or agent efficiency.

First-file metrics deduplicate paths in delivery order and count the code portion of the same annotations. They help expose broad listings; they do not establish full relevance precision. Hints may also include documentation files.

Query times include fresh-process startup, imports, index loading, querying, rendering, and stdout. Graphify runs its pinned serving API in a Python helper; graf runs the Rust CLI with `--text`. These are not resident MCP measurements, and they do not establish agent task latency. OS caches and other system activity are uncontrolled. The earlier [resident Graphify control](measured-results.md) records a different serving condition and remains separate. Token counts cover complete text outputs from these arms; no JSON metadata or agent conversation is delivered in this experiment.

Native Graphify budgets use its own convention. Overruns here mean its unchanged output exceeds the shared cl100k measurement, not a blanket defect claim. Shorter outputs are not sufficient evidence of task-token savings.

| Repository | Shared input files | Tool | Fresh build (ms) | Serialized index (MiB) |
| --- | ---: | --- | ---: | ---: |
| cobra | 54 | graf | 426.9 | 1.66 |
| cobra | 54 | graphify | 652.3 | 0.74 |
| cobra | 54 | graf-stopwords-english | 35.4 | 1.66 |
| cobra | 54 | graf-stopwords-none | 38.5 | 1.81 |
| cobra | 54 | graf-stopwords-english-full-identifiers | 44.8 | 1.83 |
| cobra | 54 | graf-path-weight-3 | 33.5 | 1.66 |
| flask | 175 | graf | 60.2 | 3.08 |
| flask | 175 | graphify | 1111.2 | 1.30 |
| flask | 175 | graf-stopwords-english | 66.4 | 3.11 |
| flask | 175 | graf-stopwords-none | 56.4 | 3.44 |
| flask | 175 | graf-stopwords-english-full-identifiers | 75.1 | 3.32 |
| flask | 175 | graf-path-weight-3 | 61.2 | 3.08 |
| fastify | 352 | graf | 170.5 | 7.75 |
| fastify | 352 | graphify | 2520.3 | 1.43 |
| fastify | 352 | graf-stopwords-english | 104.6 | 7.83 |
| fastify | 352 | graf-stopwords-none | 100.6 | 8.28 |
| fastify | 352 | graf-stopwords-english-full-identifiers | 138.8 | 8.39 |
| fastify | 352 | graf-path-weight-3 | 104.3 | 7.75 |

Build measurements are one sample per repository/tool. Both graf query arms share the displayed graf index. Fresh builds discard earlier indexes but do not flush OS caches. Graphify stores graph nodes/edges; graf stores source windows and postings. Different stored functionality limits interpretation of disk and build differences.

Unmeasured: agent task success and total tokens/cost; docs-enabled Graphify; peak memory; incremental update cost. Full agent comparisons must include Graphify plus source reads with the same solver and all-attempt usage accounting. In particular, locate IDs and previews are hints, not full source coverage. No superiority or agent-token-saving claim follows from this run.

Graphify pin: `86c57be8204673c2a1b9849a3292a7dd3ab373d8`, package version `0.9.92`. The runner checks installed Python source files against that snapshot.

Raw commands, outputs, input hashes, source receipts, and timing samples: [comparison JSON](../results/graphify-comparison.json).
Result SHA-256: `b1c7729ee8d259aeecc71ed4ad17c6c5e44e9f2925ac75eda0253143c79b145f`.
