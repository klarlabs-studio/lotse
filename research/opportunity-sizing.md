# Opportunity sizing (2026-11-03, preregistration v152)

**Of three tool ideas, only the context profile has real headroom.** The impact check misses its threshold by a wide margin. Test targeting clears it on paper, but only because the threshold was miscalibrated for long sessions. Offline, no model calls, exploratory: this picks what to pilot and supports no claim. Script: `benchmark/opportunity_sizing.py`; numbers: [opportunity-sizing.json](../results/opportunity-sizing.json).

Data: the 1,686 t10–t15 batch attempts, and the operator's own Claude Code main sessions from the last 60 days (958 transcripts, of which 48 have five or more turns and 37 run tests). Only aggregate counts were written.

| Idea | Measure | Threshold | Result | Verdict |
|---|---|---|---|---|
| Context profile | Median share of per-turn context that is prefix above the benchmark's minimal setup (real sessions) | ≥ 0.10 | 0.234 | clears |
| Test targeting | Setup failures per test-running session (real sessions) | ≥ 2.0 | 3.11 | clears as written; not on substance (below) |
| Impact check | Repeat failures whose next edits touch an unopened, call-linked source file (benchmark) | ≥ 0.25 | 0.028 (upper bound without call links: 0.046) | fails |

## Context profile

- Real sessions start from a median prefix of 71,060 tokens, against 7,972 for a t15 `builtin` attempt. The prefix stayed between about 50,000 and 115,000 tokens in every long session.
- That prefix is re-read on every turn, and it is a median 27% of all context read in a session. The excess over the benchmark's minimal setup alone is 23%.
- The benchmark baseline is a stripped Claude Code setup, so not all of the excess can be trimmed. Part of it is configuration that went unused in the window: no session invoked a skill from the sales, legal, finance, marketing, product-management, design or data plugins. Of the configured MCP servers, about 14 were called at all, most of them in fewer than 10 sessions. The global `CLAUDE.md` is about 24,000 characters (about 6,000 tokens), loaded into every session.
- How much of the prefix each component takes was not measured. That needs one first-turn measurement per configuration, which is a model call.

## Test targeting

The ≥ 2 setup failures per session threshold was set with benchmark-sized sessions in mind (about 12 turns). Real test-running sessions are far longer, with a mean of 291 test calls each. The 115 setup failures are about 1% of 10,754 test calls. Only 5.7% of test calls were unscoped, and test output was 2.3% of tool-result text. Agents in these sessions already run scoped tests and rarely fail to start them. On substance this does not clear, and it is dropped.

## Impact check

834 repeat failures. In 23 of them (2.8%), the next edits touched a source file that was unopened and call-linked to an already-edited file. Even without the call-link condition, only 4.6% touched any unopened source file. After a repeat failure, agents fix code they already have open. Dropped. Among first failures, 27% of next edits touch an unopened file, but only 5.6% are call-linked.

## Correction

This sizing measured the benchmark's per-turn context directly. The fixed prompt is a median 7,700 to 9,600 tokens, and the whole context is 18,000 to 22,000 tokens per turn. The agent-efficiency conclusion had said the fixed context was about 60,000 tokens. That was wrong and is corrected there. Its conclusion, that turns set the cost, is unchanged.
