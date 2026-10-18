# graf

A Rust CLI for retrieving source evidence from code and repository documentation, with a reproducible research suite behind it. The initial implementation uses BM25, source hashes, line citations, and progressive source reads. It runs locally without a model, embedding service, or graph database.

## Use the Rust CLI

Install a current Rust toolchain, then build from the locked dependencies:

```sh
cargo build --release --locked
./target/release/graf --root /path/to/repo index
./target/release/graf --root /path/to/repo locate "Where is session key rotation handled?" --budget 2000
./target/release/graf --root /path/to/repo search "session key rotation" --budget 2000
./target/release/graf --root /path/to/repo search "session key rotation" --kind docs --budget 1000
./target/release/graf --root /path/to/repo map --limit 20
./target/release/graf --root /path/to/repo stats
```

Start with `locate` for one compact hint per ranked file, including an expansion handle and a source-line preview. Its JSON `hints` are navigation aids; use `expand` to read the source. Both JSON and text deliver globally unique ID prefixes of at least 12 hex characters, extending them when necessary to avoid collisions.

Search emits JSON containing ranked excerpt IDs, source paths, line ranges, SHA-256 hashes, and a `context` string. Use `--text` to return only that string. The exact `cl100k_base` budget covers the context, including citation headers, handles/previews in locate, and separators; JSON metadata costs additional tokens. No output is evidence of answer correctness. An empty lexical match is not a calibrated statement that the repository lacks an answer.

Expand an anchor by passing its short handle from locate or its full ID from search:

```sh
./target/release/graf --root /path/to/repo expand EXCERPT_ID --before 24 --after 24 --budget 2000
```

Rerun `index` after changes. Unchanged files reuse their chunks; postings are rebuilt and the snapshot is saved atomically under `.graf/index.json`. Locate, search, and expansion reject stale selected files with a reindex instruction. New or unselected changed files require indexing to affect rankings. A moved checkout requires `index --rebuild`.

The scanner respects ignore rules within the root plus global ignore rules, excludes hidden/generated directories and symlinks, and indexes supported UTF-8 source/document files up to 1 MB. Add `.graf/` to each target repository's `.gitignore`. Indexed source text is stored locally in `.graf/`; ignore rules and extension filtering are not a secret detector. This is an initial CLI, with no MCP server, compiler-resolved graph, semantic index, or generated summaries. See the [implementation contract](research/rust-cli.md).

## Validate the Rust implementation

```sh
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
```

A change that must not alter results (refactors, packing or loading speedups) should also be checked for byte-identical output against the previous binary. Using fresh copies of the frozen corpora, `benchmark/check_equivalence.py` compares rebuilt indexes and every search, locate, expand, map, stats, and error output for the 30 requests at several budgets, kinds, and limits:

```sh
.research/venv/bin/python benchmark/check_equivalence.py OLD_GRAF target/release/graf
```

Equivalence is a diagnostic. It does not replace the Graphify comparison for any performance claim.

Every performance/retrieval benchmark must include the pinned Graphify baseline, as required by [AGENTS.md](AGENTS.md). After preparing the Python research environment and frozen corpora below, run the paired comparison on identical staged inputs, 30 requests, and 2K/8K budgets:

```sh
cargo build --release --locked
.research/venv/bin/python benchmark/compare_graphify.py
.research/venv/bin/python benchmark/check_comparison.py
.research/venv/bin/python benchmark/acceptance.py
.research/venv/bin/python benchmark/report_graphify_comparison.py
```

`acceptance.py` writes a per-case [acceptance report](results/acceptance.json) and exits nonzero while promotion is blocked. It checks quality and delivered-text token regressions; these exploratory results cannot establish agent efficiency. See the [decision rule](research/protocol.md#decision-rule) for the remaining acceptance requirements.

The [paired report](research/graphify-comparison.md) is generated from `results/graphify-comparison.json`, with source receipts, commands, raw text, three timing samples per query arm/case, shared code-file discovery recall, recall among the first 5/10 delivered files, file counts, output tokens, index size, and build time. Query timings include fresh-process startup and index loading for both tools. Graphify uses its pinned serving API in a Python subprocess helper; graf uses the Rust CLI. Both `graf search` and `graf locate` are compared to Graphify using the same index and unchanged BM25 scores. The [fixed locate policy](benchmark/locate-experiment.json) is recorded before measurement. The Graphify configuration is code-only, so documentation quality and agent task savings remain unmeasured. Navigation hints and source excerpts are labeled separately; do not compare source-anchor recall against Graphify's file hints.

The source-line integrity diagnostic also runs the required paired Graphify comparison:

```sh
.research/venv/bin/python benchmark/rust_pilot.py
```

The source-line diagnostic writes `results/rust-pilot.json` and links its paired Graphify results receipt. It has broader source filters and slightly different identifier tokenization than the original Python pilot; it is not a controlled language-speed or Graphify comparison. Query timings include subprocess startup, index loading, tokenizer initialization, source checks, packing, and serialization.

## Research suite

This workspace also contains a cited scientific review, frozen inputs, 30 exploratory requests, retrieval prototypes, a pinned Graphify navigation baseline, and six synthetic coding-task fixtures.

Start with the [research findings](research/report.md), [scientific evidence](research/evidence.md), and [evaluation protocol](research/protocol.md). Raw measurements live in `results/`; downloaded repositories, model weights, vector caches, source excerpts, and test workspaces stay under ignored `.research/`.

## Reproduce the retrieval pilot

Use Python 3.12 and uv. The fetch/model/package steps need network access; the measured retrievers use a local model. Do not run independent benchmarks concurrently when collecting timings.

```sh
mkdir -p .research
UV_CACHE_DIR=.research/uv-cache uv venv --python python3.12 .research/venv
.research/venv/bin/python benchmark/fetch.py
UV_CACHE_DIR=.research/uv-cache uv pip install --python .research/venv/bin/python -r benchmark/requirements.lock
UV_CACHE_DIR=.research/uv-cache uv pip install --python .research/venv/bin/python --no-deps .research/corpora/graphify
.research/venv/bin/python benchmark/prepare_models.py
.research/venv/bin/python benchmark/annotate.py
.research/venv/bin/python benchmark/validate_cpu_model.py
.research/venv/bin/python benchmark/corpus_audit.py
.research/venv/bin/python -m unittest discover -s benchmark -p 'test_*.py' -v
.research/venv/bin/python benchmark/run.py
.research/venv/bin/python benchmark/graphify_baseline.py
.research/venv/bin/python benchmark/ripgrep_baseline.py
.research/venv/bin/python benchmark/report.py
.research/venv/bin/python benchmark/check_results.py
```

`fetch.py` refuses to overwrite existing snapshots. Start in a clean checkout for a full reproduction. Repeating `run.py` reuses cached vectors and must be labeled warm; move `.research/vectors-128` aside for a new cold embedding run. The upstream model revision is pinned in `prepare_models.py`; it creates and validates a float32 CPU derivative. Model and tokenizer artifacts are hashed in `results/model-artifacts.json`; vector namespaces include this fingerprint and the 128-model-token input cap. Different runtime hardware can produce different timings and numerical ordering.

For a quick smoke run after model preparation, use `benchmark/run.py --repo cobra --repeats 1`. The aggregate report requires all three repositories. Runs preserve per-query selected source locations and all latency samples. The semantic index can be expensive to build on CPU; its build time is part of the comparison.

## Coding task fixtures

Install Go and Node.js, plus the locked Python test dependencies. Provision Fastify runtime dependencies in a disposable copy; lifecycle scripts are disabled:

```sh
mkdir -p .research/task-runtime/fastify
cp benchmark/fastify-runtime/package*.json .research/task-runtime/fastify/
npm ci --ignore-scripts --no-audit --no-fund --prefix .research/task-runtime/fastify --cache .research/npm-cache
.research/venv/bin/python benchmark/check_tasks.py
```

The runtime-only npm lock is committed; Go dependencies follow the snapshot's `go.sum`. Python uses the pinned repository source through `PYTHONPATH`; the installed Flask package supplies dependencies. `check_tasks.py` checks base-pass, injected-fail, and restored-pass. It does not call an agent. Repeating it requires a new `--run-id` to avoid overwriting workspaces.

Prepare an independent faulty workspace with:

```sh
.research/venv/bin/python benchmark/agent_trials.py prepare cobra-exact-args trial-001
.research/venv/bin/python benchmark/agent_trials.py verify cobra-exact-args .research/trials/trial-001/workspace
```

Keep the fault catalog and evidence labels outside an agent's accessible workspace. Run each arm against the same faulty snapshot, and reindex that snapshot rather than the clean reference. The [protocol](research/protocol.md) specifies agent and interface controls. No agent/model or inference budget is configured by default.

## Paired task-tool preparation

The [complete-task comparison setup](research/task-comparison.md) has a common JSONL tool broker for graf and Graphify, with source reads, fallback search, edits, and trusted verification. All six fault fixtures have been checked through both backends. These checks restore known source; they are not agent repairs or inference-saving measurements. A solver adapter, fixed model, and inference cap still need configuration.

```sh
.research/venv/bin/python benchmark/check_task_sessions.py --run-id paired-broker-v2
.research/venv/bin/python benchmark/task_session.py prepare cobra-exact-args demo-graf --arm graf
.research/venv/bin/python benchmark/task_session.py serve cobra-exact-args demo-graf --arm graf
```

`serve` accepts one JSON request per line, such as `{"operation":"retrieve","arguments":{"query":"exact argument count","budget":2000}}`. Both arms use the same operation definitions. Session metadata, tool events, build receipts, and verification logs stay outside the model's source workspace. Use `--arm graphify` with a different trial ID for the other arm. A valid solver adapter must use only the broker's tools and preserve every actual model call's usage; arbitrary native shell/file access would invalidate the experiment.

## Audit an agent trace

Supply JSONL events to `benchmark/agent_trials.py audit TRACE.jsonl`. A `model_usage` event requires `component`, `model`, `input_tokens`, `cached_input_tokens`, `output_tokens`, `reasoning_tokens`, and `cost_usd`. Input/output totals must be measured and include any cached/reasoning breakdowns. Use explicit `null` for an unknown cached/reasoning breakdown or dollar charge; its aggregate stays unknown rather than becoming zero. Include every solver, explorer, compressor, and retrieval-model call. Tool events use `type: tool`; the final event uses `type: verification` and boolean `passed`.

Missing usage is unmeasured, not zero. The validator checks accounting shape and sums; it cannot authenticate provider usage or independently prove that a reported verification event occurred. Preserve actual provider logs, final diffs, and verifier output for a trustworthy trial.

## License

MIT. See [LICENSE](LICENSE). Downloaded corpora and the Graphify baseline under `.research/` keep their own upstream licenses.
