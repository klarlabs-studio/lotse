# Proposed next experiments

**Status:** the evaluation design below was adopted on 2026-10-18 as the [decision rule](protocol.md#decision-rule) and frozen in [preregistration.json](../benchmark/preregistration.json). The candidate interventions are planned experiments, not accepted changes. Evidence and limitations for each proposal are in [evidence.md](evidence.md#evidence-for-the-next-retrieval-decisions).

## Why the current gate cannot be passed by retrieval changes alone

The latest [acceptance report](../results/acceptance.json) blocks `search` on 53 of 60 cases and `locate` on 31 of 60. At 2K, every `search` case and 25 `locate` cases lose on delivered tokens. graf packs until the budget is full (median about 1,990 tokens), while Graphify's native output is shorter (median about 1,808). Better ranking cannot fix that: a packer that fills the budget always delivers more tokens than a shorter output. `locate` at 8K fails on 6 cases, 5 of them on recall among the first five files, which is an ordering problem.

The gate also has statistical limits. It requires zero regression on each of 60 cases, judged by incomplete, author-selected labels. The two budgets share their queries, so there are 30 independent units, and those 30 queries have already been used for tuning. No statistical procedure can confirm a zero difference, so a better system can fail through label noise alone, and passing would not establish superiority.

## Candidate interventions

All arms are implemented behind opt-in CLI flags and run as separate arms by `benchmark/compare_graphify.py` (preregistration version 5). Index-time arms (B2, C1–C3) run with `locate` only, because `search` fills the budget and cannot pass the token test whatever the analyzer. The primary alpha is split across the nine experimental arms. Test one factor at a time against current graf and pinned Graphify. Freeze every parameter below before any held-out run. The values come from the cited literature or are round defaults, not tuning on the 30 exploratory queries. Changing a value after seeing held-out results creates a new, separately labeled experiment.

### A. Stopping rule (affects delivered tokens)

| Arm | Rule | Source of the idea | Falsified when |
| --- | --- | --- | --- |
| A0 | Current: pack until the budget is full | — | — |
| A1 | Largest relative gap: over sorted candidate scores s₁…sₙ, cut at the largest (sᵢ − sᵢ₊₁)/s₁ among the first ⌈0.9n⌉ positions, with at least 3 results, then add 2 more; the budget remains a hard cap | Adaptive-k (Taguchi et al. 2025) | Quality is worse than A0 beyond the non-inferiority margin, or delivered tokens do not fall |
| A2 | Relative score floor: stop at the first candidate scoring below 0.5·s₁ | Score-distribution truncation (Arampatzis et al. 2009), simplified | As for A1 |

For `locate`, apply the rule to file scores. Report the cut position for every case. Adaptive-k kept about 70% of relevant passages on QA, so a recall loss is the expected risk.

### B. File ranking for `locate` (affects ordering)

| Arm | File score | Source of the idea | Falsified when |
| --- | --- | --- | --- |
| B0 | Current: best window score (max) | SweRank's max aggregation; Dai and Callan | — |
| B1 | Damped top three: s₁ + 0.5·s₂ + 0.25·s₃ | Akkalyoncu Yilmaz et al. 2019 | No gain in recall among the first five files, or the gain tracks file length |
| B2 | Path field: BM25F with path weight 3 and window text weight 1, frequencies combined before saturation | Robertson and Zaragoza 2009; BLUiR | No gain, or losses where query terms are generic path words |

B2 changes the index schema and requires a rebuild path.

### C. Tokenization (affects matching)

| Arm | Change | Source of the idea | Falsified when |
| --- | --- | --- | --- |
| C0 | Current list, including request-phrasing words such as `test` and `implementation` | — | — |
| C1 | A published general English stopword list, applied to queries and documents | BLUiR; Fan et al. 2023 | Quality is worse than C0 beyond the margin |
| C2 | No stopwords; BM25's IDF alone down-weights common terms | BLUiR | As for C1 |
| C3 | C1 plus the lowercased full compound identifier indexed alongside its parts | BLUiR | No gain, or document-length effects reduce quality |

## Evaluation design (adopted)

Adopted 2026-10-18. Parameters the proposal gave only as examples were fixed in `benchmark/preregistration.json` on the same day, before any held-out data existed.

1. **Split the queries.** The 30 existing queries become a development and pilot set. They are used only to estimate the variance of paired differences.
2. **Held-out set.** Use independently authored requests, frozen before any arm runs. Size the set from the pilot variances, with a floor of 70. The first pilot (`results/decision.json`, development workload) found per-query recall differences with standard deviations up to about 0.41, because most queries label one or two code files. At the preregistered 0.05 margin, that implies roughly 410–430 queries. Preregistration version 3 therefore widens the margin to 0.10, which implies about 105–110 queries; the held-out minimum is 110. These are normal-approximation planning estimates, not cited results. Treat budgets as a repeated factor, not as extra queries.
3. **Judgments.** Pool the union of the top ten files from every arm and Graphify. A judge who did not tune graf, and who does not see which system returned each file, labels the pool. Report the unjudged fraction.
4. **Primary metric and test.** Preregister one primary efficiency metric, for example delivered tokens at a stated budget. Test the per-query mean difference with a one-sided paired permutation test at α = 0.05.
5. **Quality guardrails.** Use non-inferiority tests with fixed margins on code-file recall and recall among the first five and ten files, with Holm correction across guardrails.
6. **No hidden losses.** Publish every case. Add a tail guardrail: at most a preset share of queries may suffer a severe loss, such as losing every labeled file. Report a confidence interval for that share.
7. **Latency.** Use 10–20 alternating fresh-process invocations per arm and case, with separate cold-cache and warm-cache conditions. Report the median with a bootstrap interval.
8. **Agent trials.** Run the same solver, model, and spend caps for every arm. Count every attempt. Use at least 20 tasks with at least 5 paired trials per task and arm. Report success with Wilson intervals, report pass^k, and require non-inferiority on success with superiority on total cost. Natural held-out issues are still required before any product claim.
9. **Comparators.** Run against pinned Graphify and the last accepted graf version, as AGENTS.md already requires.

The current design (30 tuned queries, 3 timing samples, 6 × 3 planned agent trials) remains useful for diagnostics, variance pilots, and detecting gross failures. It cannot support superiority, latency, or agent-efficiency claims.

## Decisions needed

1. Who authors and judges the held-out requests.
2. The agent solver, model, and spend caps.

## Implementation backlog

The current implementation batch adds budgeted outline pagination, MCP argument and frame limits, recovery for absent or corrupt indexes, and a three-OS Rust CI matrix. These are foundation work; they do not establish retrieval superiority or agent savings. The pinned Graphify baseline, frozen graf t6 binary, and frozen benchmark plans are unchanged. Local macOS checks have run; the Ubuntu and Windows matrix jobs have not yet run in CI. Both index load paths reject snapshots above 128 MiB with a resource-limit error, leaving them untouched rather than repeatedly rebuilding them.

Complete the robust foundation first, then work through these remaining areas:

1. **Robust foundation:** exercise CI on all three operating systems, validate index size limits against large repositories, and close recovery, bounded-output, and request-validation gaps.
2. **Incremental freshness:** unchanged source windows and postings are now reused for same-analyzer updates. Reduce repeated full scans while preserving correct detection of edits, renames, deletions, and ignore-rule changes; full scans and hashing still occur before each MCP tool call.
3. **Richer symbol resolution:** improve relationships across modules and resolve calls beyond the current parser's reliable cases.
4. **Smarter region packing:** compare ways to choose and pack evidence regions while keeping whole-entry boundaries, citations, and explicit budgets.
5. **Rust and parser coverage:** Rust function items, impl methods, and trait default methods now have function units. Extend syntax coverage, including multiline attributes and block documentation comments, with source-location correctness checks.
6. **Documentation and configuration retrieval:** retrieve relevant docs and configuration alongside code, and evaluate these tasks separately from code-only Graphify comparisons.
7. **Operational and release hardening:** improve packaging, compatibility, error reporting, upgrade behavior, and release checks.
8. **Fresh held-out retrieval evaluation:** preregister a new supported workload and decision plan before collecting data; compare against pinned Graphify and the last accepted graf version, if one exists.
9. **MCP agent benchmark:** measure complete natural tasks with matched solver, model, limits, full tool-payload and model-call accounting, and Graphify plus source reads under the same conditions.

Until the held-out retrieval and agent evaluations are complete, keep these changes experimental and describe current retrieval performance and agent savings as unmeasured. Do not interpret microbenchmarks or integrity checks as comparative efficiency evidence.

### Foundation validation (2026-10-22)

Three `gpt-6-luna` implementation agents completed the bounded outline, index recovery, and MCP resilience changes, followed by integration review. Local macOS validation passed: `cargo test --locked` (80 tests), `cargo fmt --check`, `cargo clippy --locked --all-targets -- -D warnings`, `cargo build --release --locked`, and `.research/venv/bin/python -m unittest discover -s benchmark -p 'test_*.py'` (140 tests). Coverage includes concurrent snapshot readers and writers, failed rebuild preservation, corrupt index recovery, malformed and oversized MCP frames, argument validation, and CLI/MCP outline continuation budgets. These checks establish correctness for their fixtures, not comparative efficiency. The system Python lacks the benchmark dependencies; the repository virtual environment is required locally.

CLI compatibility diagnostic: `.research/venv/bin/python benchmark/check_equivalence.py .research/bin/graf-t6 target/release/graf --corpora .research/corpora` passed 1,631/1,631 comparisons across Cobra, Flask, and Fastify, including normalized serialized indexes. Index bytes were unchanged (477,968; 930,492; 2,153,431 respectively). This checks existing outputs against frozen graf; it measures neither Graphify performance nor the new MCP behavior.

### Incremental derivation validation (2026-10-22)

The next implementation batch reuses unchanged source metadata and postings when the analyzer is unchanged, remaps retained chunk ordinals in one pass over previous postings, and tokenizes new or changed sources. Analyzer switches and explicit full rebuilds use fresh source text. Every eligible file is still read and hashed, the complete posting map is reconstructed, and changed snapshots are still written atomically in full; this is not an event-driven index. The persisted schema and query behavior are unchanged. Successful empty MCP tool results are now preserved verbatim instead of adding an unbudgeted `No matches.` message.

Local checks passed: 85 Rust tests, 140 Python integrity tests, formatting, clippy with warnings denied, locked release build, and `cargo +1.88 check --locked --all-targets`. New tests compare serialized indexes, exports, search, and locate outputs against full rebuilds after additions, edits, removals, renames, ignore changes, and analyzer switches, and exercise persistent MCP refresh through ordinal changes. Internal tests confirm that metadata and postings can be reused from loaded chunks with empty source text. These are correctness diagnostics; latency and agent savings for this batch have not been measured against Graphify, and no promotion decision changes.

The same frozen-graf CLI compatibility command above was rerun after this batch: all 1,631 comparisons passed, and index sizes remained identical across Cobra, Flask, and Fastify. The t6 binary and benchmark plans were retained unchanged.


### Rust parser validation (2026-10-22)

Rust files now use tree-sitter-rust 0.24.0 (exact dependency pin) for function units in experimental `units`, `triage`, and `outline`. Free functions, impl methods, trait default methods, generics, and async functions are covered. Nested functions stay within the outer function unit. Contiguous single-line outer attributes and `///` comments stay with the following function; broader attribute/comment attachment remains pending. Rust uses physical LF/CRLF rows, including when a string contains a Unicode line separator, both during parsing and cached source delivery. Previous unit-cache versions are invalidated. Ordinary call names are heuristic terminal names; this does not resolve modules, types, or macro expansion. Macro token trees remain opaque.

Local macOS validation passed: 89 Rust tests, 140 Python benchmark integrity tests, `cargo fmt --check`, `cargo clippy --locked --all-targets -- -D warnings`, `cargo build --release --locked`, and `cargo +1.88 check --locked --all-targets`. New fixtures cover Rust item extraction, exact line coverage, malformed syntax, outline pagination, stale pre-Rust cache invalidation, and cache refresh after edits. These are correctness diagnostics. Rust retrieval quality, comparative Graphify performance, and agent token savings remain unmeasured; the pinned baselines and preregistrations are unchanged.

Compatibility diagnostic: 22/22 exact stdout comparisons against frozen `.research/bin/graf-t6` passed on a shared temporary Go/Python/JavaScript/TypeScript fixture: `units` and `triage` for three queries at budgets 64, 500, and 2,000, plus legacy `outline` for all four files. This checks output compatibility only; it is not a paired Graphify evaluation.


### Codex benchmark adapter (2026-10-22)

The separately preregistered Codex subscription diagnostic uses GPT-6 Luna with low reasoning and a structured-action loop through the existing task broker. Claude plans and pinned backends remain available. This provides a route to execute workflow diagnostics while Claude usage is unavailable; complete agent-efficiency acceptance still requires the missing internal request accounting and adequately sized fresh held-out tasks. See [the protocol and failure accounting](agent-results.md#codex-subscription-diagnostic-2026-10-22). Run `.research/venv/bin/python benchmark/run_codex_batch.py --dry-run` to inspect the latest frozen diagnostic order; running without `--dry-run` consumes the existing Codex allowance and sends issue/source/tool payloads to Codex. The runner refuses implementation, catalog, backend or solver-version drift.


The first completed Codex pilot (`codex-d3`, one reused task × three arms) failed verification in every arm. See [results and accounting](agent-results.md#codex-subscription-diagnostic-2026-10-22). Do not scale this protocol yet: fresh CLI invocations repeatedly send full history, and the model still emits extra actions that the broker discards. Improve protocol adherence and context overhead in a separately frozen revision, validate successful repairs, then size a fresh held-out comparison. Lower token counts on failed repairs do not establish useful savings. The existing runner will skip completed trials; do not overwrite their IDs or artifacts.


### Native Codex follow-up (2026-10-22)

The native MCP adapter uses one CLI session per attempt and typed broker tools. The complete synthetic repair passed, all 152 Python tests passed, and native batch m2 completed all three arms with matching source manifests and broker/CLI logs. Every natural-task attempt still failed verification. See [native results](agent-results.md#native-codex-mcp-diagnostic-2026-10-22); no token-saving or promotion claim follows. The next solver experiment should validate successful natural repairs under a separately frozen configuration before scaling. Keep the existing cheap GPT-6 Luna model as the baseline when testing a changed reasoning setting, and compare every resulting graf attempt against pinned Graphify plus reads and search-only under equal limits. Do not retry these IDs or pool protocols. Inspect the completed plan with `.research/venv/bin/python benchmark/run_codex_mcp_batch.py --dry-run`.


The v34 `codex-m3` diagnostic changed only Luna reasoning effort to medium. Search-only passed the reused natural repair; graf and pinned code-only Graphify plus reads failed. All source manifests and native/broker logs matched, and no limit was reached. Graf found the target function but finished without editing; Graphify made an incomplete repair. See [m3 outcomes and accounting](agent-results.md#native-codex-mcp-diagnostic-2026-10-22). The single passing attempt validates fixture feasibility with this solver, not a reliable configuration or product advantage.

Before further model calls, freeze a separate protocol diagnostic addressing premature finish consistently across all arms, and preserve the current failed attempts. A potential change is to require a test call and expose its failure before allowing normal completion; any such policy must apply equally to graf, pinned Graphify plus source reads, and search-only, while preserving action/time/token limits and failed requests. Review the source/tool payloads for redundant context and task-instruction interference offline first. These are hypotheses, not accepted improvements. Do not scale to the held-out promotion workload until the solver workflow is stable across a separately frozen validation set. No additional model calls were made after m3.


The v35 `codex-m4` experiment implemented the optional, uniform test-before-finish rule and matching instructions without changing graf retrieval. All 154 Python tests passed. Graf passed the reused repair; pinned code-only Graphify plus reads and search-only failed. Each arm made nine native broker requests, all source manifests and logs matched, and no policy refusal or resource-limit stop occurred. See [recorded outcomes](agent-results.md#native-codex-mcp-diagnostic-2026-10-22) and [accounting](../results/codex-m4-accounting.json). The rule remains experimental: this single stochastic result cannot establish reliability, a policy effect, or superiority, and earlier opposite outcomes remain preserved.

Next, design a separate solver-validation set using several development tasks with a fixed paired repetition plan, equal policies and limits, and predefined criteria for protocol completion and behavioral success before any new data. Include pinned Graphify plus source reads and search-only for every graf case. A failed test followed by finish remains possible under this rule; investigate task difficulty, repair persistence and resource use rather than requiring success or excluding unsuccessful attempts. Retain failures and missing internal provider accounting. Only after solver validation should a separately sized, fresh held-out product comparison be considered. Do not promote m4 or silently retry its IDs.


### Development solver validation outcome (2026-10-23)

Preregistration v36 completed the planned 18 trials: three exposed development tasks, two repetitions each, and graf/pinned code-only Graphify plus source reads/search-only in adjacent shuffled groups. GPT-6 Luna medium, the m4 finish policy and per-attempt limits were fixed. All 158 Python tests and all three offline faulted/reference controls passed before model calls. Every source manifest and native/broker log matched; usage was recorded for all 18 CLI invocations.

Graf passed 3/6 repairs using 1,070,662 reported tokens; Graphify passed 4/6 using 907,362; search-only passed 2/6 using 633,940. All approaches failed both JavaScript repairs. On Go repetition 1 graf failed while both comparators passed. Eight observed per-attempt token thresholds were exceeded, one request was blocked at the action limit, and three agents stopped without finish and required parent grading. Both solver-readiness conditions failed. See [all paired cases](agent-results.md#development-solver-validation-2026-10-23) and [accounting](../results/codex-v1-accounting.json). No product change is promoted; agent savings remain unmeasured.

The next work should be offline failure diagnosis: determine which behavioral conditions the JavaScript edits missed, why agents stop despite actionable failing tests, and how much repeated source/test context contributes to input growth. Preserve these cases as development evidence. Propose a single bounded workflow/context change, cover its failure modes locally, and freeze a separate validation before further model calls. Keep identical solver policies and resource limits for graf, pinned Graphify plus reads, and search-only; do not loosen acceptance or hide failed attempts to obtain a pass. A fresh held-out product comparison remains premature. No model calls followed this completed validation.
### Offline audit and next bounded experiment (2026-10-23)

The [saved-artifact audit](../results/codex-v1-failure-audit.json) found no truncated failed-test replies: all 19 failed test outputs were complete. Eight failed test calls had no later successful edit. JavaScript repairs missed validation during request construction, before connection setup. Do not attribute these failures to hidden test feedback or claim repeated context explains provider token totals. See [the diagnosis](agent-results.md#offline-development-failure-audit-2026-10-23).

The implemented next experimental option is `show_action_budget`, shared across all arms and disabled by default. Replies report calls remaining after counting every failure/refusal, with a reminder to reserve test and finish. It addresses invisible action exhaustion, not the underlying semantic repair errors; whether it helps remains unmeasured. All 160 Python tests pass. No new model calls or comparative benchmarks were run for this change. Existing v36 trial results remain frozen and the runner refuses their stale implementation hashes.

Before testing the option, record a new preregistration version and immutable implementation snapshots with the same cheap Luna model, matched arm policies, source parity, task/repetition plan, limits and predefined readiness criteria. Preserve every loss and all added feedback payloads in usage accounting. Do not silently rerun v36 or promote this harness change as a graf performance improvement. A fresh held-out product comparison remains premature.

Preregistration v37 freezes `codex-v2` before model calls: the same three exposed tasks, two repetitions, adjacent arm order/seed, Luna medium, pinned backends, finish policy, 16-request action limit and 150,000/2,700,000 observed token thresholds as v36. Only `show_action_budget` and its matching initial instructions change. All three fresh faulted/reference controls passed and source manifests match v36. The same protocol and behavioral readiness criteria apply. Historical v36 is descriptive context, not a randomized policy control. Frozen implementation snapshots and commands are in `.research/evidence/codex-v2`; preserve all cases and stop under the recorded rule.

### Budget-feedback outcome and next work (2026-10-23)

The frozen v37 diagnostic stopped at its observed 2.7-million-token batch threshold after 13/18 attempts (2,767,997 reported tokens). Five Go cases remain missing. On the four complete three-arm groups, graf passed 1/4, pinned code-only Graphify plus reads 2/4, and search-only 2/4. Graf lost the first Python repetition to both comparators; all six JavaScript attempts failed. Both readiness conditions and both promotion gates are unpassed. Budget feedback reached every native tool reply and implementation/source integrity checks passed, but no efficiency or reliability gain is established. Keep the option disabled by default. See [every case and accounting](agent-results.md#budget-feedback-solver-diagnostic-2026-10-23).

Do not resume v37 or enlarge its budget. The next work should remain offline: inspect why the solver declares source-edit tools unavailable or stops with actionable failing tests, and trace source navigation to request construction for the JavaScript failure. Account for prompt/tool-definition overhead separately before proposing another model experiment; provider input totals alone cannot locate that overhead. Preserve all failures, missing cases and historical configurations. Any further model calls need a new frozen plan under the user's continuing cheap-model preference; a fresh held-out product comparison remains premature. No calls followed this batch stop.

### Offline context/capability follow-up (2026-10-23)

The [context audit](agent-results.md#offline-context-and-capability-audit-2026-10-23) shows the faulty request-construction conditional in tool replies for five of six JavaScript attempts in each batch. Incorrect repairs cannot be explained solely by absent navigation. Repeated units/read lines exist, but whole retrieval replies are not identical. Visible saved prompt/schema/payload counts do not reveal provider-internal replay or hidden prompt overhead. Do not claim a deduplication win or attribute the token gap without request-level evidence.

Actual startup/discovery logging is now implemented for future native broker sessions and covered by local tests; it does not change tool replies or consume actions. Historical tool availability remains reconstructed, not directly captured. All 164 Python tests pass. No model calls were made. Keep budget feedback experimental, preserve the v37 stop/missing cases, and do not rerun its IDs. A future solver experiment should isolate one hypothesis about use of available tools or behavioral feedback, capture the new discovery log, and freeze its plan before any calls. No product improvement is established by this instrumentation.

### Discovery validation follow-up (2026-10-23)

Discovery logging now has an enforced validity check for future native trials. A new recorded plan must enable `require_tool_discovery` and pin `benchmark/codex_discovery.py` alongside all other harness dependencies before calls. Advertised tools must match each arm's expected complete schemas; invalid discovery stops the run even if another resource error also occurred. The offline audit prefers actual captured schemas, preserves unknown counts as null and reconstructs historical schemas only from matching frozen dependencies. All 171 Python tests pass, and both historical inventories reproduce unchanged; see [integrity evidence](../results/codex-discovery-integrity.json). No model calls were made, and v37 must not resume.

This resolves discovery validation infrastructure, not the observed repair failures. A future separately frozen experiment should still isolate one behavioral hypothesis under equal graf, pinned code-only Graphify plus reads, and search-only conditions. Preserve complete usage and failed attempts; budget feedback remains experimental, readiness remains failed, and agent savings remain unmeasured.

### Current comparison checkpoint (2026-10-23)

The saved-evidence [progress report](../results/codex-graphify-progress.json) now presents graf and pinned code-only Graphify plus reads on exactly the same complete groups, retaining every missing/failed/protocol-invalid case. Graf is behind on verified repairs in both recent diagnostics: 3/6 versus 4/6 in complete v36, and 1/4 versus 2/4 in the complete groups of stopped v37. No new model calls were made. The status artifact links these latest results without replacing older historical records. Next prioritize a single uniform repair-completion hypothesis over additional discovery instrumentation or rerunning the unchanged solver. Keep all comparisons on the cheap model, preserve existing limits and pins, freeze before new calls, and require correctness alongside token use.

### Pinned Graphify source audit (2026-10-23)

Inspected the existing extracted source snapshot of Graphify `86c57be8204673c2a1b9849a3292a7dd3ab373d8` (package 0.9.92). All 95 Python files recorded in the saved comparison's baseline manifest match their prior SHA256 values. This directory is an archive snapshot inside graf, not a separate Git checkout; parent-repository `git rev-parse` is not Graphify provenance. The pinned baseline was not changed or replaced. [Audit receipts, source hashes and raw probe outputs](../results/graphify-source-audit-2026-10-23.json) preserve the inspection.

| Area | Pinned Graphify implementation | Current graf implementation | Implication to test |
| --- | --- | --- | --- |
| Ranking | `serve.py:312,575`: query cleanup; symbol-label exact/prefix/substring tiers; IDF; squared label-term coverage; source/attribute terms | `query.rs:75`, `units.rs:468`: chunk/unit BM25; default compound identifiers are split; no equivalent symbol-label scoring tier | Test symbol-field ranking separately, retaining natural-language and alias queries; exact-match bonuses can also favor collisions |
| Neighborhood selection | `serve.py:794,1065,1413`: gap-based seeds plus per-term coverage, label deduplication, depth traversal, hub suppression; heuristic relation-context filters | `units.rs:383,408`, `triage.rs:273`: name-linked call edges, one-hop best-neighbor score boost, score floor | Test diversified seeds and bounded relationship expansion rather than assuming deeper traversal helps |
| Symbol resolution | `symbol_resolution.py:239,328`: explicit Python import aliases, unique-candidate checks, inferred-edge metadata; additional language resolution in `extractors/resolution.py` | `units.rs:383`: link called terminal names to all same-named definitions when there are at most three; drop names above that ambiguity threshold | Import-aware resolution is a concrete accuracy gap; require collision/alias/receiver fixtures, explicit uncertainty and provenance |
| Delivered context | `serve.py:1120`: node labels, source locations, typed relationships, confidence and call-site locations | `units.rs:593`: ranked code bodies; call edges influence score but are not explained in output | Test compact, cited relationship hints followed by selective source reads; raw relationship counts do not prove correct edges |
| Budget semantics | `serve.py:1120,1250`: roughly three characters per token, seed preservation and complete-edge overshoot by design | `pack.rs`, `units.rs:593`: cl100k budget checks; first oversized unit may be prefix-truncated | Preserve graf's explicit tokenizer boundary; test useful evidence retained under strict caps, not merely shorter output |
| Native workflow | `serve.py:2200`: query, get-node, neighbors, shortest-path and other MCP tools; docs/semantic extraction exist outside code-only mode | `mcp.rs:30`: triage, outline, units, locate, search and expansion | Current native Codex trials use a restricted broker, not either full native product workflow; native-workflow and documentation performance remain unmeasured |
| Caching | `cache.py:23`: extraction schema/package/grammar namespaces and per-file content caching; graph server context cache | `triage.rs:84`: source-hash unit cache and cached BM25/edges; persistent MCP index | Graphify also caches; Rust and incremental indexing alone do not establish a comparative advantage |

The saved click trial graph contains 2,872 nodes and 6,029 stored edges across containment, calls, imports, references, inheritance and other relation types. These counts establish representational breadth only; they are not a relationship-accuracy evaluation. Graf already uses graph-like call propagation internally, so the architecture choice is not simply graph versus no graph. Resolution precision, evidence selection and the agent's use of that evidence are separate hypotheses. Source differences do not establish which caused the observed repair losses.

**Adapter parity failed in a local mechanical probe.** `benchmark/compare_graphify.py:130` loads `_read_graph(...).to_undirected()` and calls `_query_graph_text` at its default depth 3. Pinned Graphify's CLI loader (`cli.py:1598`) preserves each edge's `_src`/`_tgt` before traversal and explicitly queries at depth 2 (`cli.py:1642`). On an undirected two-node fixture storing `caller -> callee`, a query for `callee` makes our adapter render `callee --calls--> caller`; the pinned native CLI correctly renders `caller --calls--> callee`. The adapter also omits the CLI's graph-path header. The mismatch is in our baseline invocation, not evidence that Graphify's CLI reverses calls. Broader full-MCP parity was not evaluated.

Reproduce with `.research/venv/bin/python .research/evidence/graphify-source-audit-2026-10-23/reproduce.py`. This diagnostic intentionally exits 1 because native-CLI direction parity fails. It preserves the fixture, exact subprocess commands, stdout/stderr, source hashes and original stored graph counts. It invokes no model and is not a paired quality/performance benchmark, so no latency/token-saving claim follows and existing retrieval acceptance/decision outputs remain unchanged.

Before further model comparisons, resolve whether the comparator contract is native CLI or an explicitly configured library query, preserve edge direction appropriately, and freeze a separate adapter revision with parity fixtures. Do not silently alter the old baseline invocation or rerun old trial IDs. Historical verifier outcomes are still observed outcomes under their recorded adapter configuration; do not describe those outputs as proven native-CLI-equivalent. No evidence yet shows whether the mismatch affected any natural repair outcome. Keep all promotion gates unpassed. After parity, prioritize import-aware relationship resolution, then symbol-field ranking, then compact relationship context as separate hypotheses, with the same cheap solver/model and matched source/task/resource limits against pinned Graphify.

### Versioned native CLI adapter (2026-10-23)

Added `native-cli-v2` as an explicit comparator revision in `benchmark/graphify-adapters.json`. `compare_graphify.py` now constructs the pinned native CLI command directly (`python -m graphify query QUERY --graph PATH --budget BUDGET`), retaining its depth, graph header, direction markers, error exits and approximate budget behavior. No rendering or loading logic is copied. The default `library-v1` query path remains unchanged for historical reproduction, and results now identify the selected adapter. The baseline commit remains unchanged.

The [contract validation receipt](../results/graphify-adapter-v2-validation.json) preserves six fixtures, sequential commands, stdout/stderr, exit codes and hashes under `.research/evidence/graphify-adapter-v2-validation/`. Direction, preserved markers, depth 2, the alternate `edges` field, small-budget seed retention and no-match exit behavior pass. All 95 installed and extracted Python source hashes match the historical baseline. The adapter deliberately runs the exact native CLI command: byte parity is an invocation-contract check, supplemented by independent expected-direction/depth/exit assertions; it is not independent Graphify correctness validation. All 178 Python tests pass. Zero model calls were made.

The original failed adapter audit and historical comparisons remain intact. Comparative use of `--graphify-adapter native-cli-v2` is blocked unless a new preregistration records `graphify_adapter` with `id`, `driver_sha256` and `manifest_sha256` matching the current driver and adapter manifest. Version 37 remains unchanged and stopped. The agent broker still uses the historical adapter; a new agent experiment must explicitly wire and freeze the new route, discovery validator and all dependencies before calls. The native CLI timing includes its query bookkeeping as well as startup, imports, loading, query, rendering and stdout. Cache conditions still require recording on each comparative run.

No paired retrieval comparison was run, so existing acceptance and decision artifacts were not replaced. No accepted product change, agent savings or performance advantage follows. Next integrate the corrected route into a separately frozen agent plan before spending on models, then test import-aware resolution as a separate experimental feature with pinned Graphify and the required acceptance checks.

### Agent integration and separate v38 freeze (2026-10-23)

The corrected `native-cli-v2` route now serves both agent orientation and subsequent retrieve requests. Session metadata persists the adapter and rejects switches on reopen; metadata without this field retains `library-v1`. The native MCP subprocess receives the same explicit selection. Raw retrieval command receipts preserve return codes, stdout/stderr and elapsed samples. Native CLI exit 4 with the exact no-match sentinel is a normal empty search; unexpected exits remain infrastructure errors. Historical adapters and outputs are retained.

Preregistration v38 freezes `codex-v3` separately before solver calls, with mandatory discovery validation and current implementation hashes. It retains Luna medium, the same three exposed tasks, two repetitions, paired sequential order, frozen binaries, test-before-finish and action-budget feedback, 16 requests, 180-second process timeout, and observed 150K/2.7M token stop thresholds. These remain observed thresholds with possible overshoot. Corrected comparator routing and discovery validation change the protocol; do not infer a causal effect or pool with v1/v2. The full v37 file and stopped specification are preserved, including all missing cases; v37 is not resumed.

[Offline preflight receipt](../results/codex-v3-preflight.json): dry run and frozen hashes pass, 181 Python tests pass, and all three faulted/reference controls pass with unchanged source manifests and protected tests. The initial sandboxed Go reference failed because `httptest` could not bind localhost; that failure is preserved separately. Approved execution with local listener access passed all controls. Future runs require that same local test-server capability across every arm; do not narrow verifiers to bypass it. No model calls were made: all 18 v3 trials remain pending. No paired retrieval comparison was run, so acceptance/decision artifacts remain untouched and promotion remains unpassed. Next run only this separately frozen diagnostic, retain all payloads/failures and usage, stop at its original limits, then evaluate outcomes before changing retrieval features.

### Frozen native CLI comparison v38 / codex-v3 (2026-10-23)

Ran the separately frozen Luna medium diagnostic with pinned native CLI Graphify plus source reads, graf's unchanged pinned Rust binary and search-only. The batch stopped at its original observed 2,700,000-token threshold after 13/18 attempts (2,780,846 reported input-plus-output tokens, including 2,337,024 cached input). No retries, replacement tasks, budget increase or resumption. Thirteen CLI invocations and 164 broker calls are recorded; provider internal requests and subscription charges remain unknown.

Shared totals include only four complete three-arm task/repetition groups. All failures and eleven per-attempt observed-token-threshold overshoots remain included. Wall times are descriptive only: shared warm OS/dependency caches, uncontrolled provider caches, isolated workspaces/indexes; startup, orientation/indexing, tools and final verification included, workspace preparation excluded. Competitors ran sequentially. Code-only Graphify does not establish documentation-task performance. No accepted graf version exists for an additional accepted-version comparison.

| Shared metric | graf | Pinned native CLI Graphify + reads | Search-only |
| --- | ---: | ---: | ---: |
| Verified repairs | 2/4 | 3/4 | 2/4 |
| Reported tokens, including cached input | 730,347 | 1,083,118 | 876,732 |
| Native broker calls | 43 | 57 | 55 |
| CLI invocations | 4 | 4 | 4 |
| Descriptive wall seconds | 107.31 | 167.31 | 135.65 |
| Attempts over observed per-attempt token threshold | 3 | 4 | 4 |

Every planned case (P/F denotes trusted verification; tokens are reported input plus output):

| Task / repetition | graf | Graphify | Search-only |
| --- | --- | --- | --- |
| Python click / 1 | P; 181,693 | P; 311,030 | P; 199,025 |
| Python click / 2 | P; 200,254 | P; 253,361 | P; 208,841 |
| JavaScript undici / 1 | F; 204,262 | F; 280,530 | F; 216,404 |
| JavaScript undici / 2 | F; 144,138 | P; 238,197 | F; 252,462 |
| Go gin / 1 | Missing | Missing | Missing |
| Go gin / 2 | Missing | Missing | P; 90,649 (unmatched) |

Graf's matched token total is 32.6% lower, but it solved fewer repairs. This is not useful agent-savings or superiority evidence. The severe quality loss is JavaScript repetition 2: graf failed and Graphify passed. The successful Graphify attempt edited `lib/core/request.js` as well as dispatcher files; graf edited a later dispatcher path. This is observed behavior, not proof that the adapter correction or particular retrieved facts caused the difference. These development tasks and repeated attempts cannot pass held-out promotion, and historical batches use different configurations.

All completed attempts have provider usage records, a captured valid tools/list exchange and an agent-called finish. There were no blocked, policy-rejected or failed broker requests. All observed initial source manifests match the freeze, the implementation hashes match, and all 95 baseline Python source hashes match. Raw command receipts confirm both Graphify orientation and retrieval invoked the native CLI. These integrity checks pass; protocol readiness, behavioral readiness, product promotion and agent-efficiency acceptance remain **false**, due to missing trials, threshold errors and insufficient success. The batch runner's exit 0 is not acceptance.

[Matched comparison](../results/codex-v3-graphify-comparison.json), [accounting](../results/codex-v3-accounting.json), [stopped status and failed gates](../results/codex-v3-status.json), [failure audit](../results/codex-v3-failure-audit.json), [context inventory](../results/codex-v3-context-audit.json). Raw evidence, commands, full discovery/control payloads, per-query elapsed samples, verification outputs and 317 artifact hashes are preserved under `.research/evidence/codex-v3/` and the original trial directories. Twenty failed-test outputs were complete, not truncated; five failed tests had no subsequent edit. Context inventories are serialization diagnostics, not provider replay or savings measurements.

No paired retrieval-only comparison was run, so the existing retrieval acceptance/decision outputs remain unchanged and unpassed. v37 remains stopped and historical results remain separate. The corrected comparator infrastructure now has real run evidence; further unchanged-solver reruns should give way to a separately scoped experimental retrieval change, starting with import-aware resolution and evidence selection for the request-construction boundary. Any next experiment needs a new freeze, pinned Graphify comparison and preserved quality losses; no feature is promoted by this diagnostic.


### 2026-10-23: focused units v39 — implemented, unproven

Added the opt-in Rust CLI `graf units "query" --focus`: when the first ranked
function exceeds the budget, score its lines with BM25, choose the earliest best
match, retain up to 24 adjacent lines, and shrink around that anchor until the
quoted citation and complete source lines fit cl100k exactly. Unit ranking and
legacy prefix packing remain unchanged. It is not enabled in triage or MCP.
The legacy default text matches the preserved pre-experiment binary in all 60
query/budget cases. Synthetic tests exercise deep matches, exact ranges, empty
and tiny budgets; these are diagnostics, not comparative efficiency evidence.

Version 39 was frozen before comparative outputs, preserving v38 and its stopped
Codex batch. `benchmark/compare_graphify.py --graphify-adapter native-cli-v2
--repeats 1 --output results/focused-units-v39-comparison.json` ran all 30 exposed
development queries at 2K/8K, all 16 arms sequentially, using the same source
snapshots, tokenizer and budgets. Graphify remains pinned at
86c57be8204673c2a1b9849a3292a7dd3ab373d8 (code-only/no-clustering). The baseline
adapter differs from historical library-v1 runs; do not pool them or attribute
changed baseline scores to product changes. Fresh indexes and subprocesses used
shared warm/uncontrolled OS caches; 960 timing samples are descriptive only.

| Arm | Mean text tokens, 2K | Code-file hint recall, 2K | Mean text tokens, 8K | Code-file hint recall, 8K |
| --- | ---: | ---: | ---: | ---: |
| graf locate | 1,784.13 | 94.44% | 3,036.23 | 98.15% |
| Legacy units | 1,530.47 | 66.67% | 5,070.83 | 81.48% |
| Focused units | 1,528.67 | 66.67% | 5,092.53 | 81.48% |
| Native Graphify | 2,411.37 | 87.04% | 13,001.93 | 87.04% |

Recall applies to the 27 code-annotated queries; tokens average all 30. Native
Graphify exceeded the requested cl100k budget in 6/30 and 16/30 cases respectively;
outputs were preserved. These are not equal-realized-budget comparisons.
Graphify hints do not establish source-anchor recall or documentation performance.

The important negative finding: none of these 60 cases exercised the new
oversized-first-function fallback. Citation quoting changed token counts, and
one later packing boundary (cobra-05 at 2K) dropped a documentation file while
retaining the same code-file hints. This workload does not establish improved
deep-match visibility. Both unit arms have four severe losses against Graphify:
cobra-03, fastify-01, fastify-03 and fastify-04. Lower retrieval text use therefore
does not demonstrate useful savings. All 60 cases, lost/gained file hints and
shared metrics are in `results/focused-units-v39-report.json`.

Source/output auditing passed. Rust tests: 91; Python tests: 183; clippy and
release build passed. `acceptance.py` and `decision.py` both exited 1 and remain
not_passed; historical reports were preserved, and version-specific copies are
saved. Quality guardrails and tail-loss requirements fail; this exposed workload
cannot promote a product. The pilot still requires at least 110 newly frozen,
independently authored/judged scored queries for future evaluation. There were
no new model attempts; agent efficiency and charges remain unmeasured.

Existing undici repetition-2 receipts also show the faulty request-construction
gate absent from all five graf retrieval replies. That does not isolate prefix
truncation as the cause: the returned unit choices point to ranking as a separate
problem. Next work should target file/symbol-aware unit ranking with natural
long-function tasks, rather than assuming this fallback fixed that repair.

Evidence: `results/focused-units-v39-comparison.json`, its `-audit.json`,
`results/focused-units-v39-report.json`, `results/focused-units-v39-acceptance.json`,
`results/focused-units-v39-decision.json`, and `.research/evidence/focused-units-v39/`
(raw commands, outputs, source/binary hashes, freeze, compatibility and inspection).

### 2026-10-23: symbol-aware units v40 — experimental, gates unpassed

Added opt-in `graf units "query" --symbols --focus`. Ranking combines normalized
body BM25 with BM25 over cached function names/file paths (weight 0.5), an exact
case-sensitive identifier bonus (1.0), and an explicit component-aligned file
path bonus (2.0). Weights were fixed before comparison. Existing score-floor,
source freshness checks, complete-line packing and quoted citations remain in
use. Metadata scoring runs at query time and is included in elapsed samples.
Default CLI/MCP behavior is unchanged; all 900 historical graf output contexts
match v39. No accepted graf version exists. The v39 binary and reconstructed
source copies verified against every original frozen implementation hash are
preserved for reproducibility.

Preregistration v40 was frozen before new comparative outputs. Ran
`benchmark/compare_graphify.py --graphify-adapter native-cli-v2 --repeats 1
--output results/symbol-units-v40-comparison.json`: 30 exposed development
queries, 27 code-annotated, identical frozen sources, cl100k tokenizer and
requested 2K/8K budgets, 17 sequential arms, 1,020 timing samples. Graphify stays
pinned at 86c57be8204673c2a1b9849a3292a7dd3ab373d8, code-only/no-clustering.
Fresh indexes/prebuilt unit caches and fresh query subprocesses used shared
warm/uncontrolled OS caches; one fixed-order repeat supports no latency claim.

| Arm | Mean text tokens, 2K | Code-file hint recall, 2K | Mean text tokens, 8K | Code-file hint recall, 8K |
| --- | ---: | ---: | ---: | ---: |
| Focused units | 1,528.67 | 66.67% | 5,092.53 | 81.48% |
| Symbol-aware focused units | 1,306.97 | 79.63% | 3,110.60 | 85.19% |
| graf locate | 1,784.13 | 94.44% | 3,036.23 | 98.15% |
| Native Graphify | 2,411.37 | 87.04% | 13,001.93 | 87.04% |

Tokens average all 30; recall averages the 27 annotated code queries. Graphify
exceeds the requested cl100k budget in 6/30 cases at 2K and 16/30 at 8K; raw
outputs are retained. These are not equal-realized-budget superiority results.
Graphify hints do not establish source-anchor recall or documentation quality.

All 60 candidate contexts changed. Compared with focused units, six query/budget
cases gained code recall and two regressed: flask-01 and flask-02 at 8K each
fell from 1.0 to 0.5. Their output shrank to 552 and 227 tokens respectively;
shorter output can omit required files. All lost/gained paths and every shared
metric are retained in `results/symbol-units-v40-report.json`. One severe
zero-recall loss against Graphify remains: fastify-03. A post-run structural
audit found no oversized-first-function crop in these cases, so this run still
does not establish the focused fallback's effectiveness on natural deep matches.

`acceptance.py` and `decision.py` each exited 1, status **not_passed**, product
promotion and agent-efficiency acceptance **false**. The token primary test
passes descriptively, but code-recall guardrails at both budgets, recall@10 at
2K, and the severe-loss bound fail. This exposed development set cannot pass
held-out acceptance; the pilot requires at least 110 newly frozen independently
authored/judged scored queries. There were zero new model attempts; agent
savings remain unmeasured. No default promotion or Graphify superiority claim.

Validation passed: 94 Rust tests, 183 Python tests, formatting, clippy, release
build and source/output audit. Current gate aliases and version-specific gate
copies are retained. Evidence includes `results/symbol-units-v40-comparison.json`,
its `-audit.json`, `results/symbol-units-v40-report.json`,
`results/symbol-units-v40-acceptance.json`, `results/symbol-units-v40-decision.json`,
and `.research/evidence/symbol-units-v40/` (freeze, source copies, commands,
outputs, hashes, timing samples and structural inspection).

Next work should separately preregister a coverage-preserving selection
experiment: inspect how the normalized scores interact with the existing
relative floor before changing weights or routing. Preserve these two Flask
regressions as development diagnostics, and add natural long-function tasks
under a new freeze before any model batch. Do not tune or rerun v40 as held-out.

### 2026-10-23: retain-body v41 — coverage recovery, experimental

Added `graf units "query" --symbols --focus --retain-body`. v40's symbol bonuses
raise the best combined score, and therefore raise its relative eligibility
floor; this can exclude units that passed the original body/call floor. v41
takes the union of candidates passing either floor (each still 0.5), orders the
union using unchanged symbol scores, and packs with no second relative cutoff.
Finite budgets and stop-on-next-nonfitting-unit still limit delivered coverage.
Default CLI/MCP behavior and earlier experimental arms remain unchanged.

Before comparative outputs, froze v41 under
`results/preregistrations/v41-f18b11e56461bef91c0a1f35f23dff215066450d1fc075790ddba9400ea2b9d9.json`.
Preserved v40's binary and verified all saved source copies against its freeze.
Ran `benchmark/compare_graphify.py --graphify-adapter native-cli-v2 --repeats 1
--output results/retain-body-v41-comparison.json` on the same 30 exposed
development queries (27 code-annotated), frozen source sets, cl100k tokenizer,
requested 2K/8K budgets, 18 sequential arms and 1,080 timing samples. Graphify
remains pinned at 86c57be8204673c2a1b9849a3292a7dd3ab373d8, code-only/no-clustering.
Fresh indexes/prebuilt unit caches and fresh subprocesses used shared warm,
uncontrolled OS caches. One fixed-order repeat supports no latency inference.

| Arm | Mean text tokens, 2K | Code-file hint recall, 2K | Mean text tokens, 8K | Code-file hint recall, 8K |
| --- | ---: | ---: | ---: | ---: |
| Focused units | 1,528.67 | 66.67% | 5,092.53 | 81.48% |
| v40 symbol ranking | 1,306.97 | 79.63% | 3,110.60 | 85.19% |
| v41 retained body candidates | 1,665.53 | 83.33% | 5,414.07 | 88.89% |
| graf locate | 1,784.13 | 94.44% | 3,036.23 | 98.15% |
| Native Graphify | 2,411.37 | 87.04% | 13,001.93 | 87.04% |

Tokens average all 30 queries; recall averages 27 code-annotated queries.
Graphify exceeds the requested cl100k budget in 6/30 cases at 2K and 16/30 at
8K. These raw outputs remain intact; the table does not establish superiority
at equal realized budgets. Native hints do not establish source-anchor recall,
documentation performance or repair-task outcomes.

Against v40, 36 contexts changed; four query/budget cases gained code-file
recall and none lost it. flask-01 and flask-02 each recovered from 0.5 to 1.0 at
both budgets, resolving the two 8K regressions against focused-only units.
No previously cited paths were removed in any of the 60 cases versus v40.
Against focused-only units, eight cases gained code recall and none lost it.
Recovery costs more retrieval text, especially at 8K. All per-case metrics,
lost/gained paths and unchanged cases are in `results/retain-body-v41-report.json`.
Against Graphify, cobra-07 at 2K still has partial recall (0.5 vs 1.0), and
fastify-03 at both budgets remains a severe loss (0 vs 1.0). Aggregate recall
at 8K must not hide these losses.

All 960 existing graf output contexts match v40 exactly. All 60 native Graphify
contexts match after normalizing only their run-specific scratch graph path in
the header; original raw outputs and the compatibility receipt are preserved.
No accepted graf baseline exists. Source/output auditing passed. Validation:
95 Rust tests, 183 Python tests, formatting, clippy and release build passed.
The initial Python check caught the new arm before its preregistration entry
was written; that failed log is preserved, and the synchronized check passed.

`acceptance.py` and `decision.py` each exited 1: **not_passed**; product promotion
and agent-efficiency acceptance remain **false**. The primary paired token test
has p=0.003410, above the corrected alpha=0.003333 (15 experiments), so there is
no significant primary reduction under the frozen rule. Code-file recall at
2K does not establish non-inferiority, and the severe-loss bound fails. The
exposed development workload and insufficient sample size also block promotion.
The pilot still requires at least 110 newly frozen independently authored/judged
scored queries. No model attempts were made; agent savings remain unmeasured.
The stopped Codex batches remain unchanged.

Evidence: `results/retain-body-v41-comparison.json`, its `-audit.json`,
`results/retain-body-v41-report.json`, `results/retain-body-v41-acceptance.json`,
`results/retain-body-v41-decision.json`, current gate aliases and
`.research/evidence/retain-body-v41/` (freeze, source copies, commands, raw output,
all elapsed samples, test logs, compatibility receipt and artifact hashes).

Next work should isolate the remaining fastify-03 miss and how the first
nonfitting unit prevents later candidates from being delivered. Any alternative
packing, file diversity or routing must receive a new freeze and comparison;
v41 is not promoted and should not be retuned after its outcomes.

### 2026-10-23: file-diverse packing v42 — file gain, source anchors still missing

Added opt-in `graf units "query" --symbols --focus --retain-body --file-diversity`.
Eligibility and scores remain v41's body/symbol union. In descending score order,
visit the first unit from each file, followed by repeated units; preserve original
index order for ties. Positive ordinal keys encode that order for the unchanged
focused packer. Full fitting units, quoted exact ranges, oversized-first fallback
and stop-on-next-nonfitting-unit remain unchanged. Default behavior and all older
experiments remain available.

A pre-change trace of exposed fastify-03 found `lib/decorate.js:137-142`
(`decorateRequest`) eligible at rank 62, while the annotated
`checkReferenceType` function at lines 69-73 ranked 643 and failed both floors.
Many earlier units belonged to repeated test files. This motivates testing file
coverage, but ordering cannot restore an ineligible exact source anchor. The
trace source, commands, raw TSV and initial failed compiler diagnostic are saved
under `.research/evidence/packing-v42/`. The compiler diagnostic was corrected by
using a dependency-free TSV trace; product validation subsequently passed.

Froze v42 before comparative outputs:
`results/preregistrations/v42-60d3014ca627f4a8607922992fc840ac8154ebbfb4ff426e02203eb4d879a023.json`.
Preserved the v41 binary and verified its saved implementation hashes. Ran
`benchmark/compare_graphify.py --graphify-adapter native-cli-v2 --repeats 1
--output results/packing-v42-comparison.json`: 30 exposed development queries,
27 code-annotated, identical frozen source sets, cl100k tokenizer, requested 2K/8K
budgets, 19 sequential arms, 1,140 elapsed samples. Graphify remains pinned at
86c57be8204673c2a1b9849a3292a7dd3ab373d8, code-only/no-clustering. Fresh indexes
and prebuilt unit caches, fresh subprocesses, fixed order and shared warm,
uncontrolled OS caches; one repeat supports no latency inference.

| Arm | Mean text tokens, 2K | Code-file hint recall, 2K | Mean text tokens, 8K | Code-file hint recall, 8K |
| --- | ---: | ---: | ---: | ---: |
| v40 symbol ranking | 1,306.97 | 79.63% | 3,110.60 | 85.19% |
| v41 retained body candidates | 1,665.53 | 83.33% | 5,414.07 | 88.89% |
| v42 file-diverse packing | 1,622.00 | 83.33% | 5,121.10 | 92.59% |
| graf locate | 1,784.13 | 94.44% | 3,036.23 | 98.15% |
| Native Graphify | 2,410.37 | 87.04% | 13,000.93 | 87.04% |

Tokens average all 30 queries; recall averages the 27 code-annotated queries.
Graphify's run-specific graph-path header uses one fewer cl100k token than v41;
all 60 contexts otherwise match after replacing only that scratch path prefix.
Raw outputs remain intact. Graphify exceeds the requested budget in 6/30 cases
at 2K and 16/30 at 8K. No equal-realized-budget superiority claim follows.
Code-only Graphify hints do not establish source-anchor recall, documentation
performance or repair-task outcomes.

Against v41, 55 contexts changed; fastify-03 at 8K gained code-file recall from
0 to 1.0, and no scored case lost code-file recall. No previously cited paths
were removed in any of the 60 cases. Against focused-only units, nine cases
improved and none regressed. All per-case metrics and lost/gained file hints
are preserved in `results/packing-v42-report.json`. At 2K, cobra-07 remains a
partial loss against Graphify (0.5 vs 1.0), and fastify-03 remains a severe loss
(0 vs 1.0). At 8K, cobra-07 is full recall and fastify-03 now names the right
file, but only returns lines 137-142. Neither annotated checkReferenceType line
69 nor documentation line 183 is delivered at either budget. This is a file
navigation gain, not established answer-evidence recovery.

All 1,020 existing graf contexts match v41 exactly. Source/output audit passed.
Validation passed: 97 Rust tests, 183 Python tests, formatting, clippy and release
build. No accepted graf baseline exists; versioned gate reports and current gate
aliases are retained. `acceptance.py` and `decision.py` each exited 1:
**not_passed**, product promotion and agent-efficiency acceptance **false**.
The primary paired token test passes (p=0.001980, corrected alpha=0.003125 across
16 experiments), but code-file recall at 2K does not establish non-inferiority,
and the severe-loss bound fails. This development workload and sample size also
block promotion; the pilot still requires at least 110 newly frozen,
independently authored/judged scored queries. No new model attempts; agent
savings remain unmeasured, and stopped Codex batches remain unchanged.

Evidence: `results/packing-v42-comparison.json`, its `-audit.json`,
`results/packing-v42-report.json`, `results/packing-v42-acceptance.json`,
`results/packing-v42-decision.json`, and `.research/evidence/packing-v42/` (freeze,
implementation, exact commands, raw outputs, timing samples, trace, tests,
compatibility checks and artifact hashes).

Next work should address evidence selection within a discovered file, or
explicitly linked definitions, under a separately frozen experiment. Keep file
hint metrics and source-anchor outcomes distinct; do not treat the recovered
filename as proof that a solver can answer or repair the task. No default
promotion or held-out qualification occurred.

### 2026-10-23: bounded same-file callee evidence v43 — recovery with a documentation loss

Implemented opt-in `graf units "query" --symbols --focus --retain-body
--file-diversity --linked`. V41's body/symbol union still selects seeds and v42's
first-per-file ordering selects each file's first seed. After each seed, include
at most two directly called same-file definitions with a positive body/call
score, descending body/call score and original index ties. Use existing directed,
name-linked edges; no recursive expansion. Then visit remaining eligible units
in their original ranking, deduplicating all units. Positive ordinal keys encode
this order for the unchanged focused packer, with exact source ranges, source
freshness checks, oversized-first fallback and stop on next nonfitting unit.
Existing call edges can be ambiguous. No default or MCP behavior changed.

Froze algorithm, workload, 17-arm multiplicity family, resources, source/binary
hashes and timing scope before comparative data:
`results/preregistrations/v43-5b69a325b7cd074fb1e9c4c7328337bdf43db1a8d4044837b3a2dc3af28bd9f1.json`.
Preserved v42's binary and verified saved implementation hashes. Ran the pinned
Graphify comparison through `benchmark/compare_graphify.py --graphify-adapter
native-cli-v2 --repeats 1 --output results/callees-v43-comparison.json`: 30 exposed
development queries (27 code-scored), identical frozen sources, cl100k tokenizer,
requested 2K/8K budgets, 20 sequential arms and 1,200 timing samples. Graphify
remains pinned at 86c57be8204673c2a1b9849a3292a7dd3ab373d8, code-only/no-clustering.
Fresh indexes and prebuilt unit caches, fresh subprocesses, fixed arm order and
shared warm/uncontrolled OS caches; one repeat supports no latency inference.

| Arm | Mean text tokens, 2K | Code-file hint recall, 2K | Mean text tokens, 8K | Code-file hint recall, 8K |
| --- | ---: | ---: | ---: | ---: |
| v41 retained body candidates | 1,665.53 | 83.33% | 5,414.07 | 88.89% |
| v42 file-diverse packing | 1,622.00 | 83.33% | 5,121.10 | 92.59% |
| v43 bounded callee evidence | 1,699.53 | 83.33% | 5,047.00 | 92.59% |
| graf locate | 1,784.13 | 94.44% | 3,036.23 | 98.15% |
| Native Graphify | 2,411.37 | 87.04% | 13,001.93 | 87.04% |

Tokens average all 30 queries; shared recall averages 27 code-annotated queries.
Graphify's run-specific scratch-path header is one cl100k token longer than v42;
all 60 native contexts match after replacing only that prefix. Raw outputs are
unchanged. Graphify overshoots the requested budget in 6/30 cases at 2K and 16/30
at 8K. Requested budgets match, realized budgets do not; no equal-realized-budget
superiority or documentation-task claim follows.

Against v42, 59 contexts change, with zero scored file-recall gains or losses.
Eleven cases lose other cited paths; every lost/gained path is preserved in the
report. Source evidence changes despite unchanged shared recall. A descriptive
check of the exposed author-selected anchors (graf excerpts only, never
Graphify hints) finds these three changes versus v42:

- flask-05 at 2K: restores `src/flask/app.py:1416`, anchor recall 0 to 0.5.
- fastify-03 at 8K: restores `lib/decorate.js:69` (`checkReferenceType`), anchor
  recall 0 to 0.5. Its documentation anchor remains absent. At 2K neither the
  code file nor either answer anchor is delivered.
- fastify-09 at 8K: loses `docs/Reference/Server.md:293` while keeping
  `lib/route.js:633`, anchor recall 1.0 to 0.5.

Mean exploratory anchor recall changes 50.00% to 51.85% at 2K and stays 73.46%
at 8K; this metric uses all annotated code/documentation anchors and is not a
shared Graphify comparison or a solver outcome. No task-accuracy claim follows.
File-hint losses against Graphify remain cobra-07 at 2K (0.5 versus 1) and severe
fastify-03 at 2K (0 versus 1). All cases are retained in the report.

All 1,080 previous graf contexts match v42 byte-for-byte. Source/output audit
passed: 60 cases, 1,200 timing samples and 25,152 verified locate hints. Validation
passed: 99 Rust tests, 183 Python tests, formatting, clippy and release build.
The initial fixture argument indexing compile error and interim preregistration
arm mismatch were corrected before freezing; notes and initial Python failure
log are retained. No accepted graf version exists.

Both `acceptance.py` and `decision.py` exited 1: **not_passed**, product promotion
and agent-efficiency acceptance **false**. The primary paired token diagnostic
fails (p=0.005860 versus corrected alpha=0.002941 across 17 experiments); code-file
recall at 2K does not establish non-inferiority, and the severe-loss confidence
bound fails. Development exposure and sample size also block promotion. The
pilot still requires at least 110 independently authored/judged, newly frozen,
scored held-out queries. No new model calls; agent savings remain unmeasured.
Stopped model batches and older preregistrations/results remain intact.

Evidence: `results/callees-v43-comparison.json`, its `-audit.json`,
`results/callees-v43-report.json`, versioned acceptance/decision reports, current
gate aliases, `results/status.json` and `.research/evidence/callees-v43/` (freeze,
source copies, v42-to-v43 patch, commands, raw stdout/stderr, timing samples,
validation logs and artifact hashes).

The result supports investigating bounded evidence expansion, with explicit
protection for previously delivered documentation and broader file coverage.
Any change to evidence allocation or routing needs a separately frozen version;
v43 is unproven and must not replace the default. Restore source evidence without
hiding losses behind file recall before spending on another solver batch.

### 2026-10-23: append-only helper evidence v44 — preserves baseline, limited recovery

Added opt-in `graf units "query" --symbols --focus --retain-body
--file-diversity --linked-tail`, mutually exclusive with v43's `--linked`.
Run v42 file-diverse packing at the full budget first, retaining its exact text
prefix and unit metadata. For the first selected unit per file, require a full
indexed-unit match; visit at most two same-file directly called definitions with
positive body/call scores, descending body/call score and original index ties.
Use existing directed name-linked edges, without recursive expansion. Hydrate
source under the existing freshness checks. Skip already-covered helpers and
complete helpers that cannot fit the total cl100k budget; continue to later
helpers. Append only, with exact quoted source ranges and no helper truncation.
The baseline packer's stop-on-next-nonfit and first oversized fallback stay
unchanged. Existing edges may be ambiguous. Default and MCP behavior are unchanged.

The structural guarantee is evidence preservation versus v42, not versus v43,
which used a different allocation and could recover other evidence. Additional
tokens must still justify themselves through measured outcomes; safe appending
alone is not a comparative efficiency win.

Froze v44 before comparative data:
`results/preregistrations/v44-866872622cf73f1141e6a128b526e6b7378e48c75b94150ba89cecf3d50fb5ad.json`.
Verified v43's saved implementation hashes and preserved its binary. Ran
`benchmark/compare_graphify.py --graphify-adapter native-cli-v2 --repeats 1
--output results/tail-v44-comparison.json`: 30 exposed development queries,
27 code-scored, identical frozen sources, cl100k tokenizer, requested 2K/8K
budgets, 21 sequential arms, 1,260 timing samples. Graphify remains pinned at
86c57be8204673c2a1b9849a3292a7dd3ab373d8, code-only/no-clustering. Fresh indexes
and prebuilt unit caches, fresh subprocesses, fixed arm order and shared warm,
uncontrolled OS caches; one repeat supports no latency inference. Primary
multiplicity is now 18 experimental arms; no prior results were rewritten.

| Arm | Mean text tokens, 2K | Code-file hint recall, 2K | Mean text tokens, 8K | Code-file hint recall, 8K |
| --- | ---: | ---: | ---: | ---: |
| v41 retained body candidates | 1,665.53 | 83.33% | 5,414.07 | 88.89% |
| v42 file-diverse packing | 1,622.00 | 83.33% | 5,121.10 | 92.59% |
| v43 inline callee evidence | 1,699.53 | 83.33% | 5,047.00 | 92.59% |
| v44 append-only callee evidence | 1,729.23 | 83.33% | 5,257.30 | 92.59% |
| graf locate | 1,784.13 | 94.44% | 3,036.23 | 98.15% |
| Native Graphify | 2,410.37 | 87.04% | 13,000.93 | 87.04% |

Tokens average all 30 queries; shared recall averages 27 code-annotated queries.
Graphify's run-specific scratch-path header uses one fewer cl100k token than
v43. All 60 native contexts match after replacing only that prefix; raw outputs
are retained. Graphify exceeds requested budgets in 6/30 cases at 2K and 16/30
at 8K. Requested budgets match, realized budgets do not; no equal-realized-budget
superiority claim follows. Code-only hints do not establish Graphify documentation
performance, source-anchor delivery or solver outcomes.

All 60 contexts preserve v42's exact prefix and every cited path. Forty-one gain
additional text. Every scored query retains v42/v43 file recall. Against v42,
the exposed author-selected source-anchor diagnostic improves only fastify-03 at
8K, from 0 to 0.5: `lib/decorate.js:69` (`checkReferenceType`) is delivered, but
`docs/Reference/Decorators.md:183` remains absent. There are zero anchor losses
versus v42. Mean exploratory anchor recall stays 50.00% at 2K and increases
73.46% to 75.31% at 8K. These are graf exact-range diagnostics across annotated
code/documentation, not shared Graphify metrics or task outcomes.

Against v43, v44 restores fastify-09 at 8K from 0.5 to 1.0, retaining both
`docs/Reference/Server.md:293` and `lib/route.js:633`. It loses v43's flask-05 at
2K code-anchor gain (`src/flask/app.py:1416`, 0.5 to 0). Fastify-03 at 2K still
misses the code file and both annotated anchors. This is a tradeoff versus v43,
not an across-the-board improvement. The full report preserves every per-case
metric, lost/gained path and anchor. Shared file-hint losses against Graphify
remain cobra-07 at 2K (0.5 versus 1) and severe fastify-03 at 2K (0 versus 1).

All 1,140 prior graf contexts match v43 exactly. Source/output audit passed:
60 cases, 1,260 timing samples and 25,152 verified locate hints. Validation passed:
101 Rust tests, 183 Python tests, formatting, clippy and release build. The initial
CLI fixture already selected its helper under the existing propagation cutoff;
the fixture was corrected before freezing to exercise recovery after a blocking
unit. The implementation and algorithm were unchanged; initial failure retained.
No accepted graf baseline exists.

Both acceptance and decision commands exited 1: **not_passed**, product promotion
and agent-efficiency acceptance **false**. Primary token reduction is not
significant (p=0.009480, corrected alpha=0.002778 for 18 experiments); 2K code-file
recall does not establish non-inferiority and the severe-loss confidence bound
fails. The development workload and sample size also block promotion. Future
held-out planning still requires at least 110 independently authored/judged,
newly frozen scored queries. No new model calls; agent savings remain unmeasured.
Older experiments and stopped model batches remain unchanged.

Evidence: `results/tail-v44-comparison.json`, its `-audit.json`,
`results/tail-v44-report.json`, versioned acceptance and decision reports, current
gate aliases, `results/status.json` and `.research/evidence/tail-v44/` (freeze,
source copies, v43-to-v44 patch, exact commands, raw outputs and all timing
samples, validation, prefix checks and artifact hashes).

Next work should address 2K file discovery and the lost flask-05 evidence rather
than assuming safe appending solves retrieval. Consider a separately frozen
locate-plus-bounded-source strategy, since locate has higher shared file recall
on this exposed workload; it must be compared under matched budgets and source
reads before any agent-efficiency claim. Keep v44 experimental and do not spend
on another solver batch solely because one known helper is now delivered.


## v45 locate-first source windows: configuration deviation (2026-10-23)

Implemented opt-in `graf evidence`: unchanged locate ranking followed by equal
source-window budget shares, up to 24 complete physical lines beginning eight
before each hinted line, trimming farther endpoints until each share fits.
Freshness checks, exact quoted citations and a total cl100k cap remain enforced.
No default or MCP behavior changed, and no model calls were made.

The immutable v45 archive is
`results/preregistrations/v45-671b0a8696b0a09bde3768db6bc03e7429bac632486387bd221c88a7136aa2b4.json`.
After the run, command inspection found that the common benchmark driver passed
`--limit 1000`, overriding the described 20-file default. The driver bytes and
actual commands were retained, but prose and invocation disagree. Mark this run
**experimental_configuration_deviation** and exclude it from confirmatory evidence
or promotion. The explicit 20-file replay requires a newly frozen version; do
not rewrite v45's archive or relabel these outputs as that replay.

For transparency, the actual 1000-limit run's descriptive code-file hint recall
was 92.59% / 98.15% at 2K / 8K, versus v44 83.33% / 92.59% and pinned native
code-only Graphify 87.04% / 87.04%. Mean emitted text tokens were 1,639.47 /
6,824.50, versus v44 1,729.23 / 5,257.30 and Graphify 2,410.37 / 13,000.93.
Graphify overshot requested cl100k budgets in 6 / 30 and 16 / 30 cases; graf had
zero overshoots. Requested and realized budgets differ; no equal-realized-budget
superiority claim. At 2K, four code-file recall gains versus v44 (cobra-07,
flask-03, fastify-05, fastify-08) and one loss (fastify-07); at 8K, two gains
(flask-03, fastify-08) and no losses. fastify-03 remains a severe 2K loss versus
Graphify. The full report preserves every case and gained/lost path.

Exact source-anchor coverage is substantially worse: 25.31% / 37.65% versus
v44 50.00% / 75.31%, with five case gains and 29 losses across both budgets.
These exposed author-selected code/documentation anchors are graf diagnostics,
not shared Graphify metrics or solver outcomes. fastify-03 at 8K includes the
code file but misses `checkReferenceType` line 69 and documentation line 183.
The short-window approach cannot be promoted from better file discovery alone.

Both gate commands exited 1, **not_passed**, product promotion and agent-efficiency
acceptance false. The descriptive primary p=0.000310 (corrected alpha=0.002632,
19 experiments) passes, but top-5 quality non-inferiority fails at both budgets,
the severe-loss bound fails, and development/sample-size restrictions apply.
Fresh independently authored/judged frozen held-out planning still requires at
least 110 scored queries. Agent savings remain unmeasured; zero new model calls.

Source/output audit passed for 60 cases, 1,320 timings and 25,152 locate hints.
All 1,200 previous graf contexts match v44 exactly; all 60 Graphify contexts
match after scratch-header path normalization. Validation passed: 103 Rust tests,
184 Python tests, formatting, clippy and release build. Artifacts:
`results/evidence-v45-{comparison,comparison-audit,report,acceptance,decision}.json`
and `.research/evidence/evidence-v45/` (freeze, source copies, raw output, commands,
all timings, validation, source-anchor losses and hashes). No accepted graf
baseline exists. Preserve v45 and freeze an explicit 20-file replay next.


## v46 explicit 20-file source-window replay (2026-10-23)

Corrected the benchmark command to explicitly pass `--limit 20` for
`graf_evidence`, matching the original plan. The Rust `graf evidence` algorithm
is unchanged: locate first, equal per-returned-file token shares, exact quoted
source ranges of at most 24 physical lines starting eight before the hint,
farther-endpoint trimming, and omission when the anchor line cannot fit. No
redistribution, ranking changes, default/MCP changes or solver calls. The v45
1000-limit run remains preserved and explicitly excluded for configuration
deviation. A regression check verifies every historical command vector plus
the new explicit 20-file invocation.

Frozen before the corrected comparison:
`results/preregistrations/v46-2cda5619b3b3dfd20ccbec3da2ebd9888d8e55c249681dc12d4f42d6b52c17bf.json`. Same 30 exposed development queries (27 with code
annotations), frozen sources, pinned Graphify 0.9.92 commit
`86c57be8204673c2a1b9849a3292a7dd3ab373d8`, native-cli-v2, cl100k tokenizer,
requested 2K/8K budgets, 22 arms, one sequential repeat and 1,320 timing samples.
Only the evidence arm has the explicit 20-file selection limit; historical
search/locate commands retain their frozen 1000 limit and unit commands remain
unchanged. These are different retrieval strategies under the same source/query/
output-budget workload. Fresh indexes, prebuilt unit indexes, fixed arm order,
shared warm/uncontrolled OS caches; no latency inference from one sample.
Source reads, locate, window selection, tokenization and stdout are all timed.

| Requested budget | Output | Mean text tokens | Shared code-file hint recall |
|---|---|---:|---:|
| 2000 | graf evidence, 20 files | 1,845.33 | 92.59% |
| 2000 | v44 experimental tail | 1,729.23 | 83.33% |
| 2000 | Pinned native code-only Graphify | 2,410.37 | 87.04% |
| 8000 | graf evidence, 20 files | 4,178.47 | 92.59% |
| 8000 | v44 experimental tail | 5,257.30 | 92.59% |
| 8000 | Pinned native code-only Graphify | 13,000.93 | 87.04% |

Token means include all 30 queries per budget; shared code-file recall uses the
27 code-annotated queries. Graf evidence has zero context-budget overshoots.
Native Graphify has 6/30 at 2K and 16/30 at 8K; retain those native outputs.
Requested and realized budgets differ; this is no equal-realized-budget
superiority claim. Code-only hints do not establish Graphify documentation
performance or source-anchor delivery.

Against v44, all 60 evidence contexts change. At 2K, three code-file recall gains
(cobra-07 0.5 to 1, flask-03 0 to 1, fastify-05 0 to 1), with no recall losses.
At 8K, flask-03 improves 0 to 1 while fastify-03 regresses 1 to 0, leaving the
same mean recall. fastify-03 loses against Graphify at both budgets (0 versus 1).
No other case has lower shared full-list file recall than Graphify. Top-5
ranking remains a separate quality concern; all per-case metrics and gained/lost
paths are retained. The v45 1000-limit comparison is included separately in the
report and is not silently pooled with this run.

The source-window strategy is **not an overall improvement**. Mean exploratory
exact source-anchor coverage is 31.48% at 2K
and 45.68% at 8K, versus v44 50.00% and
75.31%. Across both budgets it has five anchor gains and 26 losses versus v44.
These exposed author-selected code/documentation anchors are graf exact-range
diagnostics, not Graphify metrics or task outcomes. fastify-03 now misses
`lib/decorate.js` itself at both budgets, as well as line 69 and documentation
line 183. File discovery alone cannot establish useful source delivery.

Both acceptance and decision commands exited 1, **not_passed**; product promotion
and agent-efficiency acceptance remain false. The descriptive primary diagnostic
p=0.030540, corrected alpha=0.002632, family 19,
passed=false. Blockers are preserved verbatim in the report:
- development workload: the decision rule applies only to the frozen held-out set
- Only 30 queries; preregistered minimum is 110
- Insufficient scored queries: frozen pilot/minimum requires 110 per guardrail
- No significant reduction in the primary metric
- Not non-inferior: code_file_hint_recall_at_5 at 2000
- Not non-inferior: code_file_hint_recall_at_5 at 8000
- Too many queries with severe quality losses

Future newly frozen, independently authored/judged held-out planning requires at
least 110 scored queries, or more if the
future pilot requires it. No accepted graf baseline exists; earlier variants
remain experimental. Agent savings remain unmeasured; no solver/model batch was
launched or resumed.

Source/output audit passed: 60 cases, 1,320 timing samples and 25,152 locate
hints. Every one of the 1,200 historical graf contexts matches v45 exactly;
all 60 Graphify contexts match after scratch-header path normalization. Validation:
103 Rust tests, 185 Python tests, formatting, clippy and release build passed.
Rust tests/build checks are reused from v45 with identical verified source and
binary hashes; the changed Python suite was rerun. The wrapper first refused to
start while tests were pending; a later shell redirection failed for disk space
before wrapper execution. Removed only the regenerable Rust incremental cache
and retried the same freeze. All benchmark evidence was retained. These launch
failures produced no comparative samples; they are recorded in the command log.

Evidence: `results/evidence-v46-{comparison,comparison-audit,report,acceptance,decision}.json`,
current gate aliases, `results/status.json`, and `.research/evidence/evidence-v46/`
(freeze, implementation copies, patch, raw stdout/stderr, exact commands, all
timing samples, source-anchor gains/losses and artifact hashes). V45 remains
separately archived with its protocol discrepancy. Neither run is promoted.

Next work should test query-relevant structural source selection within discovered
files rather than repeating uniform short windows. Recovering exact evidence
and the fastify-03 tail loss must be checked alongside file recall, then fresh
held-out natural tasks and matched Graphify-plus-source solver trials are needed
before any claim of agent token savings.


## v47 structural source selection within located files (2026-10-23)

Implemented opt-in `graf evidence-units`. It keeps v46's locate ranking,
explicit 20-hint limit and equal per-returned-file token shares. Existing global
unit BM25/call scores and symbol scores order strictly positive structural
units within each selected file (original-index ties, no score floor). It emits
the first complete unit that fits that file's share; oversized units are skipped.
Invalid physical ranges or legacy/physical payload disagreements are rejected.
When no unit fits, it uses v46's unchanged physical source-window fallback.
Total exact cl100k context is capped, including JSON-quoted citations; response
JSON metadata is extra. Spare shares are not redistributed. Defaults, MCP,
index extraction and all previous experimental arms remain unchanged.

Frozen before comparative outputs:
`results/preregistrations/v47-88384e6a00f178745bb056602352d7f92ef663248f8ce7bf7fb9bde5ff7e3193.json`. Same 30 exposed development queries (27 with code
annotations), frozen source files, cl100k tokenizer, requested 2K/8K budgets,
23 arms, one sequential repeat, 1,380 timing samples and zero solver calls.
The evidence commands explicitly use limit 20; historical search/locate commands
retain their frozen 1000 limit, and unit commands remain unchanged. These are
preregistered retrieval strategies under the same source/query/output-budget
workload. Pinned Graphify is 0.9.92, commit
`86c57be8204673c2a1b9849a3292a7dd3ab373d8`, native-cli-v2, code-only. Graphify
is used only as the benchmark competitor; graf remains independent Rust.

Fresh indexes prebuild unit caches, fixed sequential arm order, shared
warm/uncontrolled OS caches. The timed boundary includes process startup,
index/unit-index load, locate and unit ranking, source reads/freshness checks,
complete-unit fit checks, fallback trimming, tokenization and stdout. One sample
per case cannot support latency inference.

| Budget | Output | Mean emitted text tokens | Shared code-file hint recall |
|---|---|---:|---:|
| 2000 | v47 structural evidence | 1,496.23 | 92.59% |
| 2000 | v46 source windows | 1,845.33 | 92.59% |
| 2000 | v44 experimental tail | 1,729.23 | 83.33% |
| 2000 | Pinned native code-only Graphify | 2,410.37 | 87.04% |
| 8000 | v47 structural evidence | 3,753.73 | 92.59% |
| 8000 | v46 source windows | 4,178.47 | 92.59% |
| 8000 | v44 experimental tail | 5,257.30 | 92.59% |
| 8000 | Pinned native code-only Graphify | 13,000.93 | 87.04% |

Token means include all 30 queries; shared code-file recall uses the 27
code-annotated queries. All raw native outputs are retained. Graphify overshot
requested cl100k budgets in 6/30 cases at 2K and 16/30 at 8K; graf structural
context overshoots: 2000: 0, 8000: 0.
Requested and realized budgets differ; no equal-realized-budget superiority
claim. Code-only Graphify hints do not establish documentation performance,
source-anchor coverage or solver outcomes.

Shared code-file recall gains and losses (all cases remain in the report):
- Versus v46 windows at 2000: no per-case changes.
- Versus v46 windows at 8000: no per-case changes.
- Versus v44 tail at 2000: cobra-07 0.5 to 1; flask-03 0 to 1; fastify-05 0 to 1.
- Versus v44 tail at 8000: flask-03 0 to 1; fastify-03 1 to 0.

Severe query losses versus Graphify: fastify-03.

Exploratory exact source-anchor coverage for graf only:

| Source delivery | 2K | 8K |
|---|---:|---:|
| v47 structural | 25.93% | 58.02% |
| v46 windows | 31.48% | 45.68% |
| v44 tail | 50.00% | 75.31% |

These are author-selected exposed code/documentation source anchors with exact
full-range coverage, not shared Graphify metrics, independent labels or natural
task outcomes. Preserve individual losses rather than replacing them with a mean.
Compared with v46 windows: 13 source-anchor case gains and 10 losses across both budgets.
Compared with v44 tail: 4 source-anchor case gains and 22 losses across both budgets.

The report preserves fastify-03 file/range checks at both budgets, including
`lib/decorate.js:69` and `docs/Reference/Decorators.md:183`. Expansion IDs retain
the original locate chunk's anchor, not a new whole-unit identity: expanding one
may read a different range from the chosen unit; clients should use the returned
path/range to read the selected evidence. No follow-up agent efficiency claim
is established by these text-only retrieval measurements.

Both gate commands exited 1: **not_passed**. Product promotion and agent-efficiency
acceptance remain false. Primary descriptive p=0.000010, corrected
alpha=0.002500, family=20, passed=true.
Decision blockers are preserved verbatim:
- development workload: the decision rule applies only to the frozen held-out set
- Only 30 queries; preregistered minimum is 110
- Insufficient scored queries: frozen pilot/minimum requires 110 per guardrail
- Not non-inferior: code_file_hint_recall_at_5 at 2000
- Not non-inferior: code_file_hint_recall_at_5 at 8000
- Too many queries with severe quality losses

This is a development diagnostic, not an accepted improvement. No accepted graf
baseline exists, so previous graf comparisons are experimental ablations.
Future newly frozen, independently authored/judged held-out planning requires
at least 110 scored queries (or more if a
future pilot requires it). Agent token savings remain **unmeasured**; zero new
solver/model attempts and no resumed stopped batch.

Validation passed: 105 Rust tests, 185 Python checks, formatting, clippy, release
build, and exact-source/output audit for 60 cases, 1,380 timings and 25,152 locate
hints. All 1,260 historical graf contexts match v46 byte-for-byte; all 60 Graphify
contexts match after normalizing only scratch-header path prefixes. Tests cover
whole-function delivery, exact budget shares, unchanged oversized-unit fallback,
CRLF, Unicode, filtering, stale source rejection and CLI behavior.

Evidence: `results/structural-v47-{comparison,comparison-audit,report,acceptance,decision}.json`,
current gate aliases, `results/status.json`, and `.research/evidence/structural-v47/`
(freeze, implementation copies, v46-to-v47 patch, commands, raw outputs, all timing
samples, every source-anchor gain/loss, validation and artifact hashes). The
v45 configuration-deviation data and v46 corrected replay remain archived.

Keep this command experimental. Future work must address exact source selection
and the remaining severe losses together; matched Graphify-plus-source reads,
complete usage accounting and held-out natural tasks are required before agent
savings or product superiority claims.


## v48 evidence range-handle reliability repair (2026-10-23)

Fixed evidence and evidence-units JSON expansion IDs. Previously the ID resolved
the original locate chunk, although source selection could return a different
range in that file. New `range:ANCHOR:START:END` IDs retain the indexed snapshot
anchor and encode the delivered physical range. Expand validates bounded syntax,
positive canonical integer coordinates, unique lowercase hex anchor, current
source hash and in-bounds range, then widens from those range boundaries using
saturating arithmetic. It preserves the range ID. Original search/locate chunk
handles retain their behavior. This repairs follow-up source integrity; it does
not change ranking, per-file shares, selected evidence or context budgets.

Frozen before comparative data: `results/preregistrations/v48-d99dd8b6c3f64582da04327fde3d3b9bb134739b35c068c3a6786f66d753af2f.json`.
Same 30 exposed development queries (27 code-annotated), 2K/8K requested cl100k
budgets, 23 arms, one sequential repeat, 1,380 timing samples, unchanged primary
metric and 20-arm experimental family, quality margins, guardrails and tail.
No new retrieval candidate. Graphify remains code-only native-cli-v2, 0.9.92,
commit `86c57be8204673c2a1b9849a3292a7dd3ab373d8`; no baseline update.
Fresh indexes and prebuilt unit caches; fixed sequential order, shared warm and
uncontrolled OS caches. Timings include startup, loading, retrieval, source
validation, packing, tokenization and stdout. One repeat cannot establish speed.

All 1,320 graf text contexts match v47 byte-for-byte; all 60 Graphify contexts
match after normalizing only scratch-header paths. Source-anchor diagnostics and
all per-case shared quality scores are unchanged. No accepted graf baseline
exists; v47 is a preceding experimental version, not an accepted competitor.

| Budget | Output | Mean text tokens (30 queries) | Shared code-file recall (27 queries) |
|---|---|---:|---:|
| 2000 | Structural evidence | 1,496.23 | 92.59% |
| 2000 | Source windows | 1,845.33 | 92.59% |
| 2000 | Pinned code-only Graphify | 2,411.37 | 87.04% |
| 8000 | Structural evidence | 3,753.73 | 92.59% |
| 8000 | Source windows | 4,178.47 | 92.59% |
| 8000 | Pinned code-only Graphify | 13,001.93 | 87.04% |

Graphify exceeded requested cl100k budgets in 6/30 cases at 2K and 16/30 at 8K;
these raw outputs are preserved. Graf evidence contexts remain within budget.
Different realized budgets prohibit an equal-realized-budget superiority claim.
Raw Graphify token counts include the changed scratch-header path; canonical
compatibility normalizes that path only. This path-length difference is not an
efficiency change, and reported token counts remain raw.
Graphify hints do not establish source-anchor coverage or documentation results.
The severe fastify-03 loss and top-5 quality failures remain visible.

The separate integrity audit checked 120 old/new JSON
response pairs and expanded every returned range: 2331
verified expansions, 2574 sequential diagnostic process
invocations. All fields except evidence IDs match the previous binary, including
context and source hashes. Full commands, raw stdout/stderr and return codes are
preserved in `.research/evidence/range-handles-v48/range-roundtrips.jsonl`.
The initial integrity diagnostic omitted explicit zero widening and encountered
a correct default 24-line expansion. That failed audit and its three commands
are preserved; the corrected diagnostic passes --before 0 --after 0. All failed
attempts are included in the diagnostic invocation count. No implementation or
comparative configuration changed. These diagnostics are not comparative
efficiency measurements.

Range IDs add JSON metadata tokens, excluded from context budgets:

| Budget | Command | Previous mean JSON tokens | Current mean JSON tokens | Mean added tokens |
|---|---|---:|---:|---:|
| 2000 | graf_evidence | 3,658.83 | 3,772.53 | 113.70 |
| 2000 | graf_evidence_units | 3,267.63 | 3,381.20 | 113.57 |
| 8000 | graf_evidence | 6,302.50 | 6,417.73 | 115.23 |
| 8000 | graf_evidence_units | 5,839.03 | 5,955.13 | 116.10 |

Counts cover the entire serialized response and terminal newline under the same
cl100k tokenizer. They are response payload diagnostics, not total agent token
costs, solver outcomes or matched Graphify-plus-source-read savings.

Both acceptance and decision commands exited 1, status **not_passed**. Product
promotion and agent-efficiency acceptance remain false. Decision blockers:
- development workload: the decision rule applies only to the frozen held-out set
- Only 30 queries; preregistered minimum is 110
- Insufficient scored queries: frozen pilot/minimum requires 110 per guardrail
- Not non-inferior: code_file_hint_recall_at_5 at 2000
- Not non-inferior: code_file_hint_recall_at_5 at 8000
- Too many queries with severe quality losses

Future fresh independently authored/judged held-out work requires at least
110 scored queries, or the larger future
pilot requirement. Agent savings remain **unmeasured**; zero solver/model calls,
and the previously stopped solver batch was not resumed.

Validation: 108 Rust tests (33 unit, 75 integration), 185 Python checks,
formatting, clippy, release build, paired output/source audit and every range
round trip passed. An initial clippy failure (needless `Ok(...?)`) was repaired
before freezing; the original failure and subsequent passing check are retained.
The full tests passed before that equivalent lint repair and the three new
behavior tests passed again afterward. Tests cover exact/widened expansion,
legacy handles, CRLF, Unicode, escaped filenames, zero budgets, saturation,
malformed and out-of-bounds ranges, stale sources before/after reindex, and CLI.

Evidence: `results/range-handles-v48-{comparison,comparison-audit,handles-audit,report,acceptance,decision}.json`,
current gate aliases, `results/status.json`, and `.research/evidence/range-handles-v48/`
(frozen source/binary hashes, previous binary, patch, raw outputs and commands,
validation failures/repairs and artifact manifest). Earlier v45 deviation and
v46/v47 experiment archives remain preserved. Experimental commands stay
unpromoted; this correctness repair does not resolve source-selection losses.


## v50: append-only linked evidence (development pilot)

`evidence-linked` preserves v48 structural evidence and appends complete positive-ranked units from up to four additional files directly connected by existing call edges. Scope/kind, physical source authentication, range handles and exact context budgets remain enforced. Defaults and MCP unchanged.

Version49 preflight rejected a stale native-adapter driver receipt before any comparative data. Both frozen specifications and the failed invocation are retained; version50 corrected only the receipt. Initial Python validation also caught the missing arm registration before the freeze; retry passed.

| Requested budget | v48 structural tokens | v50 linked tokens | Graphify tokens | v48 code-file recall | v50 recall | Graphify recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2000 | 1496.23 | 1864.17 | 2411.37 | 92.59% | 96.30% | 87.04% |
| 8000 | 3753.73 | 4480.87 | 13001.93 | 92.59% | 92.59% | 87.04% |

{"changed_context_cases": 60, "improved_code_recall_cases": 1, "worsened_code_recall_cases": 0}. All 1320 historical graf contexts were byte-identical to v48; 60 new outputs retained their structural prefix. Graphify contexts matched after scratch-header path normalization; raw native token counts retained.

Severe-loss queries vs Graphify: ["fastify-03"]. Decision blockers: ["development workload: the decision rule applies only to the frozen held-out set", "Only 30 queries; preregistered minimum is 110", "Insufficient scored queries: frozen pilot/minimum requires 110 per guardrail", "No significant reduction in the primary metric", "Not non-inferior: code_file_hint_recall_at_5 at 2000", "Not non-inferior: code_file_hint_recall_at_5 at 8000", "Too many queries with severe quality losses"]. Future scored query pilot requirement: 110.

110 Rust tests, 185 Python checks, fmt, Clippy, release build and comparison integrity audit passed. Acceptance and decision both exited1 (`not_passed`); product promotion and agent-efficiency acceptance remain false. This is an unproven opt-in experiment.

Same frozen source sets and queries, cl100k, requested budgets and one paired repetition, sequential competitors, fresh indexes with prebuilt units and warm/uncontrolled OS caches. 24 arms / 1440 timing samples. One sample is not latency evidence. Code-only Graphify yields navigation hints, not source excerpts; source-anchor recall is exploratory and attributed only to graf. Documentation tasks and agent savings remain unmeasured; zero model calls. Exposed development queries cannot pass held-out promotion, and aggregate recall does not hide per-case losses.

All 60 contexts grew. The only new code-file hit was fastify-03 at2K, but neither annotated source anchor was delivered there or at8K. Overall exploratory source-anchor recall stayed unchanged at25.93% /58.02%. At8K earlier larger fitting units consume the four-file append cap before lib/decorate.js, so the file rescue is not monotone in budget. This result does not establish better answer evidence. No post-result algorithm tuning was performed.

Report: `results/linked-evidence-v50-report.json`; raw comparison: `results/linked-evidence-v50-comparison.json`; freeze: `results/preregistrations/v50-0077f8a819ecca3357d6ad31491e84f72d7cf0b6efa1b6c7f264c252f83319e7.json`; evidence: `.research/evidence/linked-evidence-v50/`.


## v51: atomic linked-function/helper bundles (development pilot)

Rejected as an improvement: Rejected as an improvement on the exposed development pilot: more mean output tokens at both budgets versus v50, one code-file recall loss at2K (fastify-03), no code-file gains and no exploratory source-anchor gains at either budget. No source evidence gained versus the simpler structural prefix. The synthetic functionality works, but this data does not support the retrieval hypothesis. Retain opt-in for reproducibility; do not promote. No post-result algorithm tuning.
`evidence-bundles` preserves evidence-units as a prefix. It keeps the v50 cross-file candidate ranking, but appends each candidate function atomically with at most two direct same-file helpers ranked by body/call score and original index. No recursive traversal, partial bundle, default or MCP change. At most four new-file bundles. Whole physical-source authentication, freshness, exact token budgets and range handles apply to every member.

Initial synthetic tests exposed that positive-score-only helper eligibility discarded useful zero-score callees. Before any new comparative data or freeze, eligibility was changed to include direct helpers regardless of lexical score; their ordering still uses body/call scores. All initial failures and the final validation logs are retained.

| Requested budget | v50 linked tokens | v51 bundled tokens | Graphify tokens | v50 code-file recall | v51 recall | Graphify recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2000 | 1864.17 | 1883.83 | 2413.37 | 96.30% | 92.59% | 87.04% |
| 8000 | 4480.87 | 4755.87 | 13003.93 | 92.59% | 92.59% | 87.04% |

All-case changes vs ablations: {"graf_evidence_units": {"changed_context_cases": 60, "improved_code_recall_cases": 0, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 0, "worsened_source_anchor_cases": 0}, "graf_evidence_linked": {"changed_context_cases": 44, "improved_code_recall_cases": 0, "worsened_code_recall_cases": 1, "improved_source_anchor_cases": 0, "worsened_source_anchor_cases": 0}}. All 1380 historical graf contexts were byte-identical to v50; all60 new outputs retained their structural prefix. Graphify contexts matched after scratch-header path normalization; raw native outputs/tokens retained.

Exploratory graf-only source-anchor recall: {"2000": {"graf_evidence_units": 0.25925925925925924, "graf_evidence_linked": 0.25925925925925924, "graf_evidence_bundles": 0.25925925925925924}, "8000": {"graf_evidence_units": 0.5802469135802469, "graf_evidence_linked": 0.5802469135802469, "graf_evidence_bundles": 0.5802469135802469}}. Source-anchor gains and losses are reported per case; no source ranges are attributed to Graphify hints.

Fastify-03 detailed ranges: [{"budget": 2000, "code_file_recall": {"graf_evidence_units": 0.0, "graf_evidence_linked": 1.0, "graf_evidence_bundles": 0.0, "graphify": 1.0}, "anchors": {"graf_evidence_units": {"recall": 0.0, "anchors": [{"path": "docs/Reference/Decorators.md", "start": 183, "end": 183, "delivered": false}, {"path": "lib/decorate.js", "start": 69, "end": 69, "delivered": false}]}, "graf_evidence_linked": {"recall": 0.0, "anchors": [{"path": "docs/Reference/Decorators.md", "start": 183, "end": 183, "delivered": false}, {"path": "lib/decorate.js", "start": 69, "end": 69, "delivered": false}]}, "graf_evidence_bundles": {"recall": 0.0, "anchors": [{"path": "docs/Reference/Decorators.md", "start": 183, "end": 183, "delivered": false}, {"path": "lib/decorate.js", "start": 69, "end": 69, "delivered": false}]}}}, {"budget": 8000, "code_file_recall": {"graf_evidence_units": 0.0, "graf_evidence_linked": 0.0, "graf_evidence_bundles": 0.0, "graphify": 1.0}, "anchors": {"graf_evidence_units": {"recall": 0.0, "anchors": [{"path": "docs/Reference/Decorators.md", "start": 183, "end": 183, "delivered": false}, {"path": "lib/decorate.js", "start": 69, "end": 69, "delivered": false}]}, "graf_evidence_linked": {"recall": 0.0, "anchors": [{"path": "docs/Reference/Decorators.md", "start": 183, "end": 183, "delivered": false}, {"path": "lib/decorate.js", "start": 69, "end": 69, "delivered": false}]}, "graf_evidence_bundles": {"recall": 0.0, "anchors": [{"path": "docs/Reference/Decorators.md", "start": 183, "end": 183, "delivered": false}, {"path": "lib/decorate.js", "start": 69, "end": 69, "delivered": false}]}}}].

Severe-loss queries vs Graphify: ["fastify-03"]. Decision blockers: ["development workload: the decision rule applies only to the frozen held-out set", "Only 30 queries; preregistered minimum is 110", "Insufficient scored queries: frozen pilot/minimum requires 110 per guardrail", "No significant reduction in the primary metric", "Not non-inferior: code_file_hint_recall_at_5 at 2000", "Not non-inferior: code_file_hint_recall_at_5 at 8000", "Too many queries with severe quality losses"]. Future pilot requirement: 110 scored queries, at least the preregistered minimum110.

113 Rust tests, 185 Python checks, fmt, Clippy, release build and comparison integrity audit passed. Acceptance and decision both exited1 (`not_passed`); product promotion and agent-efficiency acceptance remain false. This is unproven and opt-in.

Same frozen source sets/queries, cl100k, requested budgets, sequential competitors, one paired repetition, fresh indexes/prebuilt units and warm/uncontrolled OS caches. 25 arms /1500 timing samples. One sample is not latency inference. Code-only Graphify yields navigation hints; source-anchor recall is an exploratory graf diagnostic. Documentation-task performance and agent savings are unmeasured, zero model calls. Development queries are exposed and cannot pass held-out promotion. No algorithm tuning after this result.

Report: `results/bundled-evidence-v51-report.json`; comparison: `results/bundled-evidence-v51-comparison.json`; freeze: `results/preregistrations/v51-dc807d3da8d20d083cb7f630acecda6f40a3b222e0eda28e63147bced8dc93a6.json`; evidence: `.research/evidence/bundled-evidence-v51/`.


## v52: distinct IDF-weighted coverage ranking (development pilot)

Implemented opt-in `evidence-coverage`. Each BM25 chunk score is multiplied by the square of the fraction of indexed query IDF represented by distinct matching terms. Query duplicates and out-of-vocabulary words do not change ranking. Existing file collapse, scope/kind filters, structural unit selection, equal shares, physical-source validation, budgets and range handles remain in use. No default or MCP changes. This experiment isolates file ranking from the v51 graph expansion attempt.

| Requested budget | Structural v48 tokens | Bundles v51 tokens | Coverage v52 tokens | Graphify tokens | v48 code-file recall | v51 recall | v52 recall | Graphify recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2000 | 1496.23 | 1883.83 | 1512.77 | 2412.37 | 92.59% | 92.59% | 94.44% | 87.04% |
| 8000 | 3753.73 | 4755.87 | 3897.67 | 13002.93 | 92.59% | 92.59% | 94.44% | 87.04% |

Interpretation: Mixed development diagnostic: better total code-file recall than v51 and the structural ablation via Fastify-08 at both budgets, with no total file-recall losses against those two. Three query-budget source-anchor gains and one loss; fastify-07 loses an anchor at2K, fastify-03 remains severe loss vs Graphify. Primary token test passes against Graphify, but top5/top10 quality NI and tail gates do not. Exposed pilot cannot promote; agent efficiency unmeasured.

Per-case gains and losses vs structural/ranking ablation and previous experiment: {"graf_evidence_units": {"changed_context_cases": 60, "improved_code_recall_cases": 2, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 3, "worsened_source_anchor_cases": 1}, "graf_evidence_bundles": {"changed_context_cases": 60, "improved_code_recall_cases": 2, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 3, "worsened_source_anchor_cases": 1}}. Every case, including all losses, is retained in the report.

Exploratory graf-only source-anchor recall: {"2000": {"graf_evidence_units": 0.25925925925925924, "graf_evidence_bundles": 0.25925925925925924, "graf_evidence_coverage": 0.25925925925925924}, "8000": {"graf_evidence_units": 0.5802469135802469, "graf_evidence_bundles": 0.5802469135802469, "graf_evidence_coverage": 0.6111111111111112}}. These author-selected anchors are a development diagnostic, not shared Graphify quality or evidence of documentation-task performance.

Severe-loss queries vs Graphify: ["fastify-03"]. Decision blockers: ["development workload: the decision rule applies only to the frozen held-out set", "Only 30 queries; preregistered minimum is 110", "Insufficient scored queries: frozen pilot/minimum requires 110 per guardrail", "Not non-inferior: code_file_hint_recall_at_5 at 2000", "Not non-inferior: code_file_hint_recall_at_5 at 8000", "Not non-inferior: code_file_hint_recall_at_10 at 2000", "Not non-inferior: code_file_hint_recall_at_10 at 8000", "Too many queries with severe quality losses"]. Primary query-level result: {"metric": "output_tokens", "budget": 2000, "hypothesis": "candidate delivers fewer tokens than the comparator", "test": "one-sided paired sign-flip permutation on comparator minus candidate", "alpha": 0.002173913043478261, "family": 23, "queries": 30, "mean_reduction": 899.6, "p_value": 1.999980000199998e-05, "passed": true}. Future pilot requirement: 110 scored queries, never below110.

All1440 historical graf contexts were byte-identical to v51. All60 Graphify outputs matched after scratch-header normalization; native raw outputs/tokens and budget overshoots remain preserved. Both acceptance and decision exited1 (not_passed). Promotion and agent-efficiency acceptance remain false; candidate stays experimental.

115 Rust tests, 185 Python checks, fmt, Clippy, release build and exact source/comparison audit passed. Initial test-only range-separator assertion failure and missing draft arm registration failure were fixed before freeze/data and retained in evidence. No ranking algorithm adjustment after freeze or results.

Same frozen sources/queries, cl100k tokenizer, requested2K/8K budgets,26 arms, one sequential repetition,1560 timing samples. Fresh indexes/prebuilt units; shared warm/uncontrolled OS caches. Timings are descriptive only. Code-only pinned Graphify produces navigation hints; graf delivers source ranges. These30 queries (27 code-annotated) are exposed development data and cannot establish product promotion. Agent savings unmeasured, zero model calls. No accepted graf version exists.

Report: `results/coverage-evidence-v52-report.json`; comparison: `results/coverage-evidence-v52-comparison.json`; frozen preregistration: `results/preregistrations/v52-753d904cd35a4df4adeedee54a2fe916466fe9276dfe46603ab717c1167f876a.json`; evidence: `.research/evidence/coverage-evidence-v52/`.


## v53: bounded indexed lexical families (development pilot)

Implemented opt-in `evidence-families`: each unique query term groups its exact indexed spelling with at most4 indexed ASCII alphabetic alternatives of length5..64, same first4 bytes and edit distance<=1 for minimum length5/6 or<=2 for minimum length>=7. Alternatives ordered distance then spelling. Group frequency is maximum discounted tf (0.5^distance), union document frequency determines IDF, squared IDF coverage follows v52. Original query drives previews and structural-unit selection. Source packing, filters, freshness, budgets, handles, defaults and MCP behavior are unchanged. No dependency or schema change.

| Requested budget | Structural v48 tokens | Coverage v52 tokens | Families v53 tokens | Graphify tokens | v48 code-file recall | v52 recall | v53 recall | Graphify recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2000 | 1496.23 | 1512.77 | 1514.23 | 2411.37 | 92.59% | 94.44% | 94.44% | 87.04% |
| 8000 | 3753.73 | 3897.67 | 3847.30 | 13001.93 | 92.59% | 94.44% | 94.44% | 87.04% |

Interpretation: Mixed, unpromoted development result. Compared with v52, total code-file recall is unchanged, top5/top10 recall improves by1.85185 percentage points each, but source-anchor recall loses Cobra-08 site/content/docgen/md.md:20 at2K (no anchor gains). Fastify-03 remains a severe loss vs Graphify, and the earlier Fastify-07 source loss remains unrecovered. Primary token test against Graphify passes, but top5/top10 quality NI and severe-loss tail gates fail. No held-out or agent-efficiency evidence.

Held-out planning: Pilot planning requires142 scored queries for this candidate, above preregistered floor110 and the existing held-out111 code-annotated queries. Do not run or promote this candidate on an undersized held-out set; independently author and freeze an appropriately sized new workload before new data.

Per-case gains and losses vs structural/ranking ablation and previous experiment: {"graf_evidence_units": {"changed_context_cases": 60, "improved_code_recall_cases": 2, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 3, "worsened_source_anchor_cases": 2}, "graf_evidence_coverage": {"changed_context_cases": 58, "improved_code_recall_cases": 0, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 0, "worsened_source_anchor_cases": 1}}. Every case, including all losses, is retained in the report.

Exploratory graf-only source-anchor recall: {"2000": {"graf_evidence_units": 0.25925925925925924, "graf_evidence_coverage": 0.25925925925925924, "graf_evidence_families": 0.24691358024691357}, "8000": {"graf_evidence_units": 0.5802469135802469, "graf_evidence_coverage": 0.6111111111111112, "graf_evidence_families": 0.6111111111111112}}. These author-selected anchors are a development diagnostic, not shared Graphify quality or evidence of documentation-task performance.

Severe-loss queries vs Graphify: ["fastify-03"]. Decision blockers: ["development workload: the decision rule applies only to the frozen held-out set", "Only 30 queries; preregistered minimum is 110", "Insufficient scored queries: frozen pilot/minimum requires 110 per guardrail", "Not non-inferior: code_file_hint_recall_at_5 at 2000", "Not non-inferior: code_file_hint_recall_at_5 at 8000", "Not non-inferior: code_file_hint_recall_at_10 at 2000", "Not non-inferior: code_file_hint_recall_at_10 at 8000", "Too many queries with severe quality losses"]. Primary query-level result: {"metric": "output_tokens", "budget": 2000, "hypothesis": "candidate delivers fewer tokens than the comparator", "test": "one-sided paired sign-flip permutation on comparator minus candidate", "alpha": 0.0020833333333333333, "family": 24, "queries": 30, "mean_reduction": 897.1333333333333, "p_value": 9.99990000099999e-06, "passed": true}. Future pilot requirement: 142 scored queries, never below110.

All1500 historical graf contexts were byte-identical to v52. All60 Graphify outputs matched after scratch-header normalization; native raw outputs/tokens and budget overshoots remain preserved. Both acceptance and decision exited1 (not_passed). Promotion and agent-efficiency acceptance remain false; candidate stays experimental.

119 Rust tests, 185 Python checks, fmt, Clippy, release build and exact source/comparison audit passed. Targeted synthetic spelling, cap, dismax, source and CLI tests also passed. No ranking algorithm adjustment after freeze or results.

Same frozen sources/queries, cl100k tokenizer, requested2K/8K budgets,27 arms, one sequential repetition,1620 timing samples. Fresh indexes/prebuilt units; shared warm/uncontrolled OS caches. Timings are descriptive only. Code-only pinned Graphify produces navigation hints; graf delivers source ranges. These30 queries (27 code-annotated) are exposed development data and cannot establish product promotion. Agent savings unmeasured, zero model calls. No accepted graf version exists.

Report: `results/family-evidence-v53-report.json`; comparison: `results/family-evidence-v53-comparison.json`; frozen preregistration: `results/preregistrations/v53-1cb0f0e582cdd5acab88575c46dfec55305afef59c38346cbb947648fc4a9a6b.json`; evidence: `.research/evidence/family-evidence-v53/`.


## v54: full-file lexical passages (development pilot)

Implemented opt-in `evidence-passages`: keep v53 family-ranked files, scan every physical 24-line window using original query term rarity, saturated frequency and squared coverage. Select the highest-scoring passage before fitting, then trim around its strongest matching line to the equal per-file budget share. Paths and metadata do not contribute passage term counts. This can split functions. Defaults and MCP behavior are unchanged.

| Requested budget | Structural v48 tokens | Families v53 tokens | Passages v54 tokens | Graphify tokens | v48 code-file recall | v53 recall | v54 recall | Graphify recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2000 | 1496.23 | 1514.23 | 1830.20 | 2412.37 | 92.59% | 94.44% | 94.44% | 87.04% |
| 8000 | 3753.73 | 3847.30 | 4635.53 | 13002.93 | 92.59% | 94.44% | 94.44% | 87.04% |

Interpretation: Do not promote v54. Compared with v53, total code-file recall is unchanged, all60 contexts change, output tokens rise from1514.23 to1830.20 at2K and3847.30 to4635.53 at8K. Exploratory source-anchor recall rises from24.69% to29.01% at2K but falls from61.11% to45.06% at8K; across budgets12 query cases gain anchors and17 lose them. The fixed24-line lexical window can lose evidence captured by a complete structural unit, even with a larger budget. Fastify-03 still misses both selected anchors and its code file. Fastify-07 loses all delivered anchors at both budgets (v53 had1/3 at2K and2/3 at8K). Cobra-08 recovers doc/md_docs.go:125 only at8K but still misses the previously lost documentation anchor at line20. No superiority or agent savings established. All cases retained; no algorithm tuning after results.

Held-out planning: Current pilot requires142 scored queries; preregistered minimum110. Existing held-out workload has111 code-annotated queries (123 total), insufficient for this pilot. Author and freeze a new recorded workload of at least142 scored queries before any new held-out data. This exposed development experiment cannot promote.

Per-case gains and losses: {"graf_evidence_units": {"changed_context_cases": 60, "improved_code_recall_cases": 2, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 13, "worsened_source_anchor_cases": 16}, "graf_evidence_families": {"changed_context_cases": 60, "improved_code_recall_cases": 0, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 12, "worsened_source_anchor_cases": 17}}. Every case, including losses and exact source anchors, is retained in the report.

Exploratory graf-only source-anchor recall: {"2000": {"graf_evidence_units": 0.25925925925925924, "graf_evidence_families": 0.24691358024691357, "graf_evidence_passages": 0.29012345679012347}, "8000": {"graf_evidence_units": 0.5802469135802469, "graf_evidence_families": 0.6111111111111112, "graf_evidence_passages": 0.4506172839506173}}. These author-selected development anchors are not a shared Graphify quality metric or documentation-task performance evidence.

Severe-loss queries vs Graphify: ["fastify-03"]. Decision blockers: ["development workload: the decision rule applies only to the frozen held-out set", "Only 30 queries; preregistered minimum is 110", "Insufficient scored queries: frozen pilot/minimum requires 110 per guardrail", "No significant reduction in the primary metric", "Not non-inferior: code_file_hint_recall_at_5 at 2000", "Not non-inferior: code_file_hint_recall_at_5 at 8000", "Not non-inferior: code_file_hint_recall_at_10 at 2000", "Not non-inferior: code_file_hint_recall_at_10 at 8000", "Too many queries with severe quality losses"]. Primary query-level result: {"metric": "output_tokens", "budget": 2000, "hypothesis": "candidate delivers fewer tokens than the comparator", "test": "one-sided paired sign-flip permutation on comparator minus candidate", "alpha": 0.002, "family": 25, "queries": 30, "mean_reduction": 582.1666666666666, "p_value": 0.023399766002339978, "passed": false}. Future pilot requirement: 142 scored queries, never below110.

All1560 historical graf contexts were byte-identical to v53. All60 Graphify outputs matched after scratch-header normalization; raw native outputs/tokens and budget overshoots remain preserved. Both acceptance and decision exited1 (not_passed). Promotion and agent-efficiency acceptance remain false; candidate stays experimental.

123 Rust tests, 185 Python checks, fmt, Clippy, release build and exact physical source/comparison audit passed. No algorithm adjustment after freeze or results.

Same frozen sources/queries, cl100k tokenizer, requested2K/8K budgets,28 arms, one sequential repetition,1680 timing samples. Fresh indexes/prebuilt units; shared warm/uncontrolled OS caches. Timings are descriptive only. Code-only pinned Graphify produces navigation hints; graf delivers source ranges. These30 queries (27 code-annotated) are exposed development data and cannot establish product promotion. Agent savings unmeasured, zero model calls. No accepted graf version exists.

Report: `results/passage-evidence-v54-report.json`; comparison: `results/passage-evidence-v54-comparison.json`; frozen preregistration: `results/preregistrations/v54-cf3b55465afde10aa717b3b6d7de9539da422f9e89747b1cf0c09cb7811c90bd.json`; evidence: `.research/evidence/passage-evidence-v54/`.


## v55: structural evidence with novel-term complements (development pilot)

Implemented opt-in `evidence-complement`: retain the exact v53 structural source context and excerpt metadata as prefix. Split unused budget equally across delivered base files. Append at most one non-overlapping24-line physical passage per file, scored by query terms absent from that files original excerpt and trimmed to fit its fixed share. No novel-term match means no append. Exact range handles, hashes, filters, total token accounting, defaults and MCP behavior retained. Added passages can split functions.

| Requested budget | Structural v48 tokens | Families v53 tokens | Complements v55 tokens | Graphify tokens | v48 code-file recall | v53 recall | v55 recall | Graphify recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2000 | 1496.23 | 1514.23 | 1671.70 | 2412.37 | 92.59% | 94.44% | 94.44% | 87.04% |
| 8000 | 3753.73 | 3847.30 | 5951.23 | 13002.93 | 92.59% | 94.44% | 94.44% | 87.04% |

Interpretation: Keep v55 experimental; not an accepted improvement. It preserves the exact v53 structural context in all60 cases and adds518 non-overlapping ranges across55 changed cases. Total shared code-file recall and top5/top10 recall are unchanged from v53. Exploratory source-anchor recall gains only two query-budget cases with zero losses vs v53: Cobra04 at2K recovers site/content/user_guide.md:287, Cobra05 at8K recovers command.go:985. Mean tokens rise from1514.23 to1671.70 at2K and3847.30 to5951.23 at8K; anchor recall rises from24.69% to26.54% and61.11% to62.96% respectively. This is a weak marginal yield for the added output and does not establish agent savings. No source losses vs v53 does not mean no losses vs other methods: compared to structural v48 there are5 query-budget anchor gains and2 losses (Cobra08 at2K, Fastify07 at2K), inherited from v53. Compared to v54 replacement passages there are17 gains and10 losses. Fastify03 remains the severe code-file miss and both author anchors absent; Fastify07 retains v53 anchors but recovers no missing anchor; Cobra08 recovers none. Graphify token primary test passes (p0.00130, family-adjusted alpha0.001923), but quality guardrails, severe-tail cap, development-only/undersized workload and unmeasured agent outcomes prevent acceptance. No algorithm tuning after results.

Held-out planning: Current pilot requires142 scored queries; preregistered minimum110. Existing held-out workload has111 code-annotated queries (123 total), insufficient for this pilot. Author and freeze a new recorded workload of at least142 scored queries before any new held-out data. Exposed development experiments cannot promote.

Per-case gains and losses: {"graf_evidence_units": {"changed_context_cases": 60, "improved_code_recall_cases": 2, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 5, "worsened_source_anchor_cases": 2}, "graf_evidence_families": {"changed_context_cases": 55, "improved_code_recall_cases": 0, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 2, "worsened_source_anchor_cases": 0}, "graf_evidence_passages": {"changed_context_cases": 60, "improved_code_recall_cases": 0, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 17, "worsened_source_anchor_cases": 10}}. Every case, including losses and exact source anchors, is retained in the report.

Exploratory graf-only source-anchor recall: {"2000": {"graf_evidence_units": 0.25925925925925924, "graf_evidence_families": 0.24691358024691357, "graf_evidence_passages": 0.29012345679012347, "graf_evidence_complement": 0.2654320987654321}, "8000": {"graf_evidence_units": 0.5802469135802469, "graf_evidence_families": 0.6111111111111112, "graf_evidence_passages": 0.4506172839506173, "graf_evidence_complement": 0.6296296296296297}}. These author-selected development anchors are not a shared Graphify quality metric or documentation-task performance evidence.

Severe-loss queries vs Graphify: ["fastify-03"]. Decision blockers: ["development workload: the decision rule applies only to the frozen held-out set", "Only 30 queries; preregistered minimum is 110", "Insufficient scored queries: frozen pilot/minimum requires 110 per guardrail", "Not non-inferior: code_file_hint_recall_at_5 at 2000", "Not non-inferior: code_file_hint_recall_at_5 at 8000", "Not non-inferior: code_file_hint_recall_at_10 at 2000", "Not non-inferior: code_file_hint_recall_at_10 at 8000", "Too many queries with severe quality losses"]. Primary query-level result: {"metric": "output_tokens", "budget": 2000, "hypothesis": "candidate delivers fewer tokens than the comparator", "test": "one-sided paired sign-flip permutation on comparator minus candidate", "alpha": 0.0019230769230769232, "family": 26, "queries": 30, "mean_reduction": 740.6666666666666, "p_value": 0.0012999870001299987, "passed": true}. Future pilot requirement: 142 scored queries, never below110.

All1620 historical graf contexts were byte-identical to v54. All60 candidate contexts retained the exact v53 family context as prefix. All60 Graphify outputs matched after scratch-header normalization; raw native outputs/tokens and budget overshoots remain preserved. Both acceptance and decision exited1 (not_passed). Promotion and agent-efficiency acceptance remain false; candidate stays experimental.

126 Rust tests, 185 Python checks, fmt, Clippy, release build and exact physical source/comparison audit passed. No algorithm adjustment after freeze or results.

Same frozen sources/queries, cl100k tokenizer, requested2K/8K budgets,29 arms, one sequential repetition,1740 timing samples. Fresh indexes/prebuilt units; shared warm/uncontrolled OS caches. Timings are descriptive only. Code-only pinned Graphify produces navigation hints; graf delivers source ranges. These30 queries (27 code-annotated) are exposed development data and cannot establish product promotion. Agent savings unmeasured, zero model calls. No accepted graf version exists.

Report: `results/complement-evidence-v55-report.json`; comparison: `results/complement-evidence-v55-comparison.json`; frozen preregistration: `results/preregistrations/v55-88917bd5e67ecbe782fc4d43dcc695285626fd26f1a9ead263c1f358f28b5041.json`; evidence: `.research/evidence/complement-evidence-v55/`.


## v56: code/docs/test file interleave (development pilot)

Implemented opt-in `evidence-stratified`: form code/docs/test queues from family-ranked file anchors, keeping relevance order within each type, and drain one of each in that order, skipping empty queues. Scores are reciprocal interleaved positions; Stop policies use those scores. FileScore Max/Damped applies before interleave. Existing artifact kinds are path heuristics. Scope/kind filters precede interleave. File limits, structural selection and equal source shares are unchanged; no extra source passages. No default/MCP/schema changes.

| Requested budget | Structural v48 tokens | Families v53 tokens | Stratified v56 tokens | Graphify tokens | v48 code-file recall | v53 recall | v56 recall | Graphify recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2000 | 1496.23 | 1514.23 | 1483.73 | 2413.37 | 92.59% | 94.44% | 94.44% | 87.04% |
| 8000 | 3753.73 | 3847.30 | 3805.87 | 13003.93 | 92.59% | 94.44% | 94.44% | 87.04% |

Interpretation: Keep v56 experimental; all acceptance gates remain unpassed. Compared to v53 families, mean top5 code-file recall rises64.81% to74.07%, top10 rises79.63% to87.04%; total recall stays94.44%. Mean context tokens fall1514.23 to1483.73 at2K and3847.30 to3805.87 at8K. These descriptive v53 changes do not establish statistical superiority over v53. Top5 improves Cobra08, Cobra09 and Flask05 at both budgets, but Fastify06 regresses0.5 to0 at both budgets. All60 contexts change while exploratory source-anchor recall and every anchor-recall case remain unchanged vs v53. Compared to previous v55 complement output, two source-anchor gains are lost (Cobra04 at2K and Cobra05 at8K), with no new anchor gains. Compared to structural v48 there are3 source-anchor case gains and2 losses; every case is retained. Fastify03 remains zero code-file recall, and Fastify08 remains0.5 at both budgets: type interleaving reorders useful hits but does not solve the missing files. Graphify token primary test passes, but Holm non-inferiority at top5/top10, severe-tail cap, development-only/undersized workload and unmeasured agent outcomes prevent acceptance. No algorithm tuning after freeze or results.

Held-out planning: Current v56 pilot requires114 scored queries, never below preregistered minimum110. Existing frozen held-out set has111 code-annotated queries (123 total), insufficient for this pilot. A new recorded held-out workload must be sized and frozen before collecting new data; use at least114 scored queries for v56 alone, or the largest applicable pilot requirement (prior v53/v55 require142) if evaluating multiple candidates. Exposed development queries cannot promote.

Per-case gains and losses: {"graf_evidence_units": {"changed_context_cases": 60, "improved_code_recall_cases": 2, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 3, "worsened_source_anchor_cases": 2}, "graf_evidence_families": {"changed_context_cases": 60, "improved_code_recall_cases": 0, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 0, "worsened_source_anchor_cases": 0}, "graf_evidence_complement": {"changed_context_cases": 60, "improved_code_recall_cases": 0, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 0, "worsened_source_anchor_cases": 2}}. Every case, including losses and exact source anchors, is retained in the report.

Exploratory graf-only source-anchor recall: {"2000": {"graf_evidence_units": 0.25925925925925924, "graf_evidence_families": 0.24691358024691357, "graf_evidence_complement": 0.2654320987654321, "graf_evidence_stratified": 0.24691358024691357}, "8000": {"graf_evidence_units": 0.5802469135802469, "graf_evidence_families": 0.6111111111111112, "graf_evidence_complement": 0.6296296296296297, "graf_evidence_stratified": 0.6111111111111112}}. These author-selected development anchors are not a shared Graphify quality metric or documentation-task performance evidence.

Severe-loss queries vs Graphify: ["fastify-03"]. Decision blockers: ["development workload: the decision rule applies only to the frozen held-out set", "Only 30 queries; preregistered minimum is 110", "Insufficient scored queries: frozen pilot/minimum requires 110 per guardrail", "Not non-inferior: code_file_hint_recall_at_5 at 2000", "Not non-inferior: code_file_hint_recall_at_5 at 8000", "Not non-inferior: code_file_hint_recall_at_10 at 2000", "Not non-inferior: code_file_hint_recall_at_10 at 8000", "Too many queries with severe quality losses"]. Primary query-level result: {"metric": "output_tokens", "budget": 2000, "hypothesis": "candidate delivers fewer tokens than the comparator", "test": "one-sided paired sign-flip permutation on comparator minus candidate", "alpha": 0.001851851851851852, "family": 27, "queries": 30, "mean_reduction": 929.6333333333333, "p_value": 9.99990000099999e-06, "passed": true}. Future pilot requirement: 114 scored queries, never below110.

All1680 historical graf contexts were byte-identical to v55. All60 Graphify outputs matched after scratch-header normalization; raw native outputs/tokens and budget overshoots remain preserved. Both acceptance and decision exited1 (not_passed). Promotion and agent-efficiency acceptance remain false; candidate stays experimental.

128 Rust tests, 185 Python checks, fmt, Clippy, release build and exact physical source/comparison audit passed. No algorithm adjustment after freeze or results.

Same frozen sources/queries, cl100k tokenizer, requested2K/8K budgets,30 arms, one sequential repetition,1800 timing samples. Fresh indexes/prebuilt units; shared warm/uncontrolled OS caches. Timings are descriptive only. Code-only pinned Graphify produces navigation hints; graf delivers source ranges. These30 queries (27 code-annotated) are exposed development data and cannot establish product promotion. Agent savings unmeasured, zero model calls. No accepted graf version exists.

Report: `results/stratified-evidence-v56-report.json`; comparison: `results/stratified-evidence-v56-comparison.json`; frozen preregistration: `results/preregistrations/v56-e775208fc249ccccdd1fa16bb82f2d825b22f78d9337c34bffd3729d093a7fc6.json`; evidence: `.research/evidence/stratified-evidence-v56/`.


## v57: fused chunk and symbol file ranks (development pilot)

Implemented opt-in `evidence-fused`: equal reciprocal-rank fusion (offset60) of family chunk file ranks and cached body/call/symbol file ranks, then code/docs/test interleave and unchanged structural source packing. Files shared by both channels retain the family anchor. Scope/kind filters precede fusion. Existing call edges and symbol scores are reused; no new graph, model call, default/MCP/schema change.

| Requested budget | Structural v48 tokens | Families v53 tokens | Stratified v56 tokens | Fused v57 tokens | Graphify tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2000 | 1496.23 | 1514.23 | 1483.73 | 1466.60 | 2412.37 |
| 8000 | 3753.73 | 3847.30 | 3805.87 | 3677.97 | 13002.93 |

Shared quality (mean code-file hint recall, recall at5, recall at10), same at both budgets unless specified:
- 2000 graf_evidence_units: 92.59%, 70.37%, 85.19%.
- 2000 graf_evidence_families: 94.44%, 64.81%, 79.63%.
- 2000 graf_evidence_stratified: 94.44%, 74.07%, 87.04%.
- 2000 graf_evidence_fused: 94.44%, 87.04%, 92.59%.
- 2000 graphify: 87.04%, 70.37%, 83.33%.
- 8000 graf_evidence_units: 92.59%, 70.37%, 85.19%.
- 8000 graf_evidence_families: 94.44%, 64.81%, 79.63%.
- 8000 graf_evidence_stratified: 94.44%, 74.07%, 87.04%.
- 8000 graf_evidence_fused: 94.44%, 87.04%, 92.59%.
- 8000 graphify: 87.04%, 70.37%, 83.33%.

Interpretation: Keep v57 opt-in and experimental. On 30 exposed development queries (27 code-annotated), mean top5 code-file recall rises from v56 74.07% to87.04%, top10 from87.04% to92.59%; total recall stays94.44%. Mean context tokens fall1483.73 to1466.60 at2K and3805.87 to3677.97 at8K. These descriptive previous-version changes do not establish statistical superiority over v56. Top5 gains: Flask01(.5 to1), Fastify01(0 to1), Fastify04(0 to1), Fastify06(0 to1), Fastify07(.5 to1), both budgets. Fastify08 regresses top5 and top10 from.5 to0 at both budgets; total recall remains.5. Top10 gains Fastify01 and04 both budgets. No total code-file or source-anchor recall case changes vs v53/v56; source-anchor mean remains24.69% at2K and61.11% at8K. All60 contexts differ. Compared to structural v48 there are3 exploratory source-anchor case gains and2 losses, retained in every-case report. Fastify03 remains zero total code-file recall while Graphify finds the file; Fastify08 remains partial. Against pinned code-only Graphify, the primary token test and all6 Holm non-inferiority guardrails pass in this development diagnostic. This is not an acceptance pass: the development-only undersized sample and severe-loss Wilson upper.182835>.15 block the rule; matched natural-task agent outcomes and complete usage remain unmeasured. Both acceptance and decision exit1. No algorithm changes after freeze or results.

Held-out planning: Current v57 pilot requires110 scored queries, the preregistered minimum. The existing111-code-annotated-query held-out set has results from older arms, and cannot be assumed fresh. Freeze a new unseen natural-task workload before collecting results for the new candidate. If comparing multiple experimental candidates, use the largest applicable pilot requirement (v53/v55 require142). Development data cannot promote; retrieval alone does not establish agent efficiency.

All per-case gains and losses are retained in the report: {"graf_evidence_units": {"changed_context_cases": 60, "improved_code_recall_cases": 2, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 3, "worsened_source_anchor_cases": 2}, "graf_evidence_families": {"changed_context_cases": 60, "improved_code_recall_cases": 0, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 0, "worsened_source_anchor_cases": 0}, "graf_evidence_stratified": {"changed_context_cases": 60, "improved_code_recall_cases": 0, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 0, "worsened_source_anchor_cases": 0}}. Rank changes: {"graf_evidence_units": {"code_file_hint_recall_at_5": {"gains": 14, "losses": 0}, "code_file_hint_recall_at_10": {"gains": 4, "losses": 0}}, "graf_evidence_families": {"code_file_hint_recall_at_5": {"gains": 16, "losses": 2}, "code_file_hint_recall_at_10": {"gains": 10, "losses": 2}}, "graf_evidence_stratified": {"code_file_hint_recall_at_5": {"gains": 10, "losses": 2}, "code_file_hint_recall_at_10": {"gains": 4, "losses": 2}}, "graphify": {"code_file_hint_recall_at_5": {"gains": 14, "losses": 2}, "code_file_hint_recall_at_10": {"gains": 6, "losses": 0}}}.

Exploratory graf-only source-anchor recall: {"2000": {"graf_evidence_units": 0.25925925925925924, "graf_evidence_families": 0.24691358024691357, "graf_evidence_stratified": 0.24691358024691357, "graf_evidence_fused": 0.24691358024691357}, "8000": {"graf_evidence_units": 0.5802469135802469, "graf_evidence_families": 0.6111111111111112, "graf_evidence_stratified": 0.6111111111111112, "graf_evidence_fused": 0.6111111111111112}}. These author-selected development anchors are not a shared Graphify metric or documentation-task performance evidence.

Incomplete candidate file cases: [{"id": "fastify-03", "budget": 2000, "candidate_recall": 0.0, "graphify_recall": 1.0}, {"id": "fastify-03", "budget": 8000, "candidate_recall": 0.0, "graphify_recall": 1.0}, {"id": "fastify-08", "budget": 2000, "candidate_recall": 0.5, "graphify_recall": 0.0}, {"id": "fastify-08", "budget": 8000, "candidate_recall": 0.5, "graphify_recall": 0.0}]. Severe-loss queries vs Graphify: ["fastify-03"]. Decision blockers: ["development workload: the decision rule applies only to the frozen held-out set", "Only 30 queries; preregistered minimum is 110", "Insufficient scored queries: frozen pilot/minimum requires 110 per guardrail", "Too many queries with severe quality losses"]. Primary query-level result: {"metric": "output_tokens", "budget": 2000, "hypothesis": "candidate delivers fewer tokens than the comparator", "test": "one-sided paired sign-flip permutation on comparator minus candidate", "alpha": 0.0017857142857142859, "family": 28, "queries": 30, "mean_reduction": 945.7666666666667, "p_value": 9.99990000099999e-06, "passed": true}.

All1740 historical graf contexts were byte-identical to v56. All60 Graphify outputs matched after scratch-header normalization; raw native outputs/tokens and budget overshoots remain preserved. Both acceptance and decision exited1 (not_passed). Promotion and agent-efficiency acceptance remain false; candidate stays experimental.

131 Rust tests, 185 Python checks, fmt, Clippy, release build and exact physical source/comparison audit passed. No algorithm adjustment after freeze or results.

Same frozen sources/queries, cl100k tokenizer, requested2K/8K budgets,31 arms, one sequential repetition,1860 timing samples. Fresh indexes/prebuilt units; shared warm/uncontrolled OS caches. Timings are descriptive only. Code-only pinned Graphify produces navigation hints; graf delivers source ranges. These30 queries (27 code-annotated) are exposed development data and cannot establish product promotion. Agent savings unmeasured, zero model calls. No accepted graf version exists.

Report: `results/fused-evidence-v57-report.json`; comparison: `results/fused-evidence-v57-comparison.json`; frozen preregistration: `results/preregistrations/v57-38b0fc5b776d6a7d520418f4f70bd0800ce84348f173f8316f2232297d7e4bf4.json`; evidence: `.research/evidence/fused-evidence-v57/`.


## v58: explicit documentation symbol references (development pilot)

Implemented opt-in `evidence-referenced`: add a third reciprocal-rank channel from explicit names in the top8 matching documentation chunks to globally unique indexed code-function definitions. Each seed scans at most4096 Unicode scalar characters and32 distinct names (ASCII, length5..128, standalone backticks or calls); at most8 target files receive a vote. Repeated references do not accumulate. Seeds respect scope; targets respect scope/kind. Existing anchors are retained; new files anchor to the referenced function chunk. File interleave and structural packing are unchanged; no new graph, model call, default/MCP/schema change. Historical fusion uses the shared helper with references disabled.

| Requested budget | Structural v48 tokens | Families v53 tokens | Fused v57 tokens | Referenced v58 tokens | Graphify tokens |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2000 | 1496.23 | 1514.23 | 1466.60 | 1465.47 | 2413.37 |
| 8000 | 3753.73 | 3847.30 | 3677.97 | 3643.70 | 13003.93 |

Shared quality (mean code-file hint recall, recall at5, recall at10), same at both budgets unless specified:

- 2000 graf_evidence_units: 92.59%, 70.37%, 85.19%.
- 2000 graf_evidence_families: 94.44%, 64.81%, 79.63%.
- 2000 graf_evidence_fused: 94.44%, 87.04%, 92.59%.
- 2000 graf_evidence_referenced: 98.15%, 64.81%, 87.04%.
- 2000 graphify: 87.04%, 70.37%, 83.33%.
- 8000 graf_evidence_units: 92.59%, 70.37%, 85.19%.
- 8000 graf_evidence_families: 94.44%, 64.81%, 79.63%.
- 8000 graf_evidence_fused: 94.44%, 87.04%, 92.59%.
- 8000 graf_evidence_referenced: 98.15%, 64.81%, 87.04%.
- 8000 graphify: 87.04%, 70.37%, 83.33%.

Interpretation: Do not replace v57 ranking with v58. Keep this reference channel opt-in and experimental because of ranking regressions. On30 exposed development queries (27 code-annotated), total code-file recall rises94.44% to98.15% solely by recovering Fastify03 lib/decorate.js at both budgets; there are no total-recall losses vs v57. Fastify08 remains partial(.5). Mean top5 recall falls87.04% to64.81%, below pinned Graphify70.37%; top10 falls92.59% to87.04%, Graphify83.33%. Top5 has0 gains/16 query-budget losses vs v57: Flask02(.5 to0), Flask04(1 to0), Flask05(1 to0), Flask07(1 to.5), Flask08(1 to.5), Fastify02(1 to0), Fastify05(1 to0), Fastify06(1 to.5), each at both budgets. Top10 has2 gains(Fastify03 both budgets) and8 losses(Flask02/05/07/08 both budgets); all cases retained. New references raise ctx.py/helpers.py ahead of sessions.py in Flask04 and fastify.js/content-type-parser.js ahead of route.js in Fastify02. The recovered Fastify03 source range is lib/decorate.js137-142 at both budgets: decorateRequest calls checkReferenceType, but the author-selected guard at69 is still missing. All exploratory source-anchor recall cases are unchanged vs v57, mean24.69% at2K and61.11% at8K. Compared to structural v48 there are3 anchor-case gains and2 losses inherited from earlier experiments, explicitly retained.48 of60 contexts change vs v57. Mean context tokens1465.47 at2K/3643.70 at8K versus v57 1466.60/3677.97; lower output cannot compensate for lost early recall. Against Graphify primary token diagnostic and total-recall Holm NI pass, severe-loss set is now empty and Wilson upper.124555 is below.15. Top5/top10 Holm NI fail at both budgets; development-only, undersized sample and missing matched natural agent outcomes prevent acceptance. Acceptance and decision both exit1. No algorithm changes after freeze or results.

Held-out planning: Current v58 pilot requires165 scored queries, above preregistered minimum110 and above the existing111-code-annotated-query held-out set (123 total). That set also has results from older arms and cannot be assumed fresh. Freeze a new unseen natural-task workload with at least165 scored queries before data collection for v58; use the largest applicable requirement if evaluating multiple candidates. Development results cannot promote, and retrieval-only results do not measure agent efficiency.

All per-case gains and losses are retained in the report: {"graf_evidence_units": {"changed_context_cases": 60, "improved_code_recall_cases": 4, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 3, "worsened_source_anchor_cases": 2}, "graf_evidence_families": {"changed_context_cases": 60, "improved_code_recall_cases": 2, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 0, "worsened_source_anchor_cases": 0}, "graf_evidence_fused": {"changed_context_cases": 48, "improved_code_recall_cases": 2, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 0, "worsened_source_anchor_cases": 0}}. Rank changes: {"graf_evidence_units": {"code_file_hint_recall_at_5": {"gains": 8, "losses": 10}, "code_file_hint_recall_at_10": {"gains": 6, "losses": 8}}, "graf_evidence_families": {"code_file_hint_recall_at_5": {"gains": 12, "losses": 14}, "code_file_hint_recall_at_10": {"gains": 12, "losses": 10}}, "graf_evidence_fused": {"code_file_hint_recall_at_5": {"gains": 0, "losses": 16}, "code_file_hint_recall_at_10": {"gains": 2, "losses": 8}}, "graphify": {"code_file_hint_recall_at_5": {"gains": 10, "losses": 12}, "code_file_hint_recall_at_10": {"gains": 8, "losses": 8}}}.

Exploratory graf-only source-anchor recall: {"2000": {"graf_evidence_units": 0.25925925925925924, "graf_evidence_families": 0.24691358024691357, "graf_evidence_fused": 0.24691358024691357, "graf_evidence_referenced": 0.24691358024691357}, "8000": {"graf_evidence_units": 0.5802469135802469, "graf_evidence_families": 0.6111111111111112, "graf_evidence_fused": 0.6111111111111112, "graf_evidence_referenced": 0.6111111111111112}}. These author-selected development anchors are not a shared Graphify metric or documentation-task performance evidence.

Incomplete candidate file cases: [{"id": "fastify-08", "budget": 2000, "candidate_recall": 0.5, "graphify_recall": 0.0}, {"id": "fastify-08", "budget": 8000, "candidate_recall": 0.5, "graphify_recall": 0.0}]. Severe-loss queries vs Graphify: []. Decision blockers: ["development workload: the decision rule applies only to the frozen held-out set", "Only 30 queries; preregistered minimum is 110", "Insufficient scored queries: frozen pilot/minimum requires 110 per guardrail", "Not non-inferior: code_file_hint_recall_at_5 at 2000", "Not non-inferior: code_file_hint_recall_at_5 at 8000", "Not non-inferior: code_file_hint_recall_at_10 at 2000", "Not non-inferior: code_file_hint_recall_at_10 at 8000"]. Primary query-level result: {"metric": "output_tokens", "budget": 2000, "hypothesis": "candidate delivers fewer tokens than the comparator", "test": "one-sided paired sign-flip permutation on comparator minus candidate", "alpha": 0.001724137931034483, "family": 29, "queries": 30, "mean_reduction": 947.9, "p_value": 9.99990000099999e-06, "passed": true}.

All1800 historical graf contexts were byte-identical to v57. All60 Graphify outputs matched after scratch-header normalization; raw native outputs/tokens and budget overshoots remain preserved. Both acceptance and decision exited1 (not_passed). Promotion and agent-efficiency acceptance remain false; candidate stays experimental.

137 Rust tests, 185 Python checks, fmt, Clippy, release build and exact physical source/comparison audit passed. No algorithm adjustment after freeze or results.

Same frozen sources/queries, cl100k tokenizer, requested2K/8K budgets,32 arms, one sequential repetition,1920 timing samples. Fresh indexes/prebuilt units; shared warm/uncontrolled OS caches. Timings are descriptive only. Code-only pinned Graphify produces navigation hints; graf delivers source ranges. These30 queries (27 code-annotated) are exposed development data and cannot establish product promotion. Agent savings unmeasured, zero model calls. No accepted graf version exists.

Report: `results/referenced-evidence-v58-report.json`; comparison: `results/referenced-evidence-v58-comparison.json`; frozen preregistration: `results/preregistrations/v58-b8728f1e50d416f5c240643c0ab60b9d45d1c728046b1ebab3705d9fd1f9e960.json`; evidence: `.research/evidence/referenced-evidence-v58/`.


## v59: append-only documentation reference rescue (development pilot)

Implemented opt-in `evidence-rescue`: preserve the entire v57 fused output as a byte-identical prefix and append fitting whole functions from up to four new code files. Reuse the bounded v58 documentation-reference resolver unchanged. Skip ambiguous, already delivered, non-fitting or physically inconsistent targets. Scope and kind filters and source-hash checks apply. No new graph, model call, default/MCP/schema change.

| Requested budget | Structural v48 tokens | Families v53 tokens | Fused v57 tokens | Referenced v58 tokens | Rescue v59 tokens | Graphify tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2000 | 1496.23 | 1514.23 | 1466.60 | 1465.47 | 1557.33 | 2412.37 |
| 8000 | 3753.73 | 3847.30 | 3677.97 | 3643.70 | 3896.30 | 13002.93 |

Shared quality (mean code-file hint recall, recall at5, recall at10), same at both budgets unless specified:

- 2000 graf_evidence_units: 92.59%, 70.37%, 85.19%.
- 2000 graf_evidence_families: 94.44%, 64.81%, 79.63%.
- 2000 graf_evidence_fused: 94.44%, 87.04%, 92.59%.
- 2000 graf_evidence_referenced: 98.15%, 64.81%, 87.04%.
- 2000 graf_evidence_rescue: 98.15%, 87.04%, 92.59%.
- 2000 graphify: 87.04%, 70.37%, 83.33%.
- 8000 graf_evidence_units: 92.59%, 70.37%, 85.19%.
- 8000 graf_evidence_families: 94.44%, 64.81%, 79.63%.
- 8000 graf_evidence_fused: 94.44%, 87.04%, 92.59%.
- 8000 graf_evidence_referenced: 98.15%, 64.81%, 87.04%.
- 8000 graf_evidence_rescue: 98.15%, 87.04%, 92.59%.
- 8000 graphify: 87.04%, 70.37%, 83.33%.

Interpretation: All60 v59 contexts preserve the exact v57 fused prefix. On this exposed development set, total code-file recall improves94.44% to98.15%, with the only gain Fastify03 at both budgets and no file-recall losses against v57. Top5 stays87.04% and top10 stays92.59% at both budgets, avoiding v58 ranking regressions. Mean output rises from1466.60 to1557.33 tokens at2K and3677.97 to3896.30 at8K. No exploratory source-anchor recall changes versus v57 or v58: Fastify03 now includes lib/decorate.js137-142, but still omits object guard line69, and Fastify08 remains half complete. The primary token test, all six Holm quality non-inferiority checks and severe-loss tail pass as development diagnostics. Acceptance and decision both exit1/not_passed because the workload is exposed and undersized. No accepted graf version exists; keep opt-in, defaults unchanged. Agent savings remain unmeasured.

Held-out planning: Current v59 pilot requires 110 scored queries, never below the preregistered minimum110. The existing111-code-annotated/123-total held-out set already has older-arm results and cannot automatically be treated as fresh. Freeze a new unseen supported workload and size it from applicable pilot variance before collecting data. If comparing multiple candidates, use the largest applicable requirement. The30 development queries were used for tuning and cannot promote; natural agent tasks and complete usage accounting remain required.

All per-case gains and losses are retained in the report: {"graf_evidence_units": {"changed_context_cases": 60, "improved_code_recall_cases": 4, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 3, "worsened_source_anchor_cases": 2}, "graf_evidence_families": {"changed_context_cases": 60, "improved_code_recall_cases": 2, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 0, "worsened_source_anchor_cases": 0}, "graf_evidence_fused": {"changed_context_cases": 34, "improved_code_recall_cases": 2, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 0, "worsened_source_anchor_cases": 0}, "graf_evidence_referenced": {"changed_context_cases": 48, "improved_code_recall_cases": 0, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 0, "worsened_source_anchor_cases": 0}}. Rank changes: {"graf_evidence_units": {"code_file_hint_recall_at_5": {"gains": 14, "losses": 0}, "code_file_hint_recall_at_10": {"gains": 4, "losses": 0}}, "graf_evidence_families": {"code_file_hint_recall_at_5": {"gains": 16, "losses": 2}, "code_file_hint_recall_at_10": {"gains": 10, "losses": 2}}, "graf_evidence_fused": {"code_file_hint_recall_at_5": {"gains": 0, "losses": 0}, "code_file_hint_recall_at_10": {"gains": 0, "losses": 0}}, "graf_evidence_referenced": {"code_file_hint_recall_at_5": {"gains": 16, "losses": 0}, "code_file_hint_recall_at_10": {"gains": 8, "losses": 2}}, "graphify": {"code_file_hint_recall_at_5": {"gains": 14, "losses": 2}, "code_file_hint_recall_at_10": {"gains": 6, "losses": 0}}}.

Exploratory graf-only source-anchor recall: {"2000": {"graf_evidence_units": 0.25925925925925924, "graf_evidence_families": 0.24691358024691357, "graf_evidence_fused": 0.24691358024691357, "graf_evidence_referenced": 0.24691358024691357, "graf_evidence_rescue": 0.24691358024691357}, "8000": {"graf_evidence_units": 0.5802469135802469, "graf_evidence_families": 0.6111111111111112, "graf_evidence_fused": 0.6111111111111112, "graf_evidence_referenced": 0.6111111111111112, "graf_evidence_rescue": 0.6111111111111112}}. These author-selected development anchors are not a shared Graphify metric or documentation-task performance evidence.

Incomplete candidate file cases: [{"id": "fastify-08", "budget": 2000, "candidate_recall": 0.5, "graphify_recall": 0.0}, {"id": "fastify-08", "budget": 8000, "candidate_recall": 0.5, "graphify_recall": 0.0}]. Severe-loss queries vs Graphify: []. Decision blockers: ["development workload: the decision rule applies only to the frozen held-out set", "Only 30 queries; preregistered minimum is 110", "Insufficient scored queries: frozen pilot/minimum requires 110 per guardrail"]. Primary query-level result: {"metric": "output_tokens", "budget": 2000, "hypothesis": "candidate delivers fewer tokens than the comparator", "test": "one-sided paired sign-flip permutation on comparator minus candidate", "alpha": 0.0016666666666666668, "family": 30, "queries": 30, "mean_reduction": 855.0333333333333, "p_value": 2.999970000299997e-05, "passed": true}.

All1860 historical graf contexts were byte-identical to v58. All60 Graphify outputs matched after scratch-header normalization; raw native outputs/tokens and budget overshoots remain preserved. Both acceptance and decision exited1 (not_passed). Promotion and agent-efficiency acceptance remain false; candidate stays experimental.

143 Rust tests, 185 Python checks, fmt, Clippy, release build and exact physical source/comparison audit passed. No algorithm adjustment after freeze or results.

Same frozen sources/queries, cl100k tokenizer, requested2K/8K budgets,33 arms, one sequential repetition,1980 timing samples. Fresh indexes/prebuilt units; shared warm/uncontrolled OS caches. Timings are descriptive only. Code-only pinned Graphify produces navigation hints; graf delivers source ranges. These30 queries (27 code-annotated) are exposed development data and cannot establish product promotion. Agent savings unmeasured, zero model calls. No accepted graf version exists.

Report: `results/rescue-evidence-v59-report.json`; comparison: `results/rescue-evidence-v59-comparison.json`; frozen preregistration: `results/preregistrations/v59-f7ca03e232b762392887a811b6281124b609b690d05dcf4595509b77d1fcf97c.json`; evidence: `.research/evidence/rescue-evidence-v59/`.


## v60: append-only direct local callees (development pilot)

Implemented opt-in `evidence-callees`: preserve complete v59 rescue prefix and append up to four fitting whole direct local callees of fully delivered functions. Deduplicate targets and reject overlapping ranges. Rank by original-query body BM25, stable unit-order ties, check only first32 candidates. Existing cached call edges only; no recursion or cross-file expansion, no new files, graph, model call, default/MCP/schema change.

| Requested budget | Structural v48 tokens | Families v53 tokens | Fused v57 tokens | Referenced v58 tokens | Rescue v59 tokens | Callees v60 tokens | Graphify tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2000 | 1496.23 | 1514.23 | 1466.60 | 1465.47 | 1557.33 | 1798.97 | 2413.37 |
| 8000 | 3753.73 | 3847.30 | 3677.97 | 3643.70 | 3896.30 | 4913.60 | 13003.93 |

Shared quality (mean code-file hint recall, recall at5, recall at10), same at both budgets unless specified:

- 2000 graf_evidence_units: 92.59%, 70.37%, 85.19%.
- 2000 graf_evidence_families: 94.44%, 64.81%, 79.63%.
- 2000 graf_evidence_fused: 94.44%, 87.04%, 92.59%.
- 2000 graf_evidence_referenced: 98.15%, 64.81%, 87.04%.
- 2000 graf_evidence_rescue: 98.15%, 87.04%, 92.59%.
- 2000 graf_evidence_callees: 98.15%, 87.04%, 92.59%.
- 2000 graphify: 87.04%, 70.37%, 83.33%.
- 8000 graf_evidence_units: 92.59%, 70.37%, 85.19%.
- 8000 graf_evidence_families: 94.44%, 64.81%, 79.63%.
- 8000 graf_evidence_fused: 94.44%, 87.04%, 92.59%.
- 8000 graf_evidence_referenced: 98.15%, 64.81%, 87.04%.
- 8000 graf_evidence_rescue: 98.15%, 87.04%, 92.59%.
- 8000 graf_evidence_callees: 98.15%, 87.04%, 92.59%.
- 8000 graphify: 87.04%, 70.37%, 83.33%.

Interpretation: All60 candidate contexts preserve the exact v59 rescue prefix, and all1920 historical graf contexts remain unchanged. Shared code-file recall98.15%, top5 recall87.04%, and top10 recall92.59% are unchanged versus v59 at both budgets. Mean retrieval text rises1557.33 to1798.97 tokens at2K and3896.30 to4913.60 at8K. Exploratory graf-only source-anchor recall rises24.69% to26.54% at2K and61.11% to64.81% at8K: gains are Fastify03 both budgets (now reaches lib/decorate.js object guard69) and Flask05 at8K, with no source-anchor losses versus v59. Context changes in57/60 cases for these3 query-budget anchor gains. The primary token test fails: paired p0.0171298 versus family-corrected alpha0.00161290, despite lower mean tokens than Graphify. All six shared quality non-inferiority checks and severe-loss tail pass as development diagnostics. Both acceptance and decision exit1/not_passed; development data are exposed and undersized. Do not replace v59 with v60: retain this coverage/cost tradeoff as an opt-in experiment, defaults unchanged. Agent savings remain unmeasured.

Held-out planning: Current v60 pilot requires 110 scored queries, never below the preregistered minimum110. The existing111-code-annotated/123-total held-out set already has older-arm results and cannot automatically be treated as fresh. Freeze a new unseen supported workload and size it from applicable pilot variance before collecting data. If comparing multiple candidates, use the largest applicable requirement. The30 development queries were used for tuning and cannot promote; natural agent tasks and complete usage accounting remain required.

All per-case gains and losses are retained in the report: {"graf_evidence_units": {"changed_context_cases": 60, "improved_code_recall_cases": 4, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 6, "worsened_source_anchor_cases": 2}, "graf_evidence_families": {"changed_context_cases": 60, "improved_code_recall_cases": 2, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 3, "worsened_source_anchor_cases": 0}, "graf_evidence_fused": {"changed_context_cases": 59, "improved_code_recall_cases": 2, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 3, "worsened_source_anchor_cases": 0}, "graf_evidence_referenced": {"changed_context_cases": 59, "improved_code_recall_cases": 0, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 3, "worsened_source_anchor_cases": 0}, "graf_evidence_rescue": {"changed_context_cases": 57, "improved_code_recall_cases": 0, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 3, "worsened_source_anchor_cases": 0}}. Rank changes: {"graf_evidence_units": {"code_file_hint_recall_at_5": {"gains": 14, "losses": 0}, "code_file_hint_recall_at_10": {"gains": 4, "losses": 0}}, "graf_evidence_families": {"code_file_hint_recall_at_5": {"gains": 16, "losses": 2}, "code_file_hint_recall_at_10": {"gains": 10, "losses": 2}}, "graf_evidence_fused": {"code_file_hint_recall_at_5": {"gains": 0, "losses": 0}, "code_file_hint_recall_at_10": {"gains": 0, "losses": 0}}, "graf_evidence_referenced": {"code_file_hint_recall_at_5": {"gains": 16, "losses": 0}, "code_file_hint_recall_at_10": {"gains": 8, "losses": 2}}, "graf_evidence_rescue": {"code_file_hint_recall_at_5": {"gains": 0, "losses": 0}, "code_file_hint_recall_at_10": {"gains": 0, "losses": 0}}, "graphify": {"code_file_hint_recall_at_5": {"gains": 14, "losses": 2}, "code_file_hint_recall_at_10": {"gains": 6, "losses": 0}}}.

Exploratory graf-only source-anchor recall: {"2000": {"graf_evidence_units": 0.25925925925925924, "graf_evidence_families": 0.24691358024691357, "graf_evidence_fused": 0.24691358024691357, "graf_evidence_referenced": 0.24691358024691357, "graf_evidence_rescue": 0.24691358024691357, "graf_evidence_callees": 0.2654320987654321}, "8000": {"graf_evidence_units": 0.5802469135802469, "graf_evidence_families": 0.6111111111111112, "graf_evidence_fused": 0.6111111111111112, "graf_evidence_referenced": 0.6111111111111112, "graf_evidence_rescue": 0.6111111111111112, "graf_evidence_callees": 0.6481481481481481}}. These author-selected development anchors are not a shared Graphify metric or documentation-task performance evidence.

Incomplete candidate file cases: [{"id": "fastify-08", "budget": 2000, "candidate_recall": 0.5, "graphify_recall": 0.0}, {"id": "fastify-08", "budget": 8000, "candidate_recall": 0.5, "graphify_recall": 0.0}]. Severe-loss queries vs Graphify: []. Decision blockers: ["development workload: the decision rule applies only to the frozen held-out set", "Only 30 queries; preregistered minimum is 110", "Insufficient scored queries: frozen pilot/minimum requires 110 per guardrail", "No significant reduction in the primary metric"]. Primary query-level result: {"metric": "output_tokens", "budget": 2000, "hypothesis": "candidate delivers fewer tokens than the comparator", "test": "one-sided paired sign-flip permutation on comparator minus candidate", "alpha": 0.0016129032258064516, "family": 31, "queries": 30, "mean_reduction": 614.4, "p_value": 0.017129828701712983, "passed": false}.

All1920 historical graf contexts were byte-identical to v59. All60 Graphify outputs matched after scratch-header normalization; raw native outputs/tokens and budget overshoots remain preserved. Both acceptance and decision exited1 (not_passed). Promotion and agent-efficiency acceptance remain false; candidate stays experimental.

151 Rust tests, 185 Python checks, fmt, Clippy, release build and exact physical source/comparison audit passed. No algorithm adjustment after freeze or results.

Same frozen sources/queries, cl100k tokenizer, requested2K/8K budgets,34 arms, one sequential repetition,2040 timing samples. Fresh indexes/prebuilt units; shared warm/uncontrolled OS caches. Timings are descriptive only. Code-only pinned Graphify produces navigation hints; graf delivers source ranges. These30 queries (27 code-annotated) are exposed development data and cannot establish product promotion. Agent savings unmeasured, zero model calls. No accepted graf version exists.

Report: `results/callees-evidence-v60-report.json`; comparison: `results/callees-evidence-v60-comparison.json`; frozen preregistration: `results/preregistrations/v60-9a4aaf0ce2d1482b15258bc5e672c6b39beecefe031f52c3662c7fb7173ab515.json`; evidence: `.research/evidence/callees-evidence-v60/`.


## v61: selective local callee with bounded token allowance (development pilot)

Implemented opt-in `evidence-selective`: preserve the complete v59 rescue prefix, then append at most one fitting direct local callee with positive original-query unit-body BM25. Additional tokens measured on combined context cannot exceed min(floor(budget/10),256). Check first32 positive candidates by score then unit order. Physical source checks, overlap rejection and existing local edges unchanged; no recursion, cross-file expansion, new files, model calls, default/MCP/schema change.

| Requested budget | Structural v48 tokens | Families v53 tokens | Fused v57 tokens | Referenced v58 tokens | Rescue v59 tokens | Callees v60 tokens | Selective v61 tokens | Graphify tokens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2000 | 1496.23 | 1514.23 | 1466.60 | 1465.47 | 1557.33 | 1798.97 | 1631.43 | 2412.37 |
| 8000 | 3753.73 | 3847.30 | 3677.97 | 3643.70 | 3896.30 | 4913.60 | 3985.40 | 13002.93 |

Shared quality (mean code-file hint recall, recall at5, recall at10), same at both budgets unless specified:

- 2000 graf_evidence_units: 92.59%, 70.37%, 85.19%.
- 2000 graf_evidence_families: 94.44%, 64.81%, 79.63%.
- 2000 graf_evidence_fused: 94.44%, 87.04%, 92.59%.
- 2000 graf_evidence_referenced: 98.15%, 64.81%, 87.04%.
- 2000 graf_evidence_rescue: 98.15%, 87.04%, 92.59%.
- 2000 graf_evidence_callees: 98.15%, 87.04%, 92.59%.
- 2000 graf_evidence_selective: 98.15%, 87.04%, 92.59%.
- 2000 graphify: 87.04%, 70.37%, 83.33%.
- 8000 graf_evidence_units: 92.59%, 70.37%, 85.19%.
- 8000 graf_evidence_families: 94.44%, 64.81%, 79.63%.
- 8000 graf_evidence_fused: 94.44%, 87.04%, 92.59%.
- 8000 graf_evidence_referenced: 98.15%, 64.81%, 87.04%.
- 8000 graf_evidence_rescue: 98.15%, 87.04%, 92.59%.
- 8000 graf_evidence_callees: 98.15%, 87.04%, 92.59%.
- 8000 graf_evidence_selective: 98.15%, 87.04%, 92.59%.
- 8000 graphify: 87.04%, 70.37%, 83.33%.

Interpretation: All60 v61 contexts preserve the exact v59 rescue prefix, and all1980 historical graf contexts remain unchanged. Mean retrieval tokens fall from v60 1798.97 to1631.43 at2K and4913.60 to3985.40 at8K;52 query-budget cases use fewer tokens and8 are unchanged, none higher. Every exploratory graf-only source-anchor score matches v60:26.54% at2K and64.81% at8K. This retains the three v60 gains over v59 (Fastify03 both budgets, Flask05 at8K) without source-anchor losses; Fastify03 object guard69 is delivered both budgets. Shared code-file recall98.15%, top5 recall87.04% and top10 recall92.59% stay unchanged versus v59/v60. All60 cases satisfy the one-range and min(10% requested budget,256) added-token caps. Compared with code-only Graphify, the primary paired token test passes p0.000499995 versus family-corrected alpha0.0015625; all six Holm quality non-inferiority guardrails and severe-loss tail pass as development diagnostics. Both gates still exit1/not_passed: these30 queries are exposed and undersized, pilot requires at least110 fresh scored queries. Retain v61 as an opt-in candidate for newly frozen held-out evaluation, defaults unchanged. No product superiority or agent savings established; agent savings remain unmeasured.

Held-out planning: Current v61 pilot requires 110 scored queries, never below the preregistered minimum110. The existing111-code-annotated/123-total held-out set already has older-arm results and cannot automatically be treated as fresh. Freeze a new unseen supported workload and size it from applicable pilot variance before collecting data. If comparing multiple candidates, use the largest applicable requirement. The30 development queries were used for tuning and cannot promote; natural agent tasks and complete usage accounting remain required.

All per-case gains and losses are retained in the report: {"graf_evidence_units": {"changed_context_cases": 60, "improved_code_recall_cases": 4, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 6, "worsened_source_anchor_cases": 2}, "graf_evidence_families": {"changed_context_cases": 60, "improved_code_recall_cases": 2, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 3, "worsened_source_anchor_cases": 0}, "graf_evidence_fused": {"changed_context_cases": 53, "improved_code_recall_cases": 2, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 3, "worsened_source_anchor_cases": 0}, "graf_evidence_referenced": {"changed_context_cases": 58, "improved_code_recall_cases": 0, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 3, "worsened_source_anchor_cases": 0}, "graf_evidence_rescue": {"changed_context_cases": 47, "improved_code_recall_cases": 0, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 3, "worsened_source_anchor_cases": 0}, "graf_evidence_callees": {"changed_context_cases": 52, "improved_code_recall_cases": 0, "worsened_code_recall_cases": 0, "improved_source_anchor_cases": 0, "worsened_source_anchor_cases": 0}}. Rank changes: {"graf_evidence_units": {"code_file_hint_recall_at_5": {"gains": 14, "losses": 0}, "code_file_hint_recall_at_10": {"gains": 4, "losses": 0}}, "graf_evidence_families": {"code_file_hint_recall_at_5": {"gains": 16, "losses": 2}, "code_file_hint_recall_at_10": {"gains": 10, "losses": 2}}, "graf_evidence_fused": {"code_file_hint_recall_at_5": {"gains": 0, "losses": 0}, "code_file_hint_recall_at_10": {"gains": 0, "losses": 0}}, "graf_evidence_referenced": {"code_file_hint_recall_at_5": {"gains": 16, "losses": 0}, "code_file_hint_recall_at_10": {"gains": 8, "losses": 2}}, "graf_evidence_rescue": {"code_file_hint_recall_at_5": {"gains": 0, "losses": 0}, "code_file_hint_recall_at_10": {"gains": 0, "losses": 0}}, "graf_evidence_callees": {"code_file_hint_recall_at_5": {"gains": 0, "losses": 0}, "code_file_hint_recall_at_10": {"gains": 0, "losses": 0}}, "graphify": {"code_file_hint_recall_at_5": {"gains": 14, "losses": 2}, "code_file_hint_recall_at_10": {"gains": 6, "losses": 0}}}.

Exploratory graf-only source-anchor recall: {"2000": {"graf_evidence_units": 0.25925925925925924, "graf_evidence_families": 0.24691358024691357, "graf_evidence_fused": 0.24691358024691357, "graf_evidence_referenced": 0.24691358024691357, "graf_evidence_rescue": 0.24691358024691357, "graf_evidence_callees": 0.2654320987654321, "graf_evidence_selective": 0.2654320987654321}, "8000": {"graf_evidence_units": 0.5802469135802469, "graf_evidence_families": 0.6111111111111112, "graf_evidence_fused": 0.6111111111111112, "graf_evidence_referenced": 0.6111111111111112, "graf_evidence_rescue": 0.6111111111111112, "graf_evidence_callees": 0.6481481481481481, "graf_evidence_selective": 0.6481481481481481}}. These author-selected development anchors are not a shared Graphify metric or documentation-task performance evidence.

Incomplete candidate file cases: [{"id": "fastify-08", "budget": 2000, "candidate_recall": 0.5, "graphify_recall": 0.0}, {"id": "fastify-08", "budget": 8000, "candidate_recall": 0.5, "graphify_recall": 0.0}]. Severe-loss queries vs Graphify: []. Decision blockers: ["development workload: the decision rule applies only to the frozen held-out set", "Only 30 queries; preregistered minimum is 110", "Insufficient scored queries: frozen pilot/minimum requires 110 per guardrail"]. Primary query-level result: {"metric": "output_tokens", "budget": 2000, "hypothesis": "candidate delivers fewer tokens than the comparator", "test": "one-sided paired sign-flip permutation on comparator minus candidate", "alpha": 0.0015625, "family": 32, "queries": 30, "mean_reduction": 780.9333333333333, "p_value": 0.0004999950000499995, "passed": true}.

All1980 historical graf contexts were byte-identical to v60. All60 Graphify outputs matched after scratch-header normalization; raw native outputs/tokens and budget overshoots remain preserved. Both acceptance and decision exited1 (not_passed). Promotion and agent-efficiency acceptance remain false; candidate stays experimental.

159 Rust tests, 185 Python checks, fmt, Clippy, release build and exact physical source/comparison audit passed. No algorithm adjustment after freeze or results.

Same frozen sources/queries, cl100k tokenizer, requested2K/8K budgets,35 arms, one sequential repetition,2100 timing samples. Fresh indexes/prebuilt units; shared warm/uncontrolled OS caches. Timings are descriptive only. Code-only pinned Graphify produces navigation hints; graf delivers source ranges. These30 queries (27 code-annotated) are exposed development data and cannot establish product promotion. Agent savings unmeasured, zero model calls. No accepted graf version exists.

Report: `results/selective-evidence-v61-report.json`; comparison: `results/selective-evidence-v61-comparison.json`; frozen preregistration: `results/preregistrations/v61-5eef731775d68b3086f42abf509904a9dbafcf8db9e73f0301c4a44db050909c.json`; evidence: `.research/evidence/selective-evidence-v61/`.


## v62: fresh confirmation preparation (no retrieval run)

Prepared a source-only question-authoring packet for newly pinned Zerolog
(`20ca4734f29c37a6781c3c502442c349b306dee3`, Go), Rich
(`9d8f9a372cc5916fd4781fec207ced7ddac2f08f`, Python), and Pino
(`b29dbe97820630e7128e464c50263b427965ec99`, JavaScript). These identities
are absent from the recorded prior benchmark input/snapshot inventory; this
local check is not proof of global independence. Public archives and hashes
are retained. The packet contains 581 source files,
file hashes and authoring instructions, with no retrieval outputs, candidate
implementation or tuning notes. Packet hash: `22a032fa5959f0357cbc893308b8e43f544f376da6cde9ddd0fbd1f53f1c457a`.

The prospective plan targets 123 questions, 111 with code evidence, satisfying
the current v61/v59 pilot minimum of 110 scored queries. It retains the 32-arm
historical primary correction, six Holm quality guardrails and severe-loss
cap. V61 is the candidate, v59 an experimental ablation control, and unchanged
pinned code-only Graphify the comparator; no accepted graf release exists.
Author-selected anchors remain exploratory and are not shared Graphify quality.

Added `benchmark/confirmation.py` to export the isolated packet and validate
question schema, repository quotas, duplicate/historical questions, exact
source hashes/anchors, provenance declarations, negative-control rationales,
and the scored-query minimum. It rejects altered packet requirements and
source/symlink substitution. A declaration cannot prove independence or
relevance completeness; a separate source review and later blind pooled
judgments remain required. All 199 Python checks pass (14 new packet checks).

**Preparation complete; question workload not frozen, comparison not run.**
Independent source-only authorship and review are still pending. The existing
comparison driver is restricted to the original three repositories and must
be generalized, tested and frozen with the new question hash before collection.
The old held-out workload is preserved and must not be relabeled fresh.
No retrieval implementation/default change, no new acceptance decision, no
agent model calls, and no new performance claims. V61 remains experimental;
the existing `acceptance.json` and `decision.json` remain not_passed.

Spec: `benchmark/confirmation-v62.json`; source packet:
`.research/heldout-v62-authoring/`; readiness:
`results/confirmation-v62-readiness.json`; frozen preparation:
`results/preregistrations/v62-855a0150cf711bac94faac17433c3910cbcac28ad1d720037f6f9694ae114534.json`.


### v63 harness readiness

Authenticated run plans now select frozen corpora, questions, source sets, binaries and comparison arms. The 211-test Python suite passes, and the development parity run preserves every Graf context from v61. Both acceptance and decision gates remain unpassed. Before fresh retrieval data, finish independent source review and repair of the 123-question Zerolog/Rich/Pino packet, then freeze its hashes and complete configuration in a new preregistration. Fresh pooled blind relevance judging and natural agent tasks remain separate requirements; authoring calls do not measure agent efficiency.


### v64: fresh source-reviewed workload frozen before retrieval

Three isolated GPT-6-luna authors drafted 41 questions per repository for the new pinned Zerolog, Rich and Pino sources. A first full-batch author attempt failed; it is retained in provenance. Three different fresh GPT-6-luna sessions reviewed every question using only the source packet, with follow-up repairs. This AI separation is weaker evidence of independence than independent human authoring. Model token/spend accounting is unavailable from collaboration tools; these authoring calls are not an agent-efficiency benchmark.

The mechanical freeze gate rejected an intermediate submission: 30 Zerolog anchors did not occur on their start line, and 37 Pino positive result labels had been replaced with answer text. Source reviewers corrected both, and the successful freeze validates every field, range, anchor, source hash, category quota and historical exact-question duplicate check. All source-level review blockers are resolved. The Pino review retains the caveat that two tasks examine literal documented/internal default values without showing a user-visible behavior difference. Original submissions, review changes and rejected staging are preserved.

Preregistration v64 freezes 123 questions (111 code-scored; minimum/pilot requirement 110), 581 supported source files, the unchanged v61 candidate/v59 experimental control binaries, pinned native code-only Graphify, shared cl100k 2K/8K budgets, one sequential paired repetition and resource limits. No latency inference. All fresh input and tool hashes are authenticated by `benchmark/confirmation-v64/run-plan.json`; source sets match the exported packet exactly. Query SHA-256: `cbbdef34c9d16d5e9ffbfda1969914ff0fbaa8f5953a2408ca3b096b0d8d9f29`.

The frozen v64 comparison and blind pooled source review are complete. Both Graf variants passed the preregistered retrieval decision (exit 0), while the per-case diagnostic remains not_passed (exit 1). Full results and losses are in research/heldout-results.md and results/confirmation-v64-report.json. Agent savings remain unmeasured and product promotion is blocked.


### After v64: matched natural agent trials

The fresh retrieval rule passed for v61 and v59, but v59 matched every shared file-quality score with fewer mean output tokens. Retain both as experimental; no default/promotion change. Before collecting new trial data, freeze fresh natural tasks, task-specific executable or independent outcome checks, the same cheap solver model, tool/source access, spend and turn limits, paired repetitions, cache policy, and decision criteria. Compare against pinned Graphify plus source reads and the experimental v59 control; include the last accepted Graf if one exists. Count all tool payloads, model calls, retries and failed attempts, including overhead. If complete usage cannot be obtained through the current Codex path, mark savings unmeasured rather than infer them from retrieval text size.

Investigate v64 Rich all-file and Zerolog top-5 losses as exposed diagnostics. Every changed retrieval arm needs a newly recorded preregistration and unseen workload before confirmation; never recycle v64 as fresh after tuning. Independent AI judging required failed-draft repairs and smaller batches; preserve this annotation overhead and uncertainty. Product promotion requires matched natural-agent outcome and efficiency evidence, not the retrieval pass alone.

The next-trial preparation now has a machine-readable contract in `benchmark/agent-trial-preparation.json` and a fail-closed prerequisite check in `benchmark/agent_readiness.py`. The command builder in `benchmark/agent_backend.py` selects the exact v64 evidence commands, `evidence-rescue` for v59 and `evidence-selective` for v61, with the same requested text budget and limit 20. Tests compare both commands directly against the frozen retrieval driver. The historical agent broker still uses `units`; the new builder is not yet integrated with a solver runner. Historical runners, v64 preregistration, results, and defaults are unchanged.

`results/agent-trial-preparation-readiness.json` exits 1 with four prerequisite groups unpassed: complete model-request accounting, an enforceable live token cap, fresh independently reviewed natural tasks with executable outcomes, and a fully frozen source/comparison design. The current requested solver is GPT-6-luna medium. The stopped codex-v3 batch remains stopped. No new solver trials were launched; no agent savings are measured. A declaration-only readiness pass would still not authorize execution or establish promotion.

The local preflight checked both pinned Graf evidence commands and the installed Graphify Python source equality against its exported snapshot. Commands and raw outputs are preserved in `.research/evidence/agent-trial-preparation/`. An initial Git probe was invalid because the exported Graphify archive has no Git checkout and Git inherited the parent Graf repository; that probe is retained and explicitly excluded from baseline provenance. Snapshot provenance remains the pinned archive receipt. These checks are integrity diagnostics, not performance comparisons.

The next accounting experiment should test an isolated local telemetry collector before sending any fresh task to a solver. Codex documents API request/attempt and stream events through opt-in OpenTelemetry, which may close the request-count gap; completeness, retries, final flush, token reconciliation, and enforcement still need local verification. Keep this candidate instrumentation distinct from established accounting. Official references: [non-interactive JSON usage](https://learn.chatgpt.com/docs/non-interactive-mode) and [telemetry events](https://learn.chatgpt.com/docs/agent-approvals-security).

### Synthetic accounting probe v1: CLI totals omit observed completion usage

The frozen two-invocation diagnostic completed using requested GPT-6-luna low on Codex 0.156.1. Its loopback-only OTLP HTTP JSON collector retained eight raw batches with no decode or persistence errors. No repository source or fresh benchmark task was supplied; native MCP client metadata still contains workspace identifiers, so raw evidence remains private under `.research/evidence/codex-otel-probe-v1/`. Public results contain counts and hashes only. The collector implementation was delegated to an isolated cheap-model agent; its configuration is not a verified serving-model identity.

| Synthetic case | Model request events | Response completions | CLI input/output | Telemetry input/output | Additional observed input |
| --- | ---: | ---: | ---: | ---: | ---: |
| Plain OK reply | 2 | 2 | 2,999 / 5 | 4,872 / 5 | 1,873 |
| One fixed MCP ping | 4 | 4 | 11,699 / 70 | 14,352 / 70 | 2,653 |

Each case emitted an initial zero-output completion whose input usage accounts exactly for the discrepancy; it is retained rather than subtracted. WebSocket startup prewarm is a plausible explanation based on [official client source](https://github.com/openai/codex/blob/main/codex-rs/core/src/client.rs), not a verified attribution from these event fields. Billed usage and weekly quota impact are unknown. Cached input reconciled (0 plain; 7,296 ping). `/models` requests are counted separately from model-response requests. The ping had one completed native MCP call, one independent broker call/response and one matching telemetry ping result; two additional telemetry `exec` results are preserved as overhead. The plain case also emitted a CLI configuration error about unavailable Code Mode while still exiting 0; it remains visible and requires audit.

`benchmark/codex_otel_accounting.py` uses semantic `attributes.event.name`: the exported OTLP `eventName` is often a Rust source location. It rejects missing/invalid usage, duplicates, unexplained request/completion differences, failed or unknown request status, CLI error items and mismatched tool receipts. `results/codex-otel-probe-v1.json` records both token mismatches and exits 1. All four implementation hashes match the pre-call plan; v64 preregistration and comparison/decision/report hashes are unchanged. This is an instrumentation diagnostic, with no new comparative Graphify run, retrieval change, acceptance pass or promotion.

Next: record a new bounded synthetic plan to distinguish prewarm from task requests (for example an HTTP-only control), verify failed/retried request and exporter-shutdown paths, account for all observed completions and wrapper overhead, and establish an enforceable live cap. Do not resume the stopped codex-v3 batch or relabel v64 as fresh. Complete request accounting, fresh independently reviewed natural tasks and the matched Graphify-plus-source-read trial design remain prerequisites; agent savings remain unmeasured.

### HTTP control and local retry diagnostic: accounting still blocked

The separately frozen `codex-otel-probe-v2` plan allowed two sequential live synthetic CLI invocations, requested GPT-6-luna low, with HTTP transport, provider retries disabled and a 60-second timeout. Only `plain_http` ran. It exited 0 and produced three telemetry batches with no decode or collector errors. There were no WebSocket requests, one HTTP response request and one usage-bearing completion with 2,999 input / 5 output tokens, matching CLI totals. A second `response.completed` event was a marker without token fields, and the HTTP request event omitted `success` despite reporting status 200. The CLI also retained the Code Mode host-disabled error notice even with `features.code_mode=false`.

The frozen runner therefore exited 1: it rejected the marker's absent usage fields and the CLI error item. The second live MCP probe was skipped under the frozen failure policy, not replaced. This config-bundle control supports investigating startup prewarm as the source of v1's additional observed tokens; it does not establish transport-only causality, complete accounting, billing impact or an agent-efficiency win. Preserve the distinction between generic stream markers and usage-bearing completion records in any new parser revision; never silently drop an unexplained usage-bearing record.

The separately frozen `codex-local-retry-v1` diagnostic invoked Codex once against a loopback-only fixed-error server, without successful model inference or request forwarding. Independent receipts show two identical-body response POSTs returning 503 and three ancillary requests (two model-list GETs and a turn-cost analytics POST) returning 404. All five requests lacked Authorization. Codex exited 1 normally, but no telemetry batches reached the collector, so the diagnostic also exited 1 and retry telemetry remains unverified. Analytics and metrics were disabled in this local diagnostic; absence of batches does not identify whether the cause is exporter initialization, configuration or failure-path flushing. Retain the failed run rather than claim that two independent receipts establish complete Codex telemetry.

Implementation and reporting were delegated to isolated agents configured for GPT-6-luna; measurement source hashes were frozen before each run. The plans are retained under `.research/evidence/codex-otel-probe-v2/` and `.research/evidence/codex-local-retry-v1/`. Public counts, artifact hashes and all failed checks are in `results/codex-accounting-followup.json`. The [official configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference) documents retry/transport controls, but neither it nor the installed CLI help established an enforceable live token cap for this harness. Keep that prerequisite unpassed.

Next: freeze new synthetic protocols to distinguish stream markers from usage records using request/response correlation, verify exporter behavior on failed turns with controlled analytics settings, and establish request admission plus a verified token bound before natural trials. Do not modify the historical frozen runners or resume codex-v3. Graphify results and product-promotion status remain unchanged; agent savings remain unmeasured.

### Versioned completion classification and local analytics control

Added `benchmark/codex_otel_classification.py` as a separate diagnostic parser; frozen historical parsers and failed decisions remain unchanged. It distinguishes tokenless completion markers from valid usage records, rejects partial or malformed token fields, retains zero-output records and duplicate records with visible errors, and reports optional token fields separately without adding them to input/output totals. Conversation IDs are not request identifiers. Reclassifying the preserved HTTP run identifies one marker and one usage record; the earlier WebSocket runs retain all two/four usage records, including their additional observed input. None contains per-request/response identifiers, so ordering and matching aggregate totals cannot establish complete accounting. Report: `results/codex-preserved-classification-v1.json` (exit 1).

The new `codex-local-retry-v2` protocol was frozen before two sequential local-only CLI invocations: analytics disabled, then enabled, with identical remaining settings. Each produced two independent response POST receipts returning 503 plus three ancillary requests, no Authorization headers, no successful inference, no timeout, and no telemetry captures. Both diagnostic gates failed (exit 1); enabling analytics did not resolve the gap in this local setting. This does not isolate the exporter failure's cause or establish behavior for real-provider errors. Raw commands, receipts, hashes and failed outcomes remain under `.research/evidence/codex-local-retry-v2/`; public summary: `results/codex-local-retry-v2.json`. The official [advanced configuration documentation](https://learn.chatgpt.com/docs/config-file/config-advanced) describes metrics as independent of OTel log export; these observations do not justify assuming that disabled analytics caused the earlier missing logs.

Two isolated GPT-6-luna agents implemented the new components; parent review and targeted verification covered 37 distinct tests, including 10 new tests. Three collector tests initially hit sandbox socket restrictions, then passed with loopback access. All historical measurement-source hashes and v64 comparison/preregistration hashes remain unchanged. This work added no successful live-provider probe calls, no retrieval comparison, and no promotion. Agent savings remain unmeasured, and a real-provider live token cap remains unverified.

Next: inspect the installed CLI's exporter initialization and failure-path shutdown before further probes; missing logs now reproduce with both analytics settings. A request-boundary ledger with explicit admission and a verified output bound is an alternative to relying on OTel completeness, but remains unimplemented and unverified. Fresh natural tasks and matched Graphify-plus-source-read trials stay blocked until these accounting and resource prerequisites are met.

### Local failure timing control: OTel misses a receipted attempt

The installed standalone Codex 0.156.1 package contains the native executable and resources, without matching Rust source in that package. Its executable hash matches the earlier probes. Binary strings identify exporter timer/shutdown facilities, but cannot prove executed control flow; initialization and exact shutdown behavior remain unverified. The audit is retained in `results/codex-installed-telemetry-audit-v1.json`, alongside the official documented asynchronous batching/shutdown-flush behavior.

An isolated GPT-6-luna agent implemented the new `probe_codex_retry_v3.py`; parent review and five focused tests passed before protocol freeze. The frozen protocol ran two sequential local-only conditions, immediate fixed 503 failures and six seconds of delay before each fixed 503 reply. Remaining CLI settings were identical, including disabled analytics. Requests were independently receipted before each reply; no Authorization headers, forwarding, successful inference, timeouts, decode errors or collector errors occurred. No real-provider probe ran.

The immediate CLI ran for 0.587746 seconds and exported no telemetry. The delayed CLI ran for 12.617933 seconds and exported two batches containing startup/prompt records and a response-request event for attempt 0 with status 503. Independent stub receipts record two response attempts in both cases. The delayed telemetry omitted attempt 1 and omitted the explicit success field on attempt 0. Both diagnostic gates remain failed (exit 1). These observations support a timing/shutdown-loss hypothesis without proving its exact internal cause. Longer runtime allowed some export in this pair; it did not establish complete retry accounting.

Report and reproducible analysis: `results/codex-local-retry-v3.json` and `.research/evidence/codex-local-retry-v3/analyze.py`. All historical measurement implementation hashes, prior reports and v64 preregistration/results remain unchanged. Graphify comparison status remains unchanged; agent savings are unmeasured and promotion remains blocked.

Next: implement an independent ledger at the HTTP request boundary, with durable records before forwarding, retry/failure accounting, admission checks and strict output-bound validation. OTel can remain supplementary evidence; this harness cannot use it alone to certify complete failure-path accounting. First validate that ledger against local synthetic success, failure, retry and interrupted-stream fixtures. Real-provider integration and live token enforcement will need separate verification before fresh natural tasks.

### Durable request admission: local fixtures pass

Implemented `benchmark/request_ledger.py` and the loopback-only `benchmark/request_gateway.py`. The ledger exclusively creates a private journal, synchronizes its file and directory before use, and synchronizes each admission before permitting forwarding. Each admitted attempt reserves the exact fixture input bound plus its output cap; failures retain reservations and retries require new ones. Invalid usage poisons the ledger. Concurrent admissions share the same locked budget, and missing failure usage stays unknown. The gateway supports complete POST bodies at `/v1/responses`; protocol, path, authorization and header rejections occur before ledger admission. This is not full agent accounting.

Two isolated GPT-6-luna agents implemented the modules and tests; a third independently reviewed them. Parent review resolved durable denial receipts, duplicate Content-Length, truncated and repeated SSE terminal events, journal creation durability, and exact journal assertions before freezing the diagnostic. Thirty-one distinct focused tests passed, including journal synchronization failures and concurrency limits.

The frozen `request-ledger-local-v1` plan ran seven sequential local fixture cases once: success, HTTP failure followed by retry, interrupted stream, budget denial, unknown input bound, usage exceeding its input bound, and competing concurrent admissions. All seven diagnostic gates passed (exit 0), with nine client requests, six independently receipted upstream requests, three durable denials and no pending admitted attempts. Every admitted attempt retained a 24-token reservation. The failure, interruption and invalid-usage cases retained unknown usage rather than treating it as zero. The invalid-usage case poisoned its ledger as required. Raw synthetic request/response bodies, journals, independent receipts, command, source hashes and report are preserved under `.research/evidence/request-ledger-local-v1/` and `results/request-ledger-local-v1.json`; verification is in `results/request-ledger-local-v1-verification.json`.

No Codex or live-provider inference ran. Input bounds are trusted synthetic-fixture declarations tied to the exact request bytes and model; this implementation has no real-provider admission path. A real-provider token cap remains unverified, complete agent accounting remains false, and agent savings remain unmeasured. Historical frozen sources and v64 comparison/preregistration artifacts remain unchanged. This instrumentation result does not change Graphify comparison or promotion status.

Next: implement and separately verify provider input counting and output-limit enforcement, including failed and interrupted requests, before enabling a live admission path. Then integrate a bounded solver with complete tool/model accounting, freeze fresh reviewed natural tasks, and run matched Graphify-plus-source-read trials under identical limits. Preserve the failed historical accounting gates.

### Count-before-generation protocol: offline validation

Added `benchmark/counted_request.py` without modifying the frozen ledger or gateway. It serializes a count request, a durable count receipt, generation admission and the generation request. Each count attempt has a separate ceiling, including failures; retries count and reserve again. Only explicit model, string input, optional string instructions and output cap are supported. Unsupported fields are rejected before counting. The counting projection includes every supported input field, and generation receives the original bytes. Counter errors prevent generation. Journal or payload-preservation failures poison the session; later calls cannot even invoke the counter.

The [official token-counting guide](https://developers.openai.com/api/docs/guides/token-counting) documents the input-count endpoint and states that output limits include hidden generated tokens. The [Responses reference](https://developers.openai.com/api/reference/resources/responses/methods/create) describes incomplete responses caused by `max_output_tokens`. The new fixture path preserves valid usage on that terminal outcome while retaining its incomplete status. This validates handling of a simulated provider contract, not empirical provider enforcement or count accuracy.

An isolated GPT-6-luna agent implemented the pipeline and tests; another independently reviewed it. Thirteen focused tests and four parameterized subtests passed. The initial test run exposed a wrong artifact path in a test, corrected before freezing. Parent review also fixed the possibility of a new count call after generation-ledger poisoning, exclusive run-directory creation, failure handling during payload preservation, and strict Unicode projection handling.

The frozen `counted-request-local-v1` diagnostic failed after its first case when the parent-authored runner tried hashing a relative artifact path against an absolute root. Exit 1 and all evidence remain in `results/counted-request-local-v1.json` and `.research/evidence/counted-request-local-v1/`; the remaining eight cases did not run. A separately recorded v2 fixes the runner's path resolution and retains v1's failed status and hashes. No pipeline, fixtures or criteria changed between these versions.

All nine v2 cases passed (exit 0): success, failed counter, invalid count, generation retry, output-cap terminal, count/input mismatch, missing usage, count-attempt ceiling and generation-budget denial. They made ten in-process count calls and seven generation calls. Independent fixture checks inspected the journals before each call; exact count hashes, original generation bytes, full retained reservations, unknown failure usage and incomplete-cap usage all passed. Raw payloads, journals, commands and frozen sources are retained under `.research/evidence/counted-request-local-v2/`. Reports: `results/counted-request-local-v2.json` and `results/counted-request-local-verification.json`. Thirty-three earlier artifact hashes and all frozen sources remain unchanged.

Neither version made network requests, Codex calls or provider inference calls. Counts are still explicitly synthetic fixtures, with no real-provider admission or credential path. Real count accuracy, failure billing, output enforcement, complete solver accounting and agent savings remain unverified. Graphify results and product-promotion gates are unchanged.

Next: connect this protocol to an authenticated provider transport under a new frozen verification plan, validate real counts and output limits including error accounting, and retain a hard generation admission ceiling. The current text-only fixture path does not yet support tool schemas or tool-result histories, so it cannot certify natural agent trials. Those require a separate supported request format and complete tool/model accounting before matched Graphify-plus-source-read evaluation.

### Authenticated provider harness: implemented; live preflight blocked

Added `benchmark/openai_transport.py`, `provider_ledger.py`, `provider_request.py` and `probe_openai_provider.py` as separate experimental modules, preserving the frozen fixture implementations. Two isolated GPT-6-luna agents implemented the transport and provider session; a third reviewed them independently. Parent review and tests corrected exception sanitization, returned-model validation, artifact paths outside the repository, and the standalone ledger's supported-field checks.

The transport uses verified HTTPS to fixed OpenAI endpoints, an explicitly supplied API key, bounded payloads, and one attempt per invocation, without redirects or automatic retries. The provider session records count-response provenance, synchronizes admission before forwarding generation, reserves counted input plus the output cap, and retains reservations and unknown usage on errors. It supports only string input and instructions. Tool schemas, histories and full solver accounting remain unsupported.

Frozen plan `.research/evidence/openai-provider-v1/plan.json` (SHA-256 `6e79e5fc8bb05f794d97fd6a22746f39a52df6079a6aabcb46f345896812df75`) pins seven sources, model `gpt-6-luna`, one count call, at most one generation attempt, a 16-token output cap, and a 256-token admission ceiling. It requires exact returned model, count/input agreement, output within the cap, and no unknown or pending generation usage. Even a successful single-request diagnostic would leave general provider enforcement, complete agent accounting and promotion unverified. The [official counting guide](https://developers.openai.com/api/docs/guides/token-counting) and [model documentation](https://developers.openai.com/api/docs/models/gpt-6-luna) informed this experimental contract; account/model availability is unverified.

After freezing, all 29 focused tests and 28 parameterized subtests passed using injected transports and mocks. The credential-free preflight exited 1 with `missing_OPENAI_API_KEY` and `execution_not_requested`; it made zero network requests. No authenticated provider request ran, and no credentials were read from files or Codex authentication. Commands, stdout, stderr, exit codes and source hashes are preserved under `.research/evidence/openai-provider-v1/`. Reports: `results/openai-provider-v1-preflight.json` and `results/openai-provider-v1-verification.json`. Forty-two historical artifact hashes and all seven frozen source hashes remain unchanged, including the earlier failed v1 fixture diagnostic.

Next: supply `OPENAI_API_KEY` securely in the local process environment and run the separately recorded, opt-in live command from `commands.json`; never paste a key into the conversation. Preserve the blocked preflight and use the distinct live output path. Any protocol change or model substitution requires a new recorded plan before requests. Then extend and independently test the supported tool/history format and failure accounting before fresh natural agent tasks. Existing Graphify results are unchanged; agent savings remain unmeasured and product-promotion gates remain unpassed.

### Claude subscription diagnostic: two successful Haiku cases

The user chose Claude Code after their usage limit reset. Installed Claude Code is 2.1.307. Its normal authentication command confirmed a first-party `claude.ai` subscription login outside the sandbox; the sandbox initially reported no login. No API key was needed and no credential file was read directly. The experimental OpenAI path remains optional and its blocked preflight is preserved.

Added a separate subscription probe and a conservative stream parser, with an isolated GPT-6-luna agent implementing the parser/tests. The probe disables built-in tools, uses an explicit empty or synthetic-ping MCP configuration, disables settings sources and hooks, and starts fresh sessions. Inherited API/provider overrides are removed from the child environment. It preserves complete stdout, stderr, commands, ping request/response payloads, timing, usage observations, and source hashes in private evidence files. The parser separates partial-event observations from aggregate results, includes cache read/write inputs, rejects ambiguous/repeated aggregates, and compares result totals with per-model totals without adding them together.

Preparation v1 was not executed: a Python 3.12 test fixture tried serializing a 10001-digit integer before reaching the parser. The parent generated a plan despite that failed test; `results/claude-subscription-v1.json` records this failed preparation and zero inference invocations. V2 retains its sources and plan and changes only the fixture to a 1001-digit integer, which still tests float overflow. All 13 focused tests and two parameterized subtests passed before and after freezing v2. No live criteria, budgets, prompts or models were relaxed.

Frozen v2 plan `.research/evidence/claude-subscription-v2/plan.json` has SHA-256 `e2d0c91e151c6517c268c1d9a14865224ef96a2c0c80e6d8fee30980b5d6dbf0`. Its sequential cases were a plain `OK` reply and exactly one broker ping followed by `OK`; at most two CLI invocations, one/three turns respectively, 60 seconds each, and a $0.10 client-estimated budget each. Stop-on-first-failure and no replacement policy applied. The requested alias was `haiku`; both streams reported `claude-haiku-5-5`. The [official CLI reference](https://code.claude.com/docs/en/cli-reference) explicitly says estimated spend can exceed its budget flag, so this is not hard token/spend enforcement.

Both cases passed, exit 0, with the exact expected tool inventory, replies and ping count. Plain: 850 reported input tokens including cache creation, four output tokens, $0.0001718 client estimate. Ping: 2736 reported input tokens including cache read/creation, 36 output tokens, $0.00031419 client estimate. Total: 3626 reported tokens and $0.00048599 estimated API-equivalent cost; actual subscription charge is unverified. The aggregate result and per-model totals agree in both cases. These were separate fresh sessions with warm shared OS caches and uncontrolled provider caches, whose usage is included.

Reports: `results/claude-subscription-v2.json` and `results/claude-subscription-v2-verification.json`. All 58 prior artifact hashes, six frozen sources and 12 raw artifact hashes were verified unchanged. Raw traces remain under `.research/evidence/claude-subscription-v2/` rather than copied into public prose.

This establishes successful subscription execution and consistent exposed usage on two synthetic success cases. Internal retries, failed provider requests, hard token admission, and complete agent accounting remain unverified. It is not a Graf-versus-Graphify comparison; the frozen Graphify results and promotion gates are unchanged. Next: independently exercise Claude failure/retry/interruption reporting, preserve unknown usage rather than infer zero, then freeze the exact observed model and a supported broker contract before fresh matched natural tasks. Do not revive historical batches or infer agent savings from these diagnostic totals.


### Claude failure diagnostics: retry, turn limit and interruption

Added a conservative failure classifier and a separate synthetic MCP failure probe. An isolated GPT-6-luna agent implemented the classifier and six focused tests; a second cheap agent reviewed the failure protocol. The parent implemented the runner and its tests. Captures distinguish terminal task errors from accounting consistency, retain valid prefix observations on malformed streams, and never reconstruct missing final usage from partial assistant events. Operational authentication/spawn failures save an unsuccessful report. A deliberate interruption kills the entire isolated process group after a synchronized tool-begin marker.

Frozen v1 plan `.research/evidence/claude-failures-v1/plan.json` (SHA-256 `6d39e362ff10edbfeb15b761929c504782bdfd444cb2fb6ae7aa57cf3ee60592`) ran one case and failed, exit 1. The model successfully retried the synthetic tool error and exposed consistent terminal totals, but the parent's overly strict request check rejected standard MCP `_meta` fields. The remaining two cases did not execute. All evidence and the failed gate remain in `results/claude-failures-v1.json` and its verification report. Twelve focused local tests passed but missed this protocol detail.

A separately frozen v2 plan (SHA-256 `32576306cd2768af6b173190a28c822cc296b051548f44c5689d626d72c24404`) permits only the expected name, empty tool arguments and an optional metadata dictionary. It also accepts repeated assistant observations only when every observed model matches the pin. Two new regression tests cover these details; all 14 focused tests passed before and after freezing. V1 is not reclassified or overwritten. Model, prompts, per-case turn/time/estimate limits and task/usage consistency checks otherwise remain unchanged.

V2 ran three sequential cases using the pinned observed model `claude-haiku-5-5`, with fresh sessions/cwds, warm shared OS caches and uncontrolled provider cache usage included. Each had a 45-second timeout and a $0.10 soft client-estimated budget; turn limits were four, one and two. All three capture diagnostics passed (runner exit 0):

- Synthetic tool retry: two ping requests, first an explicit tool error, second `pong`, final `OK`, CLI exit 0. Reported inputs including cache: 4300; outputs: 68; client API-equivalent estimate $0.00019002.
- Turn limit: one successful ping followed by `error_max_turns`, CLI exit 1. Reported inputs including cache: 1319; outputs: 32; estimate $0.0002796. Error terminal aggregate and per-model totals agree.
- Deliberate interruption: one synchronized ping begin, no tool reply or final result, process-group kill and CLI exit -9. Final usage and cost remain unknown; partial observations are not counted as complete totals.

Reports are `results/claude-failures-v2.json` and `results/claude-failures-v2-verification.json`. All 72 historical artifact hashes, seven frozen sources and 20 raw artifact hashes verify. There were four CLI invocations including failed v1. The two terminal v2 cases expose 5719 tokens; this is not a total for all attempts, because interruption usage is unknown. Raw traces, complete tool payloads, commands and timing remain private under `.research/evidence/claude-failures-v{1,2}/`.

A synthetic tool retry does not exercise internal HTTP/provider retries. Successful capture of an intentional interruption does not pass complete-accounting acceptance. Subscription billing, internal failed attempts and hard token/spend admission remain unverified; agent savings remain unmeasured and product promotion remains blocked. No paired retrieval comparison ran, so frozen Graphify results and acceptance decisions remain unchanged.

Next: freeze and validate the supported source-read/retrieval broker contract for the pinned Claude solver. Retain unknown usage as a failed accounting guardrail rather than fabricate savings. A complete request-boundary accounting path and enforceable limits remain required before a fresh matched natural-task promotion run against pinned Graphify plus source reads and the experimental Graf control. Do not reuse exposed task batches or silently raise historical resource limits.


### Read-only retrieval/source-read broker: frozen local contract diagnostic

The audit found a concrete gap in the historical Claude MCP adapter: `Session.call` logs normalized operation results, while the adapter does not separately preserve exact MCP replies or requests refused before dispatch. Its internal payload token estimate therefore cannot stand in for delivered MCP payload accounting. Historical runners and their frozen source hashes remain unchanged.

Added a separate experimental `benchmark/readonly_broker.py`, its stdio entry point, and a local fixture driver. A fresh GPT-6-luna agent implemented the broker/tests, a separate cheap agent reviewed it, and the parent implemented and reviewed the driver/stdio wrapper. Both tools (`retrieve` and `read`) are identical across the three command configurations. The broker reuses the frozen evidence command builder, hashes the selected binary, validates the eligible source inventory and every manifest file before retrieval, and hashes bytes before source reads. It rejects hidden/traversal/symlink and off-manifest reads; accepts root-contained absolute citations; returns source hashes and whole-line clipped content. Graphify native output overruns remain fully preserved. Backend failures prevent later retrieval. This is a new read-only contract, not a replacement for the historical repair-task protocol.

The exclusive private JSONL journal synchronizes requests before dispatch and canonical replies before returning them. All parseable tool calls, including unknown tools, malformed arguments and action-limit refusals, consume actions. Journal write failures latch a poisoned state before further dispatch. The stdio layer records raw input bytes (hex plus readable text) before parsing, rejects duplicate/nonfinite JSON, and preserves exact emitted reply lines. Local cl100k byte/token diagnostics count complete serialized payloads separately from bounded source content; they are not provider usage. Initialization and discovery exchanges are also retained. Partial backend output from subprocess exceptions is retained when exposed.

Before freezing, review caught and fixed whole-line clipping for a single long line, source-manifest checks bypassed by retrieval, missing extra-file checks, journal failure recovery, a unittest helper overriding `TestCase.run`, and mismatched assertions after the reply representation changed. These were development tests before the diagnostic plan, with no solver or real backend calls. All 33 focused tests then passed using the real benchmark tokenizer, including three local stdio subprocess checks.

Frozen `.research/evidence/readonly-broker-local-v1/plan.json` has SHA-256 `4fec2b14d45dd1c798ea289ad8e3fa4c5f6c6b949c0784722b7f503c2454e112`. It pins nine source files, Python, tiktoken 0.14.0/cl100k vocabulary fingerprint, existing binary hashes and exact Graf v59/v61/Graphify query argv. It ran six cases sequentially for each command configuration: success, refusals/action cap, changed source, backend failure, native output overrun, and stdio input/output preservation. All 18 passed (diagnostic exit 0); all 33 tests passed after freezing. Backend responses are injected fixtures; the only subprocesses are three read-only stdio servers. There are zero real retrieval-backend invocations and zero solver calls. This is not a retrieval comparison, quality measurement, speed measurement, or agent-efficiency experiment.

Reports: `results/readonly-broker-local-v1.json` and `results/readonly-broker-local-v1-verification.json`. All 79 historical artifact hashes, nine frozen source hashes and 99 raw evidence hashes verified. Commands, complete exchanges, source fixtures, backend fixture invocations, stdio streams and journals remain under `.research/evidence/readonly-broker-local-v1/`. Existing failed Claude diagnostics remain visible; prior Graphify results and acceptance decisions are unchanged. Agent savings remain unmeasured and product promotion remains blocked.

Next: validate source/index artifact parity and invoke the real frozen backends through this contract on the same source snapshot before connecting the pinned Claude solver. Any retrieval evaluation must use the pinned Graphify comparison driver and preserve acceptance/decision failures. This local contract does not attest graph/index artifacts, concurrent external filesystem mutation, repair-task edit/test/finish behavior, internal provider attempts, or hard live token admission. Fresh matched natural-task promotion still requires complete accounting, enforceable shared limits, independent fresh task review and outcome checks.


### Read-only broker real backend parity — 2026-10-24

Preregistration revision 65 adds `benchmark/readonly-broker-real-v1-comparison-plan.json` before execution. Revision 64 is preserved byte-for-byte in `.research/evidence/readonly-broker-real-v1/prior-preregistration.json`. Primary metric, quality margins, budgets and promotion requirements are unchanged. This is an exposed 30-query development regression and integration diagnostic on Cobra, Flask and Fastify, not a fresh held-out confirmation. No accepted Graf version exists; v59 remains an experimental comparator. Pinned Graphify revision and code-only native adapter are unchanged.

The required comparison driver completed 180 sequential native queries across three arms and two budgets, after six fresh index builds. Raw outputs, commands, file hashes and every timing sample are in `results/readonly-broker-real-v1-comparison.json`. OS caches were uncontrolled; one repetition supports no latency inference. At requested budget 2K, mean retrieval output tokens were 1,631.43 for Graf v61, 1,557.33 for v59 and 2,413.37 for Graphify. Shared code-file hint recall was 0.98148, 0.98148 and 0.87037 respectively on 27 code-scored queries. These metrics describe delivered retrieval text; Graphify hints do not establish source-anchor recall or documentation-task performance. Graphify's native budget is approximate and overruns remain untrimmed.

Both `acceptance.py` and `decision.py` returned exit 1. Acceptance reports 11 regressing query/budget cases per Graf arm out of 60 cells. Development exposure and insufficient sample size prevent a decision pass. The required latest aliases and versioned reports are retained; old aliases were archived before overwriting. No superiority or agent-efficiency acceptance follows from these averages.

The frozen real broker v1 diagnostic (plan SHA-256 `11f7601355ba9747d13f4650d603422f5fa843c9081ca40aac9bbf23691e1da6`) failed: 31 of 60 Graphify stdout checks differed, while all Graf output and all source-read checks matched. The comparison subprocess fixes `PYTHONHASHSEED=0`; the broker inherited an unset value. Every failed exchange remains intact. A separate v2 plan (SHA-256 `26f3b28125e4446c34af99b1cb3284c32cd4f563b0729ccf8f1d9679d77d3c0e`) fixes that environment before rerunning the same delivery checks. All 180 v2 cases passed exact stdout, stderr, return status, status classification, token count, source read and source hash checks. Source files and six index/graph artifacts matched before and after. This supports parity under the matched environment; it does not enforce that environment in the standalone broker launcher.

All 39 focused broker/command tests passed. Across both broker attempts, 360 real native queries and 360 source reads were retained; there were zero solver calls. `results/readonly-broker-real-verification.json` verifies 92 previous artifacts, all frozen source hashes, 740 evidence files and seven reports. `results/readonly-broker-real-summary.json` preserves side-by-side metrics, nonzero exits and remaining prerequisites. Agent savings remain unmeasured and product promotion remains blocked.

Next: enforce artifact hashes and the matched subprocess environment inside a separately versioned broker, test stale-index refusal, and handle native Graphify no-match exit 4 without latching an infrastructure failure. No-match was absent from this workload, so the successful real parity run does not validate that path. Harness-side artifact attestation does not address concurrent filesystem mutation. Complete provider attempt accounting and enforceable shared limits remain required before fresh matched natural agent trials.


### Broker artifact enforcement and native no-match recovery — 2026-10-24

A separate cheap gpt-6-luna implementation agent added `benchmark/readonly_broker_v2.py`, `benchmark/readonly_broker_stdio_v2.py` and `benchmark/test_readonly_broker_v2.py`; all earlier versions remain frozen. The new Broker deep-copies its configuration and requires an exact per-arm index/graph path, artifact SHA-256 and canonical source-manifest digest. It validates source inventory and artifact hashes before journal creation, rechecks artifacts/binary/source around retrieval, rejects missing/stale/symlink artifacts, and retains native stdout/stderr/status when a post-run integrity failure disables later retrieval. Rechecks do not provide atomic filesystem snapshots or prove index semantics independently of a trusted frozen build receipt.

Every retrieval child receives `PYTHONHASHSEED=0` without modifying the parent environment. The launcher requires pins for its own source, both broker modules, command builder, tokenizer module and strict JSON parser. Only Graphify's exact exit 4, exact `No matching nodes found.\n` stdout and empty stderr is a completed no-match outcome; other nonzero exits remain failures. A subsequent query remains possible after that exact no-match response.

Preregistration revision 66 freezes an unchanged exposed development comparison in `benchmark/readonly-broker-hardened-comparison-plan.json`, plus the integration workload, all-case criteria and resource limits before new backend data. Revision 65 and the previous acceptance/decision aliases were archived first. The broker diagnostic plan `.research/evidence/readonly-broker-hardened-v1/plan.json` (SHA-256 `4bcb8ef35d258b7a54b9c115e731cb75527661801ebc08bd3add257365a46701`) authenticates 15 sources, tokenizer and the new comparison receipt before broker invocations. No ranking, budget, margin, baseline revision or promotion criterion changed.

All 51 focused tests pass. The actual hardened broker passes all 180 query/budget/arm native-payload and source-read cells. Three real Graphify no-match/recovery pairs pass, one for each repository. Three actual stdio retrieval/read sessions pass with the parent seed deliberately set to 23. The final verifier independently checks all stdio native statuses/source bytes and 750 durable wire records across 186 journals, including UTF-8 byte counts and cl100k counts. There were 180 direct comparison queries, 180 broker parity queries, six no-match/recovery queries, three stdio queries, and 183 broker source reads. No solver calls were made.

The native comparison remains an exposed 30-query development diagnostic at requested 2K and 8K budgets. At 2K, mean output tokens are Graf v61 1,631.43, Graf v59 1,557.33, code-only Graphify 2,414.37; shared code-file hint recall on 27 code-scored queries is 0.98148, 0.98148, 0.87037. Both Graf outputs match the previous comparison byte-for-byte. All Graphify payloads match the previous comparison after replacing only the printed scratch path. Official metrics retain raw paths: the different header path changes Graphify's token counts, and v59's per-case regression count changes from 11 to 10. This is not an algorithmic improvement. Graphify hints do not establish source-anchor recall or documentation-task performance.

`acceptance.py` and `decision.py` both return exit 1 and are preserved in latest and versioned reports. Acceptance reports 11 regressing query/budget cells for v61 and 10 for v59 out of 60 cells each. Development exposure and sample limits prevent decision acceptance. OS caches are uncontrolled; one native repetition, overlapping local tests and native timing boundaries that exclude broker verification overhead provide no broker latency inference. No accepted Graf version exists; v59 is experimental. Agent savings remain unmeasured and product promotion remains blocked.

Reports: `results/readonly-broker-hardened-v1-summary.json` and `results/readonly-broker-hardened-v1-verification.json`. Verification passes for 843 prior artifacts, 15 frozen sources, 396 new evidence files and all six extra cases. Full native stdout/stderr/status, commands, source/build hashes, timing samples and raw MCP exchanges are retained. Next prerequisites: stronger snapshot isolation, separately preregistered matched measurement of verification overhead, then complete provider-attempt accounting and enforceable shared limits before fresh matched natural agent trials.


### Private source snapshots and root-correct Graf indexes — 2026-10-24

An isolated cheap gpt-6-luna agent implemented the private-copy layer and focused tests; the parent integrated pinned stdio launchers and real backend diagnostics. Sources and the pinned index/graph are copied into exclusive owner-only temporary directories with distinct inodes, verified before and after staging, made read-only, and removed after use. Persistent receipts bind the original artifact, staged artifact, manifest and runtime operations. These copies isolate later live-workspace changes; chmod does not provide an OS security boundary against a hostile same-user process, and verified bytes are not an atomic filesystem capture.

Preregistration revision 67 froze the unchanged native Graphify development comparison and copy-only integrity criteria before new execution. Revision 66 was archived in `.research/evidence/readonly-broker-snapshot-v1/prior-preregistration.json`. The comparison driver completed 180 sequential native queries and six fresh builds; audit passed. Acceptance and decision both exited 1 and remain preserved in latest and versioned reports. The 30 queries are exposed development data, with 27 code-scored queries; no accepted Graf version exists, and v59 remains an experimental comparator.

The first frozen snapshot diagnostic failed, exit 1: all 120 Graf retrieval cells and two Graf stdio retrievals rejected the copied index because its absolute repository root differed. All 60 Graphify retrieval cells, 180 source reads, nine live source/artifact mutation contexts and the Graphify stdio session passed. The copied-index error is retained verbatim. All 62 focused tests passed but did not exercise native Graf root binding. The failed plan, raw exchanges and `results/readonly-broker-snapshot-v1-summary.json` remain unchanged; verification authenticates preservation, not a successful integration.

Revision 68 records that failure and freezes a separate v2 diagnostic before new backend execution. The new snapshot implementation rebuilds Graf's index inside the private source copy using pinned v61, preserves the original artifact digest separately, checks the exported root against the staged root, and compares the canonical full export with a frozen per-repository digest after removing only the root field. Every other exported field must match. The new artifact hash is enforced by the existing pinned broker. Source hashes are checked after each operation and the directory is sealed again before retrieval. Graf's normal root validation remains active. Graphify retains its unchanged graph copy. Receipts distinguish `snapshot_ready` from successful session completion, use mode 0600 and synchronize their parent directory; tests cover cleanup after broker startup failure and preserve operation output when post-run integrity checks fail.

V2 uses the exact unchanged native comparison from revision 67 as an integrity reference; it does not run another ranking or efficiency evaluation. Frozen `.research/evidence/readonly-broker-snapshot-v2/plan.json` has SHA-256 `deb1ea0ba7155a60105cea5afa64f9d9c0bdae9030ae1beada26141385f46f27`. All 75 focused tests pass. All 180 query/budget/arm native-payload and source-read cells, nine live mutation contexts, and three actual stdio sessions pass. Graf output matches byte-for-byte. Graphify output matches after substituting solely the exact printed graph path; raw payloads and their actual token counts are retained. Eight Graf rebuild/export pairs are captured, along with three pre-plan reference exports. Independent verification checks all 180 exchanges and three stdio sessions, 12 snapshot receipts, the exported index contents, 738 wire records across 183 journals, 25 frozen sources, 3,408 prior artifacts and 2,144 new evidence files.

The shared native reference at requested budget 2K reports mean output tokens of Graf v61 1,631.43, Graf v59 1,557.33 and code-only Graphify 2,414.37. Shared code-file hint recall is 0.98148, 0.98148 and 0.87037. Both Graf outputs match the previous hardened comparison byte-for-byte; Graphify differs only in the printed scratch path. These are unchanged retrieval diagnostics, not an algorithmic improvement. Graphify hints do not establish source-anchor recall or documentation-task performance. Acceptance still reports 11 regressing query/budget cells for v61 and 10 for v59 out of 60 each; development exposure and sample limits prevent decision acceptance.

Reports: `results/readonly-broker-snapshot-v2-summary.json` and `results/readonly-broker-snapshot-v2-verification.json`. Across both snapshot diagnostics and their shared direct comparison, 546 native retrieval invocations and 366 broker source reads are retained; there are zero benchmark solver calls. OS caches are uncontrolled, one native repetition provides no latency inference, and staging/rebuild/verification overhead is excluded from native timing. Setup performance remains unmeasured. Agent savings remain unmeasured and product promotion remains blocked. Next: separately freeze matched staging/verification overhead measurement, then complete provider-attempt accounting and enforceable shared limits before fresh independently reviewed natural agent tasks against pinned Graphify plus source reads.

### Private-snapshot overhead diagnostic — 2026-10-24

Preregistration v69 froze six exposed development queries (the lexicographically first two IDs per repository), both 2K/8K budgets, three repetitions and all three unchanged arms before this measurement. `benchmark/compare_graphify.py` ran the pinned native-cli-v2 Graphify baseline sequentially, retaining all 108 native calls, source/build hashes, exact commands and timing samples. The additional in-process diagnostic ran 108 fresh private-snapshot sessions with one retrieval and the same first-file two-line source read each. All six competitor orders were counterbalanced; tests did not overlap either timed run. OS caches were uncontrolled. Initial live index builds, Python import/CLI startup and cross-request snapshot reuse are outside the snapshot timing scope.

`benchmark/probe_snapshot_overhead.py` records six additive phases plus nested native child time and the surrounding elapsed time. The phase subtotal excludes gaps such as receipt persistence and payload verification; it must not be labeled full agent latency. Both Graf arms rebuild/export the pinned v61 index in each private root, because the current index is root-bound. Graphify copies its pinned graph. All receipts retain original and staged artifact hashes, rebuild/export stdout/stderr/status and their own timing samples. Raw tool requests/replies, durable journals and diagnostic token counts are retained; provider usage and agent savings remain unmeasured. No benchmark solver calls were made. Implementation used an isolated gpt-6-luna agent; its implementation token usage is not exposed.

| Descriptive median (ms), 36 sessions per arm | Graf v61 | Graf v59 experimental | Graphify pinned, code-only |
| --- | ---: | ---: | ---: |
| Snapshot setup | 1045.61 | 1019.51 | 161.35 |
| Nested native child | 255.68 | 231.38 | 454.20 |
| Six-phase subtotal | 1630.72 | 1584.16 | 873.03 |
| Surrounding elapsed time | 1688.17 | 1625.52 | 876.38 |
| Subtotal minus native child | 1410.89 | 1335.42 | 406.30 |

These are component diagnostics on exposed data, not inferential latency or product superiority evidence. Every raw sample is retained. Medians of components do not add to the median subtotal. The native comparison launches Graphify with `python -m graphify`, whereas the snapshot broker uses its pinned executable; neither native direct comparison timings nor their difference are substituted for broker overhead. The measured in-broker native child is subtracted from that same session's phase subtotal.

All 108 retrieval/source-read pairs passed their native-output integrity checks (Graphify's printed graph path is the sole declared substitution), and all private roots were removed. The focused and existing broker/snapshot test suite passed 80 tests. Retrieval-only acceptance returned exit 1: each Graf arm has three regressing query/budget cells among the subset's 12 cells, despite shared code-file hint recall of 1.0 on this subset. Graphify hints are not source anchors or documentation outcomes. `decision.py` returned exit 1 because the subset differs from the full registered development query file; its raw rejection is preserved and the latest decision explicitly records invalid evidence, rather than retaining a stale earlier decision. Both required reports remain blocked for product promotion.

Reports: `results/readonly-broker-overhead-v1-summary.json`, `results/readonly-broker-overhead-v1-verification.json`; frozen plans, commands, raw exchanges, gate rejection and historical evidence hashes are in `.research/evidence/readonly-broker-overhead-v1/`. The fresh-snapshot setup cost is now a concrete optimization target: evaluate reuse of one validated private snapshot across a bounded session before changing index root validation. Freeze the session workload and amortization boundaries before measurement, retain mutation/rebuild checks, and compare against the same pinned Graphify-plus-source-read setup. Complete provider-attempt accounting and enforceable shared limits still precede fresh independent natural agent trials. No accepted Graf version exists; no performance improvement is promoted here.

### Bounded private-snapshot session diagnostic — 2026-10-24

Preregistration v70 freezes four retrieve/read pairs per repository/repetition, both requested budgets, three repetitions, counterbalanced fresh/reused modes and all six competitor orders before execution. The six exposed development queries and pinned Graphify baseline are unchanged. The required `benchmark/compare_graphify.py` comparison completed 108 sequential native retrievals; audit passed, acceptance and decision each returned exit 1. Both Graf arms have three regressing query/budget cells out of 12. The formal decision rejects the subset because it differs from the registered full development query file. Latest aliases contain this explicit rejection, with previous reports archived. No accepted Graf baseline exists; v59 remains experimental.

An isolated cheap gpt-6-luna agent implemented `benchmark/readonly_session.py` and lifecycle tests. The API makes reuse explicit, deep-copies the plan, requires a strict integer action limit from 1 through 32, creates one verified private snapshot and broker per session, retains existing per-request integrity checks, and closes both on exit or startup failure. The existing stdio v4 launcher already retained one snapshot for its whole input stream: this formalizes and measures an existing lifecycle, rather than introducing a new cache algorithm. Tests exercise action exhaustion, staged source/artifact mutation between calls, cleanup, reentry refusal and setup failures. All 93 focused and existing tests pass; tests did not overlap either timed benchmark.

`benchmark/probe_readonly_session.py` ran 54 groups: three arms, three repositories, three repetitions and two lifecycle modes. Fresh groups create four snapshots; reused groups create one snapshot with eight actions. Each pair retrieves then reads the same first-file two-line source excerpt. All 216 pairs passed exact native payload/status and source-byte/hash checks, allowing only the declared Graphify printed graph-path substitution. Three actual stdio sessions add 12 passing pairs. All 138 snapshot receipts and 184 Graf rebuild/export operations are retained, and all private roots were removed. Across the direct comparison and lifecycle diagnostic there are 336 native retrievals and 228 source reads, with zero benchmark solver calls.

| Descriptive median (ms) | Graf v61 | Graf v59 experimental | Graphify pinned, code-only |
| --- | ---: | ---: | ---: |
| Fresh four-pair phase subtotal | 8506.94 | 4915.37 | 3287.82 |
| Reused four-pair phase subtotal | 2266.23 | 2140.91 | 2215.65 |
| Fresh surrounding four-pair elapsed time | 8826.32 | 5102.93 | 3319.84 |
| Reused surrounding four-pair elapsed time | 2319.28 | 2190.16 | 2237.35 |
| Fresh amortized pair phases | 2066.31 | 1237.51 | 833.63 |
| Reused amortized pair phases | 579.10 | 557.22 | 573.86 |
| Reused setup/cleanup allocation per pair | 184.67 | 194.26 | 53.87 |
| Reused nested native child | 255.66 | 218.39 | 383.11 |

These are descriptive component measurements with uncontrolled OS caches, sequential competitors and rotated order. No significance or promotion conclusion follows. The phase subtotal excludes persistence/verification gaps; surrounding elapsed time retains them. Per-pair setup/cleanup allocation divides each group's measured setup by four, and is not observed isolated-request latency. Component medians do not add. Initial live index builds, imports/authentication and provider solving are excluded. Graphify's direct module launch differs from its broker executable launch; timings are not subtracted across those boundaries. Source/artifact checks remain active, and each Graf private root still requires a root-correct index rebuild/export.

Every paired group is preserved, including losses: reused phase subtotals exceeded fresh for Fastify repetition 2 by 1579.47 ms (v61) and 645.37 ms (v59), and Cobra repetition 3 by 25.37 ms (Graphify). Graf v61 remains slightly above Graphify's reused median on this diagnostic; v59's descriptive median is lower but does not pass acceptance or establish superiority. Raw tool payload tokens are diagnostic serialization counts, including path-dependent differences, not provider usage. Graphify code-only hints do not establish source-anchor or documentation-task quality. Agent savings remain unmeasured and product promotion remains blocked.

Reports: `results/readonly-broker-session-v1-summary.json` and `results/readonly-broker-session-v1-verification.json`. Frozen plans, all commands, raw journals, exported semantics, gate exits and historical hashes are preserved under `.research/evidence/readonly-broker-session-v1/`. The independent verifier checks 216 direct pairs, 12 stdio pairs, all receipts/operations, 936 journal records and 912 logical request/reply payloads, with raw stdio input retained separately. Next: separately freeze a breakdown of remaining per-request verification costs before altering those checks; complete provider-attempt accounting and shared resource limits before fresh independent natural agent tasks against pinned Graphify plus source reads. Keep this lifecycle API experimental until the required held-out gates pass.

### Per-request verification cost diagnostic — 2026-10-24

Preregistration v71 freezes the six exposed development queries, two budgets, three repetitions, three arms and paired plain/profiled modes before data collection. An isolated cheap gpt-6-luna agent implemented `benchmark/readonly_profile.py`; the parent reviewed it and integrated `benchmark/probe_readonly_profile.py`. The profiler temporarily wraps existing functions during each request and restores them on exit or exception. It records counts and nested inclusive/exclusive wall times without changing the frozen broker, snapshot or retrieval implementations. A process-wide lock refuses nested/concurrent profiler contexts; this remains a sequential diagnostic, with no concurrent-use security guarantee.

The first required native comparison completed all 108 retrievals, but an early implementation test's end time relative to comparison startup could not be certified. Its raw results, commands and both failing gate reports are retained; all its timing is excluded from interpretation. V72 preserves v71 and registers a separate clean comparison plan before observing repeated timing data. The same workload was rerun after all tests finished, with no overlapping tests. Both comparisons pass their integrity audit; acceptance and decision each exit 1 for both attempts. Each Graf arm has three regressing query/budget cells out of 12; the formal decision rejects the incomplete six-query subset. No thresholds or Graphify baseline revisions changed. One parent test attempt failed because a profiler fixture used closure-local helpers instead of patched module functions; its log is retained. After correction, all 101 tests and nine subtests pass before clean timing.

The component run executes 54 private eight-action sessions: three arms, three repositories, three repetitions, plain/profiled modes in alternating order, with all six competitor orders cycled. Each session contains the same four retrieve/read pairs. All 216 pairs match native payload/status and source bytes/hash; Graphify's printed graph path is the only declared substitution. Independent verification checks every pair, all 216 profiled handles, all 54 receipts, all 72 Graf rebuild/export operations and 864 durable journal records. Each profiled retrieval contains three complete inventory verifications, two artifact hashes, two binary hashes and one child call; each source read validates its one requested file. Exported Graf semantics and root binding remain checked, and all private roots are removed. Both native comparisons plus the component diagnostic retain 432 native retrievals and 216 broker source reads, with zero benchmark solver calls.

| Descriptive median per profiled retrieval (ms) | Graf v61 | Graf v59 experimental | Graphify pinned, code-only |
| --- | ---: | ---: | ---: |
| Inventory verification, inclusive of source validation | 278.28 | 281.99 | 400.35 |
| Source validation within those inventory passes | 183.72 | 193.24 | 299.65 |
| Inventory traversal/dispatch exclusive residue | 81.76 | 95.82 | 94.75 |
| Two binary hashes, including file reads | 21.62 | 20.49 | 0.17 |
| Two artifact hashes, including file reads | 1.96 | 1.79 | 2.68 |
| Tokenization | 3.53 | 3.55 | 6.16 |
| Durable journal append, exclusive of JSON serialization | 0.34 | 0.35 | 0.38 |
| Nested native child | 523.25 | 400.43 | 829.30 |

Inclusive inventory time already contains source validation. Do not add those two rows. Exclusive categories are disjoint within each request; medians of components do not add to a median total. Source validation includes path checks, file reads and SHA-256; inventory exclusive time includes traversal, dispatch and nested wrapper overhead. The source-read profile root median is 1.28 ms (v61), 1.27 ms (v59), and 1.34 ms (Graphify). Per-request validation, rather than journal durability or tokenization, is the largest measured non-native component on these samples.

Plain median amortized pair phases were 1607.83 ms (v61), 1197.15 ms (v59), and 1264.43 ms (Graphify); profiled values were 1261.12, 1290.27, and 1465.63 ms. These are exposed component diagnostics, not inferential latency or superiority evidence. Graf v61's plain median remains above Graphify's here; v59 remains an unaccepted experimental comparator. Timing varies substantially: the median paired profiled-minus-plain four-pair subtotal is -525.38 ms for v61, +103.49 ms for v59, and +83.66 ms for Graphify. Profiled groups are faster in six, four and three of nine pairs respectively. All 27 paired samples are preserved; negative differences cannot represent negative causal instrumentation cost. OS caches and external system load are uncontrolled. Do not compare these magnitudes causally with the earlier lifecycle run or subtract direct native timings from broker measurements.

Frozen plan SHA-256: `7b94d4cdfd08db23f2855e94642151baaad1e9b563a04e9d2e2d6dd26325cdfb`. It authenticates 146 sources and the clean native reference. Reports: `results/readonly-broker-profile-v1-summary.json` and `results/readonly-broker-profile-v1-verification.json`. Evidence, rejected gates, excluded first attempt, test logs and every request-level timing aggregate are under `.research/evidence/readonly-broker-profile-v1/`. Initial live builds, imports/authentication and provider solving remain outside the timing boundary. Graphify is code-only: hints do not establish source-anchor or documentation outcomes. Provider usage and agent savings remain unmeasured; promotion is blocked.

Next candidate: remove the redundant inventory pass before command construction in a separately versioned experimental broker, retaining full pre-child and post-child checks and testing command-construction mutations and pre-run refusal. Measure it against both pinned Graphify and the unchanged broker under a newly frozen paired plan before calling it an improvement. Root-correct snapshot rebuild costs remain a separate target. Complete provider-attempt accounting, shared limits and fresh independent natural agent tasks are still required for product superiority and agent-efficiency claims.


### Experimental two-scan broker diagnostic — 2026-10-24

Implemented a separate `benchmark/readonly_broker_v3.py` using an isolated cheap-model agent. The unchanged v2 broker remains the default. V3 removes only the full inventory scan before command construction; the guarded runner still verifies full source inventory and pinned binary/artifact immediately before and after the child. Read validation, journal persistence, limits, failure latching, partial failure payloads and Graphify's exact no-match classification remain. This is an integrity check contract, not an atomic snapshot or hostile-process security boundary.

Preregistration v73 froze the six-query, two-budget, three-repetition development diagnostic before data. Preserved all 7,274 prior evidence hashes, pinned Graphify commit 86c57be8204673c2a1b9849a3292a7dd3ab373d8 and unchanged Graf v61/v59 binaries. All 78 focused tests and 14 subtests passed before timed work, including mutation during command construction, post-child mutation with partial outputs and factory restoration. The implementation agent's four focused unittest tests also passed; its attempted pytest command was unavailable in its environment. No tests or source hash audits overlapped timed native workloads.

Ran `benchmark/compare_graphify.py` sequentially against pinned code-only Graphify: 108 native retrievals. Integrity audit exited 0. Acceptance exited 1: each Graf arm regressed in 3 of 12 query/budget cases. `decision.py` exited 1 because this six-query diagnostic cannot stand in for the registered full 30-query development decision. Latest and versioned reports explicitly preserve the invalid-evidence decision and blocked product promotion.

The paired broker diagnostic then ran 54 private-snapshot groups and 216 identical retrieval/read pairs across both brokers and all three native arms, with alternating broker order and rotating competitor order. All native payloads matched, allowing only Graphify's printed private graph path substitution; all source reads matched frozen bytes and hashes. Inventory counters confirm v2=3 and v3=2 full checks per retrieval, zero per read, and one at initialization. Independent verification passed 54 receipts, 72 Graf rebuild/export operations, 864 journal records and 432 request counter records, with no diagnostic failures or leftover snapshots. Total native retrievals were 324, source reads 216, and benchmark solver calls zero.

Observed median amortized retrieve/read pair phase times (setup and cleanup divided over four pairs) in milliseconds:

| Native arm | Broker v2 | Experimental v3 |
| --- | ---: | ---: |
| Graf v61 selective | 710.2 | 829.2 |
| Graf v59 rescue (experimental) | 783.4 | 699.5 |
| Graphify code-only | 906.5 | 654.4 |

These are exposed diagnostic samples, not a significant or causal efficiency result. OS caches and external load were uncontrolled. In the paired four-pair group samples, v3-minus-v2 median differences were +466.6ms for v61, +140.4ms for v59 and -971.7ms for Graphify. Negative differences occurred in 3/9, 4/9 and 6/9 groups respectively. Across the 36 paired retrieval samples per arm, negative differences occurred in 15, 24 and 29 cases. Every sample and case is retained; unpaired medians must not hide the mixed paired outcome. In this run current Graf v61 with v3 also had a higher amortized median than Graphify with v3. Removing a scan did not establish a Graf latency improvement.

Evidence: `results/readonly-broker-two-scans-v1-summary.json`, `results/readonly-broker-two-scans-v1-verification.json`, versioned comparison/acceptance/decision reports, and `.research/evidence/readonly-broker-two-scans-v1/`. No accepted Graf version exists; v59 and broker v3 remain experimental. Agent savings are unmeasured, and code-only Graphify hints do not establish source-anchor or documentation-task performance.

Next: retain v2 as default and investigate the mixed end-to-end results under a new freeze rather than promoting v3. Retrieval-quality regressions, complete provider usage accounting and independently authored held-out natural tasks remain the product acceptance blockers. Preserve the existing shared primary metric and margins; size the held-out workload from the registered pilot variance before any product superiority claim.


### Full development case audit and status correction — 2026-10-24

Correction to the prior summaries: confirmation v64 already passed the frozen held-out retrieval rule for both unchanged Graf arms. `results/confirmation-v64-readiness.json` records 123 questions, 111 scored queries, completed source-only AI review and blind pooled judging, a passed retrieval decision, and blocked product promotion. The AI independence and labeling limitations remain. The recent six-query broker diagnostics were incomplete development subsets; their failed gates do not reverse or replace the historical held-out retrieval result. Fresh independent natural agent tasks, complete provider accounting, enforceable common limits and measured task outcomes are the outstanding product acceptance requirements. Any future retrieval tuning requires a new unseen confirmation workload.

Added `benchmark/retrieval_case_audit.py` using an isolated cheap gpt-6-luna implementation agent, with parent review before freeze. It audits saved raw outputs rather than executing retrievers. It verifies query/source hashes, exact case/arm/repetition coverage, query-to-repository binding, repeated file-order consistency, required saved metrics and recomputed code-file recall. Source paths must remain within the frozen root without symlinks. Reports distinguish unique file ranks from repeated citation sections, include expected file ranks/missing files, documentation/test path heuristics and saved token counts, and refuse overwrites or incomplete evidence. Initial two focused tests passed; after review corrections all three focused tests passed. Both logs are preserved. No tests or source audits overlapped timed retrievals.

Preregistration v74 froze a three-repetition rerun of the complete existing 30-query development pilot, both requested budgets, unchanged Graf v61 and v59 binaries and pinned Graphify commit 86c57be8204673c2a1b9849a3292a7dd3ab373d8. Earlier full pilots used one repetition and remain preserved. No algorithms, annotations, margins, primary metric, severe-loss threshold or held-out requirements changed. All 7,794 prior evidence hashes were authenticated and preserved before latest aliases changed. `benchmark/compare_graphify.py` completed 540 sequential native retrievals. Every command, source hash, raw output and timing sample is retained. Builds were fresh per repository; OS caches/external load were uncontrolled, with no cold-cache or latency inference.

Exploratory shared code-file metrics on 27 scored development queries (same at 2K and 8K):

| Arm | All-file hint recall | Top-five hint recall | Top-ten hint recall |
| --- | ---: | ---: | ---: |
| Graf v61 selective | 98.15% | 87.04% | 92.59% |
| Graf v59 rescue, experimental | 98.15% | 87.04% | 92.59% |
| Pinned Graphify, code-only | 87.04% | 70.37% | 83.33% |

These are exposed, exploratory file annotations and different delivered outputs; Graphify navigation hints do not establish source-anchor or documentation-task outcomes. Requested budgets were identical, while actual Graphify overshoots remain recorded: 6 of 30 cases at2K and16 at8K, versus zero for either Graf arm. These raw output metrics are not measured agent token savings.

The saved-output audit covers all60 query/budget cases and180 arm cases. Flask02's expected `src/flask/sessions.py` appears first for all arms; `tests/test_basic.py` is Graf rank6 and Graphify rank2. Graf's first five include documentation at ranks2 and5. This describes the delivered ordering, not a causal intervention. Fastify08 remains incomplete for both Graf arms (0.5 author-label recall), whereas Graphify is0.0 on that query. Every loss is retained.

Native integrity audit exited0. Per-case acceptance exited1: v61 has11 failing query/budget cases (10 output-token regression flags plus two top-five recall flags, overlapping on Flask02 at2K); v59 has10 failing cells (eight output-token and two top-five recall flags). `decision.py` exited1/not_passed with a valid complete development report. Both arms pass the registered primary diagnostic, all Holm quality guardrails and severe-loss tail on this pilot, but development status, only30 queries and only27 scored queries prohibit acceptance; the pilot size remains110 scored queries. No thresholds were changed and no product promotion is allowed.

Independent verification authenticated all prior files, frozen runtime inputs, current native comparison, all540 raw retrieval samples, all60 query/budget and180 arm facts, current/versioned gate reports and unchanged v64 status. Reports: `results/retrieval-regression-audit-v1-cases.json`, `results/retrieval-regression-audit-v1-summary.json`, `results/retrieval-regression-audit-v1-verification.json`; evidence `.research/evidence/retrieval-regression-audit-v1/`. Benchmark solver calls were zero; implementation-agent tokens/spend are unavailable and are not agent-efficiency evidence. No accepted product Graf version exists; broker v2 remains the default and broker v3 experimental.

Next priority: complete provider request/retry/failed-attempt accounting and enforceable common spend/token limits, then prepare and independently review fresh natural agent tasks with objective outcomes. Compare unchanged candidates against pinned Graphify plus source reads using the same solver/model/access and paired limits. Do not spend another unchanged retrieval rerun to stand in for those missing agent prerequisites. Ranking/compactness experiments may use this audit as development evidence, but require new unseen confirmation before promotion.

### Durable transport accounting prerequisite — 2026-10-24

Added `benchmark/recorded_transport.py` using an isolated cheap gpt-6-luna implementation agent, followed by parent review and additional failure tests. This reusable wrapper accepts an injected transport and supports only the two existing count/generation paths. It creates an exclusive private directory, persists configuration and exact request bytes, and syncs the request/begin receipt before invoking the callable. Bounded response bytes and terminal receipts preserve HTTP errors as well as successes. Each repeated request consumes a separate forward-attempt slot; the wrapper never retries. Invalid requests and cap refusals have durable denial receipts. Persistence failures, malformed/oversized responses and cancellation latch refusal of subsequent forwards. Ordinary transport exception messages are omitted; cancellation is recorded and propagated.

The parent added `benchmark/probe_recorded_transport.py` and integration tests around the unchanged `ProviderRequestSession`. Five offline scenarios cover completion, generation HTTP failure, generation transport failure, count HTTP failure and an explicit repeated request hitting a shared three-call cap. Independent reconciliation checks exact saved request/response hashes, ordered begin/end receipts, statuses and the fixture call log: ten actual fixture calls and one durable cap refusal. In the cap case the token ledger admits two generation requests, but only the first generation is forwarded; the second remains unknown usage. This distinction is preserved rather than treating ledger admissions as successful forwards. All 43 focused/integration/existing provider tests and 37 subtests pass. An earlier pre-review run passed 40 tests and 28 subtests in the tool transcript; the final logs and commands are preserved under `.research/evidence/recorded-transport-v1/`.

This is an offline accounting diagnostic, not comparative performance or retrieval evidence. Network requests, retrievals and benchmark solver calls were zero. No Graphify baseline, retrieval implementation, preregistration, acceptance/decision alias or v64 readiness report changed; their hashes are retained in `results/recorded-transport-v1-verification.json`. No new Graphify timing run was required for this noncomparative change. Existing Graphify comparison results remain unchanged.

The wrapper records calls through its boundary; a begin receipt is durable intent, not proof that a provider received a request. Injected transports may hide internal retries unless independently constrained and verified. Raw responses remain private artifacts and may contain sensitive content. Every transport receipt marks usage unknown: token semantics still come from the separate provider ledger. The wrapper does not establish a live token/spend cap, complete agent accounting, or savings. It is not wired into a live solver or promoted as a product improvement. Next: integrate this recorder into one separately frozen provider diagnostic with a verified single-attempt transport, reconcile provider usage and failure receipts, then establish enforceable shared limits before fresh matched natural tasks against pinned Graphify plus source reads.


### Recorded provider protocol diagnostic v2 — 2026-10-24

Integrated the durable recorder into a separately versioned count/admit/generate diagnostic, using an isolated cheap gpt-6-luna implementation agent and parent review. The frozen plan binds all six source files, the exact request and protocol criteria. It permits one count call and one generation call, reserves at most 256 input-plus-output tokens, and requests at most 16 output tokens. This is a single-request protocol diagnostic, not an agent benchmark or proof of provider billing limits.

Reconciliation checks exact transport receipt coverage, raw request/response hashes, count-journal bindings, generation admission and terminal usage. Recorder usage remains explicitly unknown; validated response usage belongs to the separate ledger. Tests cover HTTP and transport failures, absent usage, count/model mismatches, admission denial, plan tampering, altered journals, extra receipts, detailed cached/reasoning usage, and output-limit interruptions. A mock HTTPS test exercises the actual transport without network access and checks that authorization is absent from saved artifacts.

The frozen combined run passed 61 tests and 37 subtests. Preflight exited 1 with `missing_OPENAI_API_KEY` and `execution_not_requested`; it made zero network requests, retrievals or solver calls. All 100 prior evidence entries remained unchanged. Independent verification authenticated 114 hash entries across prior artifacts, current sources, frozen evidence and the preflight report. Verification report SHA-256: `8be0e85d469dd26a780f9982b971b701d9ba7397815f6b9b87db4e81276e2fe0`.

Evidence: `.research/evidence/openai-provider-v2/`, `results/openai-provider-v2-preflight.json`, and `results/openai-provider-v2-verification.json`. Live provider verification, full agent accounting and product promotion remain false; agent savings remain unmeasured. No comparative retrieval evaluation occurred, so the pinned Graphify results and existing acceptance/decision reports are unchanged. The historical held-out retrieval pass does not establish agent efficiency.

Next: validate a supported provider path with an environment-supplied credential and a separately preserved execution report before attempting matched natural-task agent runs against pinned Graphify plus source reads. Claude Code and Codex are installed, but subscription CLI histories do not establish complete internal retry accounting or enforceable common provider limits. No model access or live token-cap guarantee follows from these offline tests.


### Bounded read-only agent loop prerequisite — 2026-10-24

Added `benchmark/bounded_agent.py`, an experimental local JSON-action loop connecting an existing `ProviderRequestSession` to the broker's JSON-RPC `handle` interface. It constructs no transport and runs no subscription CLI. The caller supplies and owns the counted session and broker. Each next model request includes the task, full tool inventory and all prior serialized actions/results. The provider session applies its cumulative count, attempt and token-reservation limits across turns. The loop separately bounds turns, tool actions and request/response bytes. It accepts only read/retrieve actions or a final answer, checks returned model and count/observed-input agreement, and stops on unknown usage, non-completion, malformed actions or protocol failure. There are no implicit retries or transcript clipping. A final answer remains an unverified task outcome.

Private exclusive evidence records configuration, model boundaries, exact request hashes, full tool requests/results and terminal status. Oversized tool responses are preserved in full but prevent further model requests. Tool requests are detached before dispatch so broker mutation cannot alter the recorded action/history. Event persistence is required before tool dispatch; cancellation is recorded and propagated. These local bounds do not enforce a shared elapsed-time limit, validate a live provider cap or establish dollar spend accounting. Raw private artifacts may contain task/source content. The protocol uses text JSON actions, not native provider tool calls; model reliability on this protocol is untested live.

The initial 17-test run passed before final review and freeze (tool transcript). Added broker-mutation, nonfresh-session and saved-probe checks, then froze sources and commands under `.research/evidence/bounded-agent-v1/`. The final combined suite passed 93 tests and 37 subtests. Integration tests exercise the existing artifact-pinned v2 broker and an actual frozen source read with a fixture retrieval subprocess. No native Graf or Graphify retrieval was executed.

`benchmark/probe_bounded_agent.py` preserves three synthetic scenarios: completion (six forwarded count/generation calls, two tools); cumulative reservation exhaustion (five forwarded calls, two tools, third generation denied); generation transport failure (two forwarded calls, no tool). All 13 calls reconcile against durable raw transport receipts and an independent fixture call log. Token values are invented fixture counters, not tokenizer or provider measurements. All full tool results appear in the completed scenario's subsequent request. Both recorded commands exited 0. Zero network requests, native retrievals or benchmark solver calls occurred; no efficiency or quality comparison was conducted.

Verified all 111 prior evidence entries unchanged. Independent checks authenticated 257 source/artifact hash entries, reconstructed every model request from saved count projections, and checked loop boundaries, tool counts and raw response hashes. Report: `results/bounded-agent-v1-verification.json`, SHA-256 `ae1d5b3744e59b2eed1df664745486f8bac61a6cbf5263b0179d47dcb3b90627`. Existing preregistration, Graphify pin/results, acceptance/decision reports and historical v64 retrieval status are unchanged. No accepted product Graf version exists; full agent accounting, live provider verification and product promotion remain false; agent savings remain unmeasured.

Next: verify a supported provider path live; establish enforceable common time/spend limits and objective natural-task outcome checks; independently author and review fresh held-out tasks; then freeze and run sequential matched Graf versus pinned code-only Graphify plus identical source reads with one solver/model/access and common limits. These synthetic fixtures cannot satisfy the product acceptance gate.


### Local process deadline prerequisite — 2026-10-24

Added experimental `benchmark/deadline_supervisor.py` using an isolated cheap gpt-6-luna implementation agent and parent review. It launches a trusted worker in a fresh POSIX process group, durably records intent before dispatch, applies the remaining monotonic deadline, kills descendants even after normal leader exit, and boundedly verifies cleanup. A deadline consumed by journaling prevents launch. Cancellation and journal failure trigger cleanup before propagation. Uncertain process-group exit produces `reap_unknown`, never successful completion. Private exclusive files preserve exact stdout/stderr bytes, hashes, byte counts, ordered events and the terminal report. Environment values are not written into supervisor metadata; worker output remains raw and may contain private content.

`benchmark/supervised_agent_fixture.py` and `benchmark/probe_supervised_agent.py` connect this supervisor to the existing bounded agent, provider session and durable transport recorder using offline fixtures. Saved scenarios cover normal completion, a hung generation and a hung broker read. The generation interruption retains an unfinished admitted request and unknown usage; the broker interruption retains the unfinished tool boundary after recorded fixture generation usage. Neither interrupted loop receives a fabricated successful terminal report. Fixture counters are invented values, not provider/tokenizer measurements.

The frozen combined run passed 106 tests and 44 subtests; both preserved commands exited 0. Tests include descendant cleanup, cancellation, persistence failure, exact binary output, private modes, unverified cleanup, pre-launch deadline exhaustion and the existing agent/accounting/broker regressions. Independent verification authenticated 452 hash entries, including all 254 prior entries unchanged, and checked exact raw-output hashes/counts, journal order and saved scenario reports. Evidence: `.research/evidence/supervised-agent-v1/`; report: `results/supervised-agent-v1-verification.json`, SHA-256 `7cbfdac4676dc9f2c7733f2f325ac77df5d566eb32f7095041dfdc9c042b4ef6`.

This is local process correctness evidence. Process creation and provider-side work have no hard wall-clock guarantee; local termination does not prove remote cancellation or stopped billing. Workers must be trusted not to escape the process group. Raw-output storage has no byte cap, and cleanup/persistence failures remain failures. Complete provider usage, shared dollar-spend limits and live protocol behavior remain unverified. Zero network requests, native retrievals or benchmark solver calls occurred. No comparative efficiency evaluation occurred; Graphify pins/results, preregistration, acceptance/decision reports and historical v64 status are unchanged. Agent savings remain unmeasured and product promotion remains blocked.

Next: validate a supported live provider path and interrupted-call accounting under separately frozen common resource limits; establish objective natural-task outcomes and independently authored fresh held-out tasks; then compare Graf against pinned Graphify plus identical source reads using matched solver/model/access and sequential paired trials. The local supervisor does not satisfy those remaining product acceptance requirements.
