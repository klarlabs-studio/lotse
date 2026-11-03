# Agent efficiency: conclusion (2026-11-02)

**graf does not meet the preregistered agent-efficiency requirement, and no graf build is promoted.** In six batches with graf installed as Claude Code's tool (t10 to t15, 1,782 attempts across all arms), graf was never significantly cheaper than Claude Code's own tools. It was significantly cheaper than Graphify once (t14), which the rule does not count as a pass on its own. Exploratory follow-ups to t15 explain why: on these tasks graf helps only with finding the code, and finding the code is a small part of what an attempt costs. This research line is closed; graf's retrieval results, which do not depend on agent runs, are unaffected.

Scope: issue-driven repair of real upstream bugs in Go, Python and JavaScript repositories, with hidden fail-to-pass tests; `claude-haiku-5-5` under Claude Code, $0.50 and 1,200 s per attempt, two attempts per task and arm. Nothing here speaks to other models, larger repositories, vaguer issues, or tasks without a test tool.

## The confirmatory batches

Each batch was frozen in `benchmark/preregistration.json` before its first model call, on the latest Graphify, Claude Code and Haiku releases of the day. The rule (intersection-union): graf must be significantly cheaper per task (one-sided paired sign flip, alpha 0.05) than **both** `builtin` (Claude Code without a retrieval tool) and `graphify_installed`, with success non-inferior within 0.10 against each. Success was non-inferior against both comparators in every batch; cost decided every outcome.

| Batch | Tasks | Claude Code / Graphify | Total cost: builtin / Graphify / graf | p, graf vs builtin | p, graf vs Graphify | Rule |
|---|---|---|---|---|---|---|
| t10 | 48 | 2.1.312 / 0.9.97 | $1.679 / $1.275 / $1.186 | 0.288 | 0.117 | fails |
| t11 | 47 pointer-free | 2.1.314 / 0.9.97 | $1.294 / $1.499 / $1.451 | 0.743 | 0.475 | fails |
| t12 | 48 | 2.1.314 / 0.9.97 | $0.936 / $1.076 / $0.893 | 0.376 | 0.016 (Holm over two candidates: fails) | fails |
| t13 | 47 | 2.1.316 / 0.9.99 | $0.790 / $1.103 / $0.973 | (reported) 0.895 | 0.140 | fails (Graphify only) |
| t14 | 47 search-heavy | 2.1.316 / 0.9.100 | $1.735 / $1.990 / $1.611 | 0.248 | **0.0004** | fails |
| t15 | 44 search-heavy | 2.1.316 / 0.9.100 | $1.425 / $1.508 / $1.395 | 0.345 | 0.052 | fails |

Decisions: `results/agent-decision-t10.json` to `results/agent-decision-t15.json`. Per-batch details, completeness and deviations are in [agent-results.md](agent-results.md).

## Why graf cannot win on this workload

These analyses were run after t15 on finished transcripts or offline replays; they are diagnostics, and no claim rests on them.

1. **Turns, not payloads, set the cost.** Each turn re-reads the whole context so far from cache: a median of about 18,000 to 22,000 tokens per turn across t10 to t15, about half of it Claude Code's fixed prompt (a median of 7,700 to 9,600 tokens, depending on the arm). Tool results add only about 9,000 tokens per attempt. A tool saves money only by saving turns. (Corrected 2026-11-03: an earlier version said the fixed context alone was about 60,000 tokens; see [opportunity-sizing.json](../results/opportunity-sizing.json).)
2. **Finding the code is cheap already.** `builtin` reads a faulted file after a median of 2 tool calls, Graphify after 3, graf after 1. graf's triage saves about 1.5 calls before the first edit and none afterwards ([loop-phases.json](../results/loop-phases.json)).
3. **Half the calls come after the first test run, which fails in about 85% of attempts.** About 70% of those first failures are assertion errors, not missing code.
4. **Better excerpts do not shorten the loop.** Showing functions from triage's own top source files raised fault coverage offline from 72 to 91 of 182 calls (p = 0.003), but in a 44-attempt pilot (t15x, v151) mean cost was only 2% below graf's t15 build and 4% below `builtin`, short of the preregistered 5%, and the first test run passed no more often ([t15x-pilot.json](../results/t15x-pilot.json)).
5. **After a failed test the agent is usually in the right file.** Failure output names a source file in about 15% of failed runs. A tool built from the output (`graf failure`) reaches the faulted lines in 27% of 851 replayed failures, but by then the agent had already edited a faulted file in 76% of them. At most about one look-up call per attempt could be saved, less than the turn the tool itself costs ([debug-phase.json](../results/debug-phase.json), [failure-check.json](../results/failure-check.json)).

The remaining cost is the agent writing, testing and correcting its fix. A retrieval tool does not reach it.

## What still holds

- Against Graphify, graf cost less in total in all six batches and significantly less per task in t14 (p = 0.0004); it also used fewer tokens per task in t13 (80,000) and t14 (116,000). That is a single-comparison result, not a pass under the rule, and not a superiority claim; t13 and t15 did not reach significance.
- graf's offline retrieval and localization results, with their own preregistrations and Graphify comparisons, are unaffected: see [localization-results.md](localization-results.md) and [graphify-comparison.md](graphify-comparison.md).
- Every agent candidate (`triage`, the graph-triage and triage-excerpts builds, `failure`) stays experimental, behind its feature or labelled as such.

## What would reopen the question

Only a workload where finding the code takes real search: large repositories, vague issues, or tasks without a fast test loop. That would need its own workload, screen and preregistration. One warning from t15: on the 13 tasks where `builtin` needed 5 or more calls to reach the fault, graf cost about 7% more, not less (small sample, exploratory).
