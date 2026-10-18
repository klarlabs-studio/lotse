# Repository context evaluation protocol

## Required comparator

Always include pinned Graphify when benchmarking graf. Run `benchmark/compare_graphify.py` for the common code-file discovery comparison on identical filtered inputs, queries, budgets, and repeated fresh subprocesses. Record source hashes, raw output, commands, build time, serialized index bytes, output tokens, actual budget overruns, and all timing samples. Keep standalone integrity checks labeled diagnostic; they cannot support comparative efficiency claims.

Graphify's code-only native hints and graf's source excerpts are different products. Report the common code-file discovery score alongside that distinction, not a shared source-anchor score or an answer-quality ranking. Fresh subprocess Graphify measurements use its pinned serving API in a Python helper and include startup/import/load overhead; the earlier resident control remains separately labeled. Full task comparisons require Graphify plus source reads, the same solver, total usage accounting, and a docs-capable Graphify arm when evaluating documentation. Report missing measurements explicitly.

## Frozen inputs and judgments

Use the exact commits in `benchmark/corpora.json` and verify archive receipts in `results/snapshots.json`. Cobra, Flask, and Fastify provide Go, Python, and JavaScript respectively. These are convenience-selected open-source frameworks, not a representative sample of proprietary product repositories.

Index code and repository docs, including reStructuredText for Flask. Exclude hidden paths, vendor/node_modules, graphify output, unsupported binaries, non-UTF-8 text, and files larger than 1 MB. Every retriever sees the same filtered input. Include examples and tests. Keep third-party licenses in the downloaded snapshots; do not redistribute their contents in result files.

Freeze 30 authored requests before evaluating: two exact lookups, two conceptual requests, one architecture question, one change-impact request, two documentation-to-implementation/test traces, one claim check, and one insufficient-evidence request per repository. Claims are checked for consistency; they are not assertions that actual documentation bugs exist. Unsupported commercial/roadmap questions are negative controls, not proven exhaustive absence judgments.

The references identify minimal source-line anchors and file hashes. They are author-selected exploratory judgments and incomplete relevance labels, not independently adjudicated gold. Report anchor recall and annotated-chunk precision by those names. Do not call them answer accuracy or true retrieval precision. Independently review and expand judgments before release claims; do not tune retrievers on these same queries.

## Retrieval pilot

Common unit: 24 consecutive source lines, nonoverlapping, with verbatim text and relative path/line citation. This deliberately controls chunking across methods; it does not test the best possible parser-specific chunker. Use `cl100k_base` to count rendered source context, including headers, at 2,000 and 8,000 tokens. This tokenizer is an experimental convention, not every agent model's tokenizer.

| Method | Fixed implementation |
| --- | --- |
| grep | OR-term overlap over chunks; transparent lexical control, not an autonomous grep/read session |
| bm25 | BM25, k1=1.2, b=0.75, identifier splitting and fixed stopwords; path included |
| hierarchy | Rank file signature/heading projections with BM25, retain six files, rank their source chunks |
| semantic | Local BGE small English v1.5 through FastEmbed, float32 CPU derivative; normalized cosine; explicit 128-model-token input cap can truncate a chunk |
| graph | Three lexical seed chunks, expand up to 32 neighboring files per seed, lexical rank inside the selected files |
| hybrid | Reciprocal rank fusion of lexical, semantic, and graph candidates, constant 60, top 100 per list |

An additional native ripgrep control runs case-insensitive OR-term matching over the same filtered files, ranks shared windows by matching-line count, and uses the same output packer. Record its actual version, raw matched bytes, source-anchor scores, and subprocess latency. This is an executable search/window baseline, not a full autonomous search/read session. The Python `grep` control above remains separately labeled and does not establish ripgrep's performance.

Declarations use documented regex heuristics. Relations are undirected cross-file mentions of globally unambiguous declaration names of at least four characters. They are inferred candidates, not resolved calls, precise references, control flow, or a recreation of RepoGraph/LocAgent. The hierarchy uses source declarations and headings, not generated summaries. The hybrid is a fixed fusion baseline, not a learned intent router or adaptive retrieval policy.

Pack only complete chunks from the first 100 ranked candidates, skip candidates that exceed remaining budget, deduplicate chunk IDs. No score threshold is calibrated for abstention: negative controls expose this limitation. Validate every source excerpt byte-for-text against its location. Line-valid citations establish provenance, not semantic support for an answer. Embedding uses four CPU threads and batches of eight ordered by input length to reduce padding. The 128-token input cap bounds a resource-constrained semantic control; it was chosen for CPU feasibility before observing retrieval scores. Earlier uncapped indexing attempts were stopped and are excluded from measured results. Do not infer the best embedding model's quality from this control.

Measure three sequential resident query+packing invocations per case/method/budget and retain every sample and median. Exclude model load and indexing from resident latency, reporting them separately. Repeated queries are not guaranteed cold: report first and later samples, and do not claim cold filesystem/cache performance. Output-token metrics exclude tool schema, request, reasoning, and later agent turns.

Cold embedding-index construction means an empty per-repository vector cache, with model artifacts already downloaded. Subsequent rebuilds are warm. Store vectors by content SHA-256 under a model-artifact/input-cap namespace; the controlled one-file edit appends a comment in an in-memory document copy, with unchanged-vector reuse. Rebuild lexical structures and relations in full. A no-op rebuild is not a constant-time watcher update. Peak RSS is for the combined Python process, including model and simultaneously retained old/new indexes; it is not a per-method minimum. Component serialization sizes are diagnostic, not full production index disk sizes. Report shared model artifacts separately.

The downloaded model uses float16 tensors despite its upstream repository name containing `onnx-Q`. Stack sampling identified float16 CPU matrix multiplication as a bottleneck on this machine. A separately hashed float32 derivative promotes floating tensor types and Cast targets without training; ONNX Runtime validates loadability. The generic ONNX checker rejects the upstream ORT-optimized LayerNormalization/opset combination even before conversion, so it is not used as a successful validation claim. A 16-chunk sanity comparison is recorded in `results/cpu-model-validation.json`; it is a numerical spot check, not a proof of identical rankings. Aborted float16 builds are excluded from measured indexes.

## Graphify baseline

Run the pinned source, default dependencies, `extract --code-only --no-cluster --max-workers 2` on filtered staged copies. No assistant installation, hooks, semantic extraction, or paid API. Run its resident BFS query API with native budgets of 2K/8K and record its native output unchanged. Separate extraction time and graph load time.

Report code-file hint recall on the code portion of the annotations, graph size, nodes/edges, latency, valid source locations, and actual cl100k token counts. Native graph nodes point to code rather than returning the same source excerpts as the prototypes. Do not rank Graphify's hint recall against source-anchor recall as if they were the same metric. Flag native output exceeding the shared tokenizer budget. A docs-enabled, clustered Graphify run is a separate future configuration requiring semantic-extraction budget; the code-only run does not establish inferiority on documentation tasks.

## Compact locate delivery experiment

The next comparison retains full-excerpt BM25 search and adds `locate`, with one best-scoring source anchor per file, a content-derived expansion ID, and a capped source preview. BM25 scoring and the index remain unchanged; the fixed file-selection and preview policy is recorded in `benchmark/locate-experiment.json` before the run. Hash that policy in the comparison receipt.

Run both graf query arms and pinned Graphify on identical staged inputs with the same text budgets and three sequential invocations per case. Rotate arm order across repetitions. Include handles and previews in delivered-text counts. Independently verify every locate ID, anchor range, preview line/prefix, source hash, and text budget. Report common code-file recall, recall among the first five/ten unique delivered file hints, and file counts. Hints do not count as delivered full source excerpts. Reused exploratory requests cannot establish generalization, and locate-plus-expand agent usage remains a separate unmeasured quantity.

## Agent and interface trials

Use the same six fault-repair tasks, base snapshots, agent version, model, system prompt, tool access, decoding settings, and per-run spend/turn limits across arms. Define faults and verification commands in `benchmark/agent-tasks.json`. Each task must pass at the base, fail after injection, and pass after an accepted repair. Treat these synthetic faults as harness smoke tests; use natural held-out issues for product claims.

Compare ordinary search/read, the strongest source-context retriever, Graphify plus source read, and the strongest retriever exposed as context/expand requests. Keep the ranking backend fixed for the last pair to isolate interface effects. Give every arm ordinary search/read fallback. Build indexes from the faulty task snapshot, never the repaired source or benchmark annotations. Keep task fault metadata and reference evidence outside the agent workspace.

Experimental operations: `context(query, budget)` returns source excerpts with opaque handles; `expand(handle, budget)` returns additional source lines without repeating previously delivered lines; `locate(query)` returns ranked path/line hints. All include snapshot identity, current content hash, observed-at information, truncation, and provenance. Distinguish source facts, documented intent, and inferred links. Reject stale handles after edits and refresh the affected index before expansion. This is an experiment contract, not a committed product API.

Use three paired trials per task and arm. Retain raw model usage, tool events, stdout/stderr, final diff, and verification results. Report task success and tokens/cost across **all** attempts, plus paired successful-task statistics. Count retrieval/compression/explorer inference, tool definitions, input/output/reasoning, and cached/uncached usage separately; do not apply one token price to every category. Measure wall time for indexing, model calls, tool operations, and validation. Record budget-exhausted runs as failures, never drop them. No answer-quality inference from retrieval metrics alone.

The [task broker and paired fixture validation](task-comparison.md) implement common navigation/read/edit/test operations, but do not invoke a solver. Agent runs remain unrun until an agent command/model and inference budget are supplied. The executable task preparation and verification helper does not itself invoke an agent. The JSONL validator can audit supplied traces, but does not fabricate missing provider usage or turn incomplete traces into successful trials.

## Decision rule

Outperforming pinned Graphify is a promotion requirement. Before collecting results, freeze the supported workload, shared quality criteria, primary efficiency metric, resource limits, and paired repetition plan. For each evaluated case, require no quality or primary-efficiency regression against Graphify and, when available, the last accepted graf version. Require strict improvement in the declared primary efficiency metric across the workload. Report paired differences and uncertainty; an unresolved difference is not evidence of a win. Do not select the winning metric after seeing results or hide individual losses behind an aggregate gain.

Agent-efficiency acceptance requires independently verified task outcomes and complete all-attempt accounting under the same solver and limits. Include failures, retries, index construction, source reads, and any retrieval/compression inference in the declared measurement boundaries. Missing usage, unmatched configurations, or unrun tasks leave the gate unpassed. Retrieval metrics and fixture validation cannot substitute for this gate. Natural held-out tasks are required before product superiority claims; the current exploratory requests and synthetic faults remain diagnostics.

Report latency, memory, cold build, and update cost alongside the primary metric, including tradeoffs. A claim of improvement on all of these dimensions requires no regression in each dimension on every measured case. Preserve the search fallback and retain losing or unproven changes as experimental. Every claim must name its tested workload and conditions; no finite benchmark establishes a win on every future request.

The current prototype has not passed: locate has lower aggregate code-file recall than Graphify at 2K, and complete agent-task efficiency remains unmeasured. There is no accepted graf baseline yet.

Run `benchmark/acceptance.py` after the paired comparison. It first runs the source/output audit, then checks both graf arms against Graphify on every frozen query/budget pair. The diagnostic requires no loss in shared code-file recall, recall at five/ten files, or delivered-text token count, plus a strict reduction in total delivered-text tokens. It reports latency separately and rejects missing, duplicate, unexpected, or invalid measurements. Null code recall is allowed only for requests whose annotations contain no code evidence. Such requests have no measured quality outcome; they cannot support an overall quality claim.

The executable writes `results/acceptance.json`, including each case's metrics and regressions. These rules were selected after the historical results and are explicitly retrospective diagnostics. Even a diagnostic pass cannot authorize product promotion: held-out agent outcomes, complete usage accounting, and a preregistered experiment are still required. The command returns a nonzero exit status while promotion is blocked, including when the evidence audit fails. It does not run inference or turn delivered retrieval-text counts into total agent-token measurements.
