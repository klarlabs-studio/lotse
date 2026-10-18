# Complete-task comparison: preparation and current status

The comparison has not run model-backed solvers yet. Its purpose is to test whether either retrieval backend improves verified repair success or total solver usage, including navigation, source reads, edits, verification, and failed attempts. File-discovery recall alone cannot answer that question.

## Validated tool broker

`benchmark/task_session.py` exposes the same JSONL operations for both arms: `retrieve`, `read`, `search`, `edit`, `test`, and `finish`. It is a benchmark application protocol, not a Codex/Claude adapter or an MCP server. A solver adapter remains to be selected/configured with a fixed model and inference limit.

The graf arm uses compact locate; the Graphify arm uses the pinned code-only, unclustered extraction and native query text. Both index the same faulty code/docs snapshot. Source reads, fallback regex search, exact-string edits, and trusted test commands are common. This first controlled comparison isolates retrieval with common source reads; it does not evaluate every native Graphify feature or the complete graf locate/expand product interface. Documentation-enabled Graphify and natural product-document tasks remain outside this smoke-test scope.

Indexes build from the current faulty workspace, never the clean reference. Edits mark them dirty; the next retrieval rebuilds them. Build receipts preserve source hashes, commands, outputs, and elapsed time. Native Graphify budgets may exceed the requested cl100k amount; raw text and the actual payload are retained. Both indexing and tool time must be included in task accounting rather than hidden in setup.

Source reads reject parent traversal, private metadata, hidden index paths, dependency paths, and symlinks. Graphify's absolute staged-input citations map back to the faulty workspace. Test/harness edits through the broker are rejected, and verification checks protected test hashes persisted before the attempt. `finish` runs trusted verification, records its actual result, and closes the session. A solver's statement that tests passed is never verification evidence.

Generated clean `.graf` indexes, Graphify output, `.git`, dependency trees, and selected caches are excluded when preparing a fault workspace. The broker can therefore not accidentally return original source from an inherited clean index or Git history.

These are application-level tool restrictions, not an operating-system sandbox around arbitrary solver executables. A valid adapter must expose only these tools to the model. A CLI with unrestricted native file/shell access could read private metadata or bypass logging and is not a valid configuration of this experiment. The model receives the issue and public tool definitions; private task definitions, original source, fault strings, session metadata, and reference annotations remain in the trusted harness.

## Fixture validation is not an agent result

`benchmark/check_task_sessions.py` exercised all six synthetic faults with both backends: 12 task/backend sessions and 36 actual behavioral-test invocations. Each session passes at the base, fails with its injected fault, and passes after the fixture checker restores known pristine source. It also checks faulty-snapshot indexing, source-read hashes, refresh after edits, cache exclusion, terminal verification, and identical initial source sets between arms.

Restoration uses known original source and is not a solver repair. No model is invoked. Tool-only transcripts are deliberately rejected by the agent usage auditor. The saved [fixture results](../results/task-broker-sanity.json) cannot support agent-success or token-saving claims.

Reproduce with a fresh run ID; existing sessions are never overwritten:

```sh
.research/venv/bin/python benchmark/check_task_sessions.py --run-id paired-broker-v2
.research/venv/bin/python -m unittest discover -s benchmark -p 'test_*.py' -v
```

## Model-backed trial requirements

Freeze the solver/model, reasoning/decoding settings, model context, tool schemas, per-call output limits, trial/turn/wall-time/spend caps, and retrieval budget before inference. Use independent faulty workspaces and matched task/repetition pairs, alternating arm order. The existing protocol specifies three pairs per task: 36 solver attempts for six tasks and two arms. A smaller one-pair-per-task smoke run is 12 attempts and must be labeled separately.

Capture every actual model call, full input/output/cache/reasoning usage when available, raw provider events, tool payloads, final changed files, verification logs, and all failures. Cached and reasoning tokens are subsets, not additions to total input/output. Do not infer dollar charges from subscription access or fabricate missing fields as zero. The normalized usage auditor accepts explicit `null` for unknown cache/reasoning breakdowns or dollar charges, retaining measured input/output totals and marking incomplete aggregates unknown. A limit-exhausted attempt is a failure even if a later out-of-budget operation could repair it. Report all-attempt usage and success before any paired-success-only statistic.

The broker records reference `cl100k_base` counts of its complete JSON responses as diagnostics. Those are not provider usage, model conversation totals, or dollar cost. The solver adapter must record provider usage including tool definitions, repeated conversation context, fallback calls, and verification interaction. Tool transcripts by themselves are unscoreable as agent traces.

The local Codex user configuration currently specifies `gpt-6.1-sol` with low reasoning. Codex CLI 0.156.1 is installed; no model call was made during inspection. Official [non-interactive documentation](https://learn.chatgpt.com/docs/non-interactive-mode) describes JSONL events with turn usage, and the local CLI exposes JSON output and explicit model/config options. This establishes a potential logging path, not a completed adapter, confirmed account access, spending cap, or validated model-tool isolation.

The requested solver/model and total inference budget remain unspecified. Until those are fixed and the adapter satisfies the tool-isolation/accounting contract, agent task outcomes and token savings remain unmeasured. These six synthetic single-fault tasks are harness smoke tests; independently reviewed natural issues are still needed before product-level superiority claims.
