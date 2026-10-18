# Initial Rust implementation

The first implementation ships a local CLI for indexing code and repository documentation, finding source windows, and expanding selected windows. Rust is the chosen implementation language. The original research harness stays in Python. No measured advantage over Go or Graphify is asserted.

## Commands and delivery

| Command | Behavior |
| --- | --- |
| `index` | Scan supported files, hash source text, reuse unchanged chunks, rebuild postings, atomically replace `.graf/index.json` |
| `search QUERY` | BM25 rank, optional literal path prefix and code/docs/test filter, return budgeted source windows |
| `locate QUERY` | Return one compact source hint per file with an expansion ID, using the same BM25 scores |
| `expand ID` | Validate source hash, read the anchor window and requested neighboring lines |
| `map` | Page through paths, kinds, line counts, chunk counts, and hashes from the snapshot |
| `stats` | Return file, chunk, vocabulary, source-byte, and serialized-index counts |

All commands return JSON except `search --text`, `locate --text`, and `expand --text`. Errors go to stderr with exit code 1; argument parsing errors use Clap's exit code 2. Search success describes retrieval, not answer sufficiency. Every command accepts `--root`.

The `context` field contains quoted path headers, inclusive one-based line ranges, and source text. Excerpt metadata contains a full SHA-256 anchor ID and source hash; scores are BM25 ranking scores, not probabilities. `expand` retains the anchor ID while reporting the expanded range. Anchor IDs change when their file content or path changes.

`--budget` is an exact `cl100k_base` count of the rendered context, including citation headers and separators. Metadata is outside this budget. Text output adds no trailing newline. The limit is tokenizer-specific and does not promise the same count under another model's tokenizer. Windows are indivisible: a window that cannot fit is skipped, allowing a smaller lower-ranked window to fit. Expansion returns `budget_exhausted` if the complete requested range does not fit. No model calls compress or summarize the source.

Search scores only postings for query terms. It sorts positive candidates deterministically, then considers at most 100 candidates for packing. `--limit` bounds returned excerpts (default 20). No positive candidate yields `no_lexical_match`; candidates that cannot fit yield `budget_exhausted`. Neither status is semantic abstention. Empty queries are rejected.

## Experimental delivery arms

`search` and `locate` accept `--stop gap|floor` and `locate` accepts `--file-score damped`. These implement the preregistered candidate arms in [next-experiments.md](next-experiments.md), with constants fixed in `src/policy.rs` and `benchmark/preregistration.json`. The defaults (`--stop full`, `--file-score max`) are the accepted behavior and produce byte-identical output to the previous release. The arms are not accepted improvements; each must pass `benchmark/decision.py` on the frozen held-out workload first.

## Compact navigation

`locate` selects the best-scoring chunk per file from the existing BM25 ranking, retaining score/tie order. It considers at most 100 unique file candidates and returns up to `--limit` hints (default 20). Each hint includes the quoted path and anchor range, a compact expansion handle, and one source preview. Handles are the shortest globally unique prefix of the full content-derived SHA-256 ID, with a minimum of 12 hex characters. Uniqueness is checked against all indexed chunks, including those outside the request's scope or budget. The preview chooses the anchor line with the most distinct query terms, with ties in source order; it trims whitespace and caps the prefix at 160 Unicode characters. Truncation is explicit. These are source prefixes, not generated summaries or resolved symbol relationships.

`expand` accepts both full IDs and unique prefixes of at least 12 lowercase hex characters. It rejects ambiguous or unknown prefixes and validates the source hash before returning source text. Reindexing can invalidate or make an older prefix ambiguous; request a new hint in that case. Full IDs in source-search metadata remain unchanged. Compact handles reduce hint overhead; they do not establish better ranking or agent-task efficiency.

The text budget includes paths, IDs, preview text, and separators. JSON metadata remains additional. Use `expand ID` to retrieve the full anchor window and optional neighbors. A hint's range identifies an expansion anchor, not an assertion that all those source lines have been delivered. Both locate and expansion validate current source hashes. The fixed experiment policy is in [locate-experiment.json](../benchmark/locate-experiment.json).

The comparison retains full-excerpt search as a separate arm, and reports recall among the first 5/10 delivered unique files and delivered file counts alongside total code-file recall. These distinguish improved ordering from wider listing. This experiment reuses exploratory queries; held-out agent evaluation and total locate-plus-expand costs remain unmeasured. The [previous full-excerpt comparison](graphify-comparison-search-v1.md) is retained with its original data receipt.

## Index and freshness

The scanner stays within the specified root, honors `.gitignore`/`.ignore` there and global ignore settings, and does not load parent-directory ignore files. It excludes hidden entries, symlinks, known generated/dependency directories, unsupported extensions, NUL-containing or invalid UTF-8 files, and files larger than 1,000,000 bytes. Supported extensions are explicit in `src/lib.rs`; Rust, Go, Python, JS/TS, common other source languages, Markdown/reStructuredText, and textual configuration are included. No remote content is fetched.

Files are divided into nonoverlapping 24-line windows. Blank windows are omitted. Paths contribute terms to BM25 along with source text. Identifier tokenization splits CamelCase, acronym boundaries, punctuation, and underscores, retaining Unicode alphanumeric text and removing a fixed English stopword list. Parameters are `k1=1.2`, `b=0.75`. The index persists source windows and inverted postings in versioned JSON, bound to the canonical root. A malformed, incompatible, or moved index requires an explicit rebuild.

An update reads and hashes all eligible files, reuses unchanged chunks, removes deleted files, and rebuilds all postings. It is not a fully incremental inverted-index implementation. A temporary file is flushed and synced, then atomically replaces the previous snapshot. Indexing errors leave the previous snapshot available. Concurrent builds can replace each other; no daemon or writer coordination is implemented.

Locate, search, and expansion check the current full contents of selected files against their indexed hashes and reject missing, changed, or symlinked sources. This verifies evidence against the observed files; it is not a repository-wide transaction or a promise that a file cannot change after it was read. New files and changes in unselected files require indexing before they affect retrieval. Map and stats describe the saved snapshot.

## Deliberate limits and validation

The source kinds use path/extension heuristics. They are not semantic product-intent labels. Product documents and code remain separate quoted evidence; the CLI does not infer requirement satisfaction or documentation disagreement.

The index is loaded into memory on each invocation. JSON duplicates source windows and stores term postings; it favors inspectability for this initial version over minimum disk space. No Rust/Go performance comparison, agent-task token savings, or superiority over Graphify has been demonstrated. A persistent serving interface, compiler-aware symbols, selected dependency navigation, optional semantics, and task-aware allocation still need evaluation before adding them.

The Rust integration tests cover persisted and deterministic retrieval, filtering, exact token budgets with Unicode, no-match behavior, hash-checked expansion, updates/deletions, ignore rules, symlinks, and the JSON CLI. The Rust pilot additionally runs the 30 frozen exploratory queries at two budgets and verifies tokens and selected source ranges independently with Python. Labels remain author-selected and incomplete; recall is not agent repair success. The current three-arm comparison has 540 timing samples, and its saved-result audit verifies 3,100 locate hints and confirms that all 60 full-search outputs and the three serialized indexes match the prior control exactly.

## Required Graphify comparison

Use `benchmark/compare_graphify.py` for every comparative benchmark. It stages exactly the same filtered files for both tools and runs all frozen queries/budgets with alternating sequential order. Results are stored in `results/graphify-comparison.json`. Code-file hint recall is shared; source-anchor recall remains specific to source excerpts. The pinned code-only Graphify arm does not establish documentation-task quality, and neither arm establishes agent task savings. See [AGENTS.md](../AGENTS.md) for the standing requirement.

## Historical standalone Rust CLI pilot

These measurements predate the authored-build-script scanner fix discovered by the paired Graphify check. Current comparisons use `results/graphify-comparison.json`. The saved [Rust CLI run](../results/rust-pilot.json) validates 30 authored queries at two budgets, with three process invocations per case. Python independently checks all 60 rendered contexts against source lines and token counts. This is exploratory retrieval validation, not held-out agent-task success.

| Repository | Files | Chunks | Rebuild (ms) | No-op update (ms) | Index (MiB) |
| --- | ---: | ---: | ---: | ---: | ---: |
| cobra | 55 | 846 | 428.8 | 142.8 | 1.66 |
| flask | 206 | 1532 | 1797.4 | 965.1 | 3.17 |
| fastify | 353 | 4296 | 2107.7 | 329.8 | 7.75 |

| Context budget | Mean selected-anchor recall | All anchors present | Median CLI query (ms) |
| --- | ---: | ---: | ---: |
| 2000 | 55.6% | 7/27 | 198.2 |
| 8000 | 87.0% | 21/27 | 463.0 |

Times include process startup, JSON index loading, tokenizer initialization, source validation, and output serialization. OS caches are uncontrolled. Rebuilds discard the previous index chunks but do not flush OS caches. No-op updates still rescan/hash files and rebuild postings. Source filters and identifier tokenization differ from the Python pilot, so this is not a controlled language or Graphify comparison. All three unsupported questions still return lexical context; the tool does not implement semantic abstention.
