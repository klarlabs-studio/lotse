# Measured retrieval pilot

These are exploratory source-anchor measurements, not answer accuracy or agent token savings.

## Source context retrieval

| Method | Budget | Anchor recall | All anchors / 27 | Annotated chunk precision | Median output tokens | Median resident ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| grep | 2000 | 37.0% | 4 | 8.5% | 1976 | 173.69 |
| bm25 | 2000 | 57.4% | 8 | 12.5% | 1977 | 29.57 |
| hierarchy | 2000 | 51.2% | 4 | 10.6% | 1984 | 28.76 |
| semantic | 2000 | 42.0% | 5 | 8.7% | 1982 | 55.91 |
| graph | 2000 | 56.8% | 8 | 12.6% | 1974 | 30.13 |
| hybrid | 2000 | 57.4% | 7 | 11.8% | 1984 | 46.60 |
| grep | 8000 | 64.2% | 12 | 4.1% | 7973 | 336.50 |
| bm25 | 8000 | 87.0% | 21 | 4.8% | 7975 | 211.04 |
| hierarchy | 8000 | 71.6% | 13 | 3.9% | 7962 | 217.58 |
| semantic | 8000 | 58.6% | 9 | 3.1% | 7978 | 239.58 |
| graph | 8000 | 82.7% | 17 | 4.5% | 7972 | 206.93 |
| hybrid | 8000 | 85.2% | 19 | 4.5% | 7962 | 228.00 |
| native ripgrep windows | 2000 | 24.7% | 3 | 6.2% | 1978 | 42.83 |
| native ripgrep windows | 8000 | 53.1% | 11 | 3.2% | 7964 | 105.27 |

## Category variation

Macro anchor recall at 2K tokens. Each positive category has only three or six authored queries.

| Category | grep | bm25 | hierarchy | semantic | graph | hybrid |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| exact | 41.7% | 58.3% | 58.3% | 50.0% | 58.3% | 58.3% |
| conceptual | 58.3% | 58.3% | 41.7% | 25.0% | 50.0% | 33.3% |
| architecture | 16.7% | 33.3% | 33.3% | 66.7% | 33.3% | 50.0% |
| impact | 33.3% | 83.3% | 66.7% | 66.7% | 83.3% | 83.3% |
| trace | 16.7% | 50.0% | 55.6% | 38.9% | 55.6% | 50.0% |
| discrepancy | 50.0% | 66.7% | 50.0% | 16.7% | 66.7% | 100.0% |

## Shared index costs

Model downloads are excluded. Cold/warm status follows the recorded embedding cache misses. Peak process memory includes shared runtime/model and retained indexes.

| Repository | Files | Chunks | Index s | Cold embedding misses | Edited rebuild s | Edited misses | No-op rebuild s | Peak combined MiB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| cobra | 54 | 844 | 96.56 | 839 | 1.02 | 1 | 1.76 | 315.0 |
| flask | 175 | 1474 | 246.49 | 1470 | 1.48 | 1 | 0.56 | 310.3 |
| fastify | 352 | 4294 | 124.39 | 4278 | 6.41 | 1 | 0.99 | 405.5 |

## Semantic input truncation

This resource-constrained control truncates most input chunks. Its scores must not be generalized to full-context or code-specialized embeddings.

| Repository | Documentation files | Test files | Median full model tokens per chunk | Chunks over 128 tokens |
| --- | ---: | ---: | ---: | ---: |
| cobra | 18 | 17 | 242 | 95.5% |
| flask | 92 | 57 | 221 | 91.8% |
| fastify | 51 | 231 | 212 | 92.1% |

## Graphify code navigation baseline

These code-file hints are not source excerpts and are not directly comparable to anchor recall above. Docs and semantic extraction were disabled, as was clustering.

| Repository | Build s | Graph KiB | Nodes | Budget | Code file hint recall | Median resident ms | Outputs over cl100k budget / 10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| cobra | 1.84 | 734.6 | 694 | 2000 | 100.0% | 23.39 | 0 |
| cobra | 1.84 | 734.6 | 694 | 8000 | 100.0% | 22.21 | 3 |
| flask | 6.19 | 1333.3 | 2144 | 2000 | 100.0% | 53.31 | 0 |
| flask | 6.19 | 1333.3 | 2144 | 8000 | 100.0% | 48.29 | 1 |
| fastify | 8.59 | 1460.6 | 2559 | 2000 | 88.9% | 35.71 | 0 |
| fastify | 8.59 | 1460.6 | 2559 | 8000 | 88.9% | 36.06 | 8 |

## Interpretation limits

- An anchor is a minimal line reference, not enough evidence to answer a question. Labels are incomplete and author-selected; annotated precision can undercount useful context.
- All approaches return snippets or hints for at least some insufficient-evidence questions. No calibrated abstention or generated-answer factuality evaluation is implemented.
- General-English embeddings, regex declarations, fixed windows, and a fixed heuristic graph are controls, not the strongest available versions of their families.
- Index timings include this machine's resource contention. Query timings are resident microbenchmarks; they do not include a full coding session or paid-model inference.
- Inspect first and subsequent latency samples in raw JSON; repeated resident calls are not controlled cold-cache runs.
- Synthetic task sanity checks validate fixtures, not agent repairs. End-to-end agent/interface arms are unrun.
