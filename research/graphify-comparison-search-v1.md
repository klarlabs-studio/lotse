# Historical full-excerpt graf / Graphify comparison

Both tools received identical filtered Cobra, Flask, and Fastify inputs, 30 frozen requests, and 2K/8K requested budgets. Each case runs three times per tool, alternating sequential order with a fixed Python hash seed. This is an exploratory code-file discovery comparison; the labels are author-selected and incomplete.

Graphify uses the pinned code-only, unclustered configuration. Its outputs are native navigation hints. graf returns source excerpts. The shared quality metric is **code-file hint recall**, not source-anchor recall or answer correctness.

| Budget | Tool | Code-file hint recall | Scored queries | Median process query (ms) | Median text tokens | Outputs over cl100k budget |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 2000 | graf | 72.2% | 27 | 235.7 | 1990 | 0 / 30 |
| 2000 | graphify | 96.3% | 27 | 723.3 | 1808 | 0 / 30 |
| 8000 | graf | 94.4% | 27 | 497.8 | 7976 | 0 / 30 |
| 8000 | graphify | 96.3% | 27 | 734.0 | 6844 | 11 / 30 |

Query times include fresh-process startup, imports, index loading, querying, rendering, and stdout. Graphify runs its pinned serving API in a Python helper; graf runs the Rust CLI with `--text`. These are not resident MCP measurements, and they do not establish agent task latency. OS caches and other system activity are uncontrolled. The earlier [resident Graphify control](measured-results.md) records a different serving condition and remains separate. Token counts cover complete text outputs from these arms; no JSON metadata or agent conversation is delivered in this experiment.

Native Graphify budgets use its own convention. Overruns here mean its unchanged output exceeds the shared cl100k measurement, not a blanket defect claim. Shorter outputs are not sufficient evidence of task-token savings.

| Repository | Shared input files | Tool | Fresh build (ms) | Serialized index (MiB) |
| --- | ---: | --- | ---: | ---: |
| cobra | 54 | graf | 321.1 | 1.66 |
| cobra | 54 | graphify | 2148.5 | 0.74 |
| flask | 175 | graf | 240.3 | 3.08 |
| flask | 175 | graphify | 4646.5 | 1.30 |
| fastify | 352 | graf | 491.5 | 7.75 |
| fastify | 352 | graphify | 10661.7 | 1.43 |

Build measurements are one sample per repository/tool. Fresh builds discard earlier indexes but do not flush OS caches. Graphify stores graph nodes/edges; graf stores source windows and postings. Different stored functionality limits interpretation of disk and build differences.

Unmeasured: agent task success and total tokens/cost; docs-enabled Graphify; peak memory; incremental update cost. Full agent comparisons must include Graphify plus source reads with the same solver and all-attempt usage accounting. No superiority or agent-token-saving claim follows from this run.

Graphify pin: `86c57be8204673c2a1b9849a3292a7dd3ab373d8`, package version `0.9.92`. The runner checks installed Python source files against that snapshot.

Raw commands, outputs, input hashes, source receipts, and timing samples: [comparison JSON](../results/graphify-comparison-search-v1.json).
Result SHA-256: `06cde31080f67e147f0b53611d7a60acc96315317a825785a44821d364b15065`.
