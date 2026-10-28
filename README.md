# graf

graf helps coding agents decide where to work without reading the whole repository. Given an issue or task, it lists the likely files and shows the likely functions in full, within a token budget. It runs locally with no model, embedding service, or graph database. A reproducible research suite benchmarks it against [Graphify](research/localization-results.md).

## Quick start

```sh
cargo install --locked --path .   # from a graf checkout; puts `graf` on your PATH
cd /path/to/repo
graf triage "Reply hooks run twice when the payload is a stream"
```

The first query indexes the repository into `.graf/` (git-ignored automatically). Rerun `graf index` after edits; the MCP server below does this itself.

The agent workflow is three commands:

| Step | Command | Returns |
|---|---|---|
| Orient | `graf triage "<issue or task text>"` | likely files, then likely functions in full |
| Narrow | `graf outline <path>` | a file's functions with line ranges and signatures |
| Follow up | `graf units "<identifiers or words>"` | more whole functions, ranked |

Add `--text` for plain context instead of JSON, and `--budget N` to cap the `cl100k_base` tokens returned (triage defaults to 1,500). Rust, Go, Python, JavaScript, TypeScript, TSX, and JSX are split into functions; other files are searched as line windows.

`graf outline <path>` keeps its legacy full-outline output. Add paging options to request a bounded page; for example, `graf outline src/handler.ts --budget 1000 --offset 0 --limit 25 --text` returns whole function signatures and a continuation footer when more entries remain. The budget counts the header, entries, and footer together. `--offset` is a zero-based function-entry offset, and `--limit` defaults to 100 when paging is enabled.

## Use with agents (MCP)

`graf mcp` serves the same tools over the Model Context Protocol on stdio. It keeps the index in memory and builds it on first use. Before each tool call it scans and hashes eligible sources, detecting new, changed, deleted, renamed, and newly ignored files. Updates with the same analyzer reuse unchanged files' windows and term counts, remapping postings when file positions change. Analyzer changes and explicit full rebuilds retokenize all sources. This freshness check still adds filesystem work on every call; MCP latency has not been comparatively benchmarked. MCP `search` includes expansion handles within its text budget. Successful empty results remain empty, including when the requested budget is too small to deliver an excerpt.

MCP `outline` returns a bounded page by default: 1,500 cl100k tokens and up to 100 functions, with a continuation footer for the next offset. Its accepted budget range is 64–32,000 tokens. Requests are rejected before indexing when they exceed the 1 MiB input-frame limit, a 64,000-byte UTF-8 string limit, the 32,000-token context limit, or the 10,000-line expansion limit. A missing or corrupt index is rebuilt automatically; filesystem and permission errors are returned. Both CLI and MCP reject index snapshots above 1 GiB with a resource-limit error and leave them untouched; the largest measured index, VS Code's, is 159 MB.

Claude Code:

```sh
claude mcp add graf -- graf mcp
```

Cursor, Windsurf, Claude Desktop, and other clients that read an `mcpServers` file:

```json
{
  "mcpServers": {
    "graf": { "command": "graf", "args": ["mcp"], "cwd": "/path/to/repo" }
  }
}
```

If the client cannot set a working directory, pass `"args": ["--root", "/path/to/repo", "mcp"]`.

The tools are `triage`, `outline`, `units`, `locate`, `search`, `expand`, and `index`. To make agents use them first, add this to the repository's `AGENTS.md` or `CLAUDE.md`:

```markdown
## Finding code
Before reading files, call graf `triage` with the task text. It lists likely files and the likely
functions in full. Use `outline` to see a file's functions before opening it, and read only the
line ranges you need. Use `units` for follow-up questions instead of reading whole files.
```

### Status of the evidence

- **Offline localization:** current `triage` performance against the pinned Graphify baseline is unmeasured for this implementation batch. The pinned Graphify t6 binary remains unchanged; prior results are documented in the [localization report](research/localization-results.md).
- **Agent cost:** no graf orientation has saved agent cost against Graphify. In three preregistered batches on fresh natural tasks (t7–t9, Claude Haiku 5.5), graf's orientation cost the same as Graphify's or more, with non-inferior success; on tasks both always solved, costs were equal. Savings for the current CLI and MCP behavior are unmeasured. See the [agent results](research/agent-results.md).
- **Experimental flag:** `triage`, `units`, and `outline` stay experimental, and no agent-efficiency claim is made. See [AGENTS.md](AGENTS.md) for the rules.
- **Large repositories:** in a single-sample diagnostic, a warm `triage` answered in about half a second on Kubernetes and VS Code; see the [scale diagnostic](research/scale-results.md).

`graf units "<query>" --focus --budget 2000` is an additional experiment for
oversized functions. It returns a source window around the strongest matching
line when the first function cannot fit, with exact quoted file and line ranges.
The normal `units` command retains prefix truncation. This option has not passed
the product promotion or agent efficiency gates.

`graf units "request.js constructRequest()" --symbols --focus` additionally
uses file names, symbol names and exact query identifiers when ranking units.
It is an opt-in experiment with fixed weights; current default ranking and MCP
behavior are unchanged. Its retrieval and agent efficiency remain unproven.

Add `--retain-body` to the symbol experiment to keep candidates that pass either
the original body-score cutoff or the symbol-score cutoff. Symbol scores still
determine order, and the output budget can still exclude relevant units. This
alternative is experimental and has not passed the promotion gates.

`--symbols --focus --retain-body --file-diversity` additionally visits each
candidate file's best unit before repeated units from the same files. Eligibility
and the stop at the first nonfitting unit are unchanged. This ordering experiment
does not guarantee source-anchor coverage or agent token savings.

## Lower-level commands

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

Rerun `index` after changes. Unchanged files reuse their chunks; postings are rebuilt and the snapshot is saved atomically under `.graf/index.bin` (schema 2: excerpt text is not stored but read from the hash-checked source; an older `.graf/index.json` requires `index --rebuild`). Locate, search, and expansion reject stale selected files with a reindex instruction. New or unselected changed files require indexing to affect rankings. A moved checkout requires `index --rebuild`.

The scanner respects ignore rules within the root plus global ignore rules, excludes hidden/generated directories and symlinks, and indexes supported UTF-8 source/document files up to 1 MB. graf writes `.graf/.gitignore` so its files stay out of git. File metadata, postings, and function caches are stored locally in `.graf/`; ignore rules and extension filtering are not a secret detector. There is no compiler-resolved graph, semantic index, or generated summary; call edges come from tree-sitter names. See the [implementation contract](research/rust-cli.md).

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
.research/venv/bin/python benchmark/decision.py
.research/venv/bin/python benchmark/report_graphify_comparison.py
```

`decision.py` applies the preregistered [decision rule](research/protocol.md#decision-rule) from `benchmark/preregistration.json` and writes `results/decision.json`, workload-specific `decision-development.json` / `decision-heldout.json`, and immutable content-addressed reports in `results/decisions/`. It enforces the minimum query count and frozen per-arm pilot sizes for scored queries, and reports updated variance estimates for future planning; only an eligible frozen held-out workload can pass it. Both evaluators accept `--comparison PATH` so a new run can retain earlier raw results and scratch trees. `acceptance.py` writes a per-case [acceptance report](results/acceptance.json) and exits nonzero while promotion is blocked. These exploratory results cannot establish agent efficiency. See the [decision rule](research/protocol.md#decision-rule) for the remaining acceptance requirements.

The [paired report](research/graphify-comparison.md) is generated from `results/graphify-comparison.json`, with source receipts, commands, raw text, three timing samples per query arm/case, shared code-file discovery recall, recall among the first 5/10 delivered files, file counts, output tokens, index size, and build time. Query timings include fresh-process startup and index loading for both tools. Graphify uses its pinned serving API in a Python subprocess helper; graf uses the Rust CLI. Both `graf search` and `graf locate` are compared to Graphify using the same index and unchanged BM25 scores. The [fixed locate policy](benchmark/locate-experiment.json) is recorded before measurement. The Graphify configuration is code-only, so documentation quality and agent task savings remain unmeasured. Navigation hints and source excerpts are labeled separately; do not compare source-anchor recall against Graphify's file hints.

The source-line integrity diagnostic also runs the required paired Graphify comparison:

```sh
.research/venv/bin/python benchmark/rust_pilot.py
```

The source-line diagnostic writes `results/rust-pilot.json` and links its paired Graphify results receipt. It has broader source filters and slightly different identifier tokenization than the original Python pilot; it is not a controlled language-speed or Graphify comparison. Query timings include subprocess startup, index loading, tokenizer initialization, source checks, packing, and serialization.

## Research suite

This workspace also contains a cited scientific review, frozen inputs, 30 exploratory requests, retrieval prototypes, a pinned Graphify navigation baseline, and six synthetic coding-task fixtures.

Start with the [research findings](research/report.md), [scientific evidence](research/evidence.md), and [evaluation protocol](research/protocol.md). [Proposed next experiments](research/next-experiments.md) are not yet adopted protocol. Raw measurements live in `results/`; downloaded repositories, model weights, vector caches, source excerpts, and test workspaces stay under ignored `.research/`.

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

The [complete-task comparison setup](research/task-comparison.md) has a common JSONL tool broker for graf and Graphify, with source reads, fallback search, edits, and trusted verification. All six original fault fixtures have been checked through both backends. These checks restore known source; they are not agent repairs or inference-saving measurements. A broker-only Claude Code adapter subsequently completed three paired batches with a fixed Haiku model and a $0.50 estimated per-run cap; see the [agent results](research/agent-results.md).

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

Experimental `graf units "query" --symbols --focus --retain-body --file-diversity --linked` includes up to two directly called same-file definitions after each file's first seed. Helpers must have a positive body score; expansion is one hop, ordered by body score. Existing name-based call edges can be ambiguous. Source freshness, exact citations, the token budget and stop-on-first-nonfit still apply. This opt-in experiment is not promoted; file coverage does not establish answer accuracy or agent savings.

Experimental `graf units "query" --symbols --focus --retain-body --file-diversity --linked-tail` preserves the file-diverse output as an exact prefix, then appends up to two directly called same-file helpers per full first-selected seed if they fit unused space. Helpers need a positive body/call score; covered or oversized helpers are skipped. It conflicts with `--linked`. Source freshness and the total cl100k budget still apply. This experiment cannot displace baseline evidence, but may fail to recover helpers when space is scarce; it is not promoted and establishes no agent savings.

Experimental `graf evidence "query" --budget 2000` first runs locate at the requested budget and limit (default 20). It divides the source budget equally among returned hints and emits one exact source window around each hinted line, up to 24 physical lines starting eight before the anchor. Windows shrink by complete lines to fit their share; an anchor line that cannot fit is omitted. Spare shares are not redistributed. The total context, including quoted citations, stays within the cl100k budget; JSON metadata is extra. This opt-in experiment is not promoted and establishes no agent savings.

Experimental `graf evidence-units "query" --budget 2000` keeps evidence's locate ranking and equal per-file shares, but first tries complete structural units ranked with the existing body/call and symbol signals. It skips units that cannot fit the share or cannot be authenticated against physical source lines; if none fits, it uses the original source-window fallback. This changes which lines are delivered and may lose useful evidence. It remains experimental, with no proven agent savings.

Experimental `graf evidence-linked "query" --budget 2000` preserves the `evidence-units` output as a prefix and uses spare budget for complete units from up to four additional files connected to delivered files by a single indexed call edge. Additional units use existing body/call and symbol ranks, respect scope and kind filters, and carry authenticated range handles. This can add irrelevant material; it is an unproven experiment, with agent savings unmeasured.

Experimental `graf evidence-bundles "query" --budget 2000` uses the same structural prefix and linked-file ranking, but packs each linked function atomically with up to two direct helpers from that file, ranked by existing body/call scores. A bundle that cannot fit is skipped whole. This can trade file coverage for helper bodies; it is unproven and does not establish agent savings.

Both evidence commands return `range:ANCHOR:START:END` IDs in JSON. Pass the full ID to `graf expand` to read the delivered physical range, or widen from its boundaries with `--before` and `--after`. The anchor resolves an indexed file snapshot; stale sources and invalid ranges are rejected. Existing search and locate IDs keep their original behavior. Range IDs are metadata and cost additional tokens beyond the context budget.

`graf evidence-coverage` is an opt-in ranking experiment: BM25 multiplied by
squared distinct IDF-weighted query coverage, followed by the existing structural
evidence packer. It is unproven; defaults and MCP behavior are unchanged.

`graf evidence-families` is an opt-in lexical expansion experiment. It groups
a few nearby indexed spellings with each query word before coverage ranking,
then uses the existing structural evidence packer. It remains unproven.

`graf evidence-passages` is an opt-in source-selection experiment. It keeps
family-ranked files, scans their physical 24-line passages using query rarity
and coverage, and trims the chosen passage to each file's budget share. It
can split functions and remains unproven; defaults are unchanged.

`graf evidence-complement` is an opt-in experiment that retains the complete
`evidence-families` context and uses spare budget for non-overlapping passages
containing query terms missing from each delivered file excerpt. Added passages
can split functions. No accepted efficiency or quality improvement is established.

`graf evidence-stratified` is an opt-in file-selection experiment. It interleaves
code, documentation and test files, starting with code and retaining family
relevance order within each type. Scores are reciprocal interleaved ranks;
stop policies apply to those scores. Structural source packing, file limits
and budgets stay fixed. Artifact types use path heuristics. No accepted quality
or efficiency improvement is established.

`graf evidence-fused` is an opt-in experiment combining family chunk file ranks
with cached function/symbol file ranks using equal reciprocal-rank weights
(offset 60), followed by code/docs/test interleave and structural source packing.
It has no model calls. Its promotion gate remains unpassed; development results
cannot establish agent savings or product superiority.

`graf evidence-referenced` is an opt-in experiment adding a third file-ranking
channel to fusion: explicit names from the top eight matching documentation
chunks resolve only to globally unique code-function definitions. Each seed scans
at most 4,096 characters and 32 distinct names; at most eight target files receive
a reciprocal-rank vote. Documentation seeds respect scope; output targets also
respect kind. Source packing is unchanged. Promotion and agent savings remain
unproven.

`graf evidence-rescue` is an opt-in experiment preserving the complete
`evidence-fused` output as a prefix. Spare budget can append whole functions
from up to four new files resolved through the same bounded, globally unique
documentation references as `evidence-referenced`. Non-fitting functions are
skipped. Existing citations and ordering stay intact; appended files can exceed
the base file limit by four. This experiment is not an accepted improvement.

`graf evidence-callees` is an opt-in experiment preserving `evidence-rescue`
as a prefix and using spare budget for up to four whole direct local callees
of fully delivered functions. Candidates are deduplicated, ranked by original-query
body BM25 with stable unit-order ties, and limited to the first 32 for fit checks.
Overlapping ranges and non-fitting functions are skipped. It does not expand
recursively or add files. This experiment is not an accepted improvement.

`graf evidence-selective` is an opt-in experiment preserving `evidence-rescue`
as a prefix and appending at most one whole direct local callee with positive
original-query body BM25. It uses the same local-edge and physical-source checks
as `evidence-callees`, but added context is limited to the smaller of 256 tokens
and 10% of the requested budget, measured on the combined context. It checks at
most 32 ranked candidates and skips functions that do not fit. This experiment
is not an accepted improvement.
