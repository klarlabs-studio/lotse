# Complete-task comparison: preparation and current status

Three model-backed paired batches have now completed using the broker-only Claude Code adapter, fixed `claude-haiku-5-5` model, and $0.50 estimated per-run cap. See [agent-results.md](agent-results.md) for outcomes and [preregistration.json](../benchmark/preregistration.json) for each batch. The natural-task batch found no significant cost advantage for graf over Graphify or search-only. The fixture preparation below describes the original harness, not those later outcomes.

## Validated tool broker

`benchmark/task_session.py` exposes the same JSONL operations for both arms: `retrieve`, `read`, `search`, `edit`, `test`, and `finish`. It is a benchmark application protocol. The subsequent `benchmark/broker_mcp.py` adapter and `benchmark/run_agent_trial.py` runner expose it to the fixed solver; this benchmark interface is separate from the product `graf mcp` server.

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

Freeze the solver/model, reasoning/decoding settings, model context, tool schemas, per-call output limits, trial/turn/wall-time/spend caps, and retrieval budget before inference. Use independent faulty workspaces and matched task/repetition pairs, alternating arm order. The original six-fixture smoke plan specified three pairs per task (36 attempts); the later completed batches instead use their separately preregistered task catalogs and five attempts per task and arm.

Capture every actual model call, full input/output/cache/reasoning usage when available, raw provider events, tool payloads, final changed files, verification logs, and all failures. Cached and reasoning tokens are subsets, not additions to total input/output. Do not infer dollar charges from subscription access or fabricate missing fields as zero. The normalized usage auditor accepts explicit `null` for unknown cache/reasoning breakdowns or dollar charges, retaining measured input/output totals and marking incomplete aggregates unknown. A limit-exhausted attempt is a failure even if a later out-of-budget operation could repair it. Report all-attempt usage and success before any paired-success-only statistic.

The broker records reference `cl100k_base` counts of its complete JSON responses as diagnostics. Those are not provider usage, model conversation totals, or dollar cost. The solver adapter must record provider usage including tool definitions, repeated conversation context, fallback calls, and verification interaction. Tool transcripts by themselves are unscoreable as agent traces.

The original local Codex CLI inspection was a preparation diagnostic and made no model calls. Subsequent trials used Claude Code with the benchmark broker as its only tool surface. Their provider usage, all-attempt cost estimates, verified outcomes, invalid earlier batch, and natural-task replication are reported in [agent-results.md](agent-results.md). These outcomes do not validate the current product MCP server or establish a general product-efficiency win. No inference was run as part of the 2026-10-22 audit repairs.
