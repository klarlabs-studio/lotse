# Audit repairs, 2026-10-22

The five audited gaps are addressed. No product improvement or efficiency win is promoted.

- MCP checks the complete eligible source set before every tool call, including new terms in previously unmatched files, additions, removals, renames, and ignore-rule changes. Hash checks add filesystem work; current MCP latency and agent efficiency remain unmeasured.
- MCP search prints globally unique expansion handles and repacks complete source sections so the handles fit within the context budget. CLI search output is unchanged.
- The decision evaluator rejects workloads below the preregistered minimum and checks scored-query counts against the frozen per-arm pilot plan. Preregistration v26 records this repair and the development-only verification run before its new data. The pilot sizes are derived from the preserved previous development decision; this is not a retroactive promotion opportunity for the already observed held-out set.
- Decisions have separate development and held-out aliases plus immutable content-addressed copies. The original held-out decision and its contemporaneous preregistration were recovered from commit `20cb053`; the held-out report now links to those exact artifacts. The required `results/decision.json` remains the latest report.
- Project status and setup documentation reflect the three completed agent batches. The scale report distinguishes historical timing boundaries from current indexing and MCP behavior.

## Verification

59 Rust tests and 137 Python tests pass. Formatting, Clippy with warnings denied, and the locked release build pass. New regression coverage exercises source membership and term changes, handle expansion and budgets, minimum/pilot sample sizes, and preservation of held-out reports across development runs.

The paired comparison used unchanged pinned **code-only Graphify**, all 30 frozen development queries (27 code-scored), both 2K and 8K budgets, the same cl100k tokenizer, all 13 graf arms, and one sequential invocation per tool and case: 60 query/budget cases and 840 timing samples. Preregistration v26 freezes this as a quality/token regression diagnostic. Fresh indexes were built for both tools; OS caches were uncontrolled and the machine was under load. One repeat cannot establish latency improvement. Native Graphify hints and graf source excerpts remain different outputs; only code-file hint recall is shared here. Documentation-task performance and current-build agent savings are unmeasured. No accepted graf release exists for a release-baseline comparison.

Selected arm summaries follow; [raw results](../results/audit-2026-10-22-comparison.json) preserve every case, every arm, all commands, output text, source hashes, and timing samples. Recall is the mean over the 27 code-scored queries; tokens are mean delivered text across all 30 queries. These are development diagnostics, not a promotion result.

| Budget | Arm | Mean delivered tokens | Code-file hint recall |
| --- | --- | ---: | ---: |
| 2K | graf default search | 1,979.10 | 72.22% |
| 2K | graf default locate | 1,784.13 | 94.44% |
| 2K | graf damped/floor locate (experimental) | 627.20 | 88.89% |
| 2K | pinned code-only Graphify | 1,750.17 | 96.30% |
| 8K | graf default search | 7,965.73 | 94.44% |
| 8K | graf default locate | 3,036.23 | 98.15% |
| 8K | graf damped/floor locate (experimental) | 640.23 | 88.89% |
| 8K | pinned code-only Graphify | 14,647.80 | 96.30% |

Graphify exceeded the requested cl100k budget on 11 of the 30 8K cases; its native output was retained, with no truncation or adjustment. Thus the 8K token totals are not equal realized-output budgets and cannot establish equal-budget superiority. At 2K, default locate still delivers more tokens and has lower recall than Graphify; the smaller experimental arm loses more recall. Aggregates do not erase the individual losses in the full reports.

All 780 graf CLI output strings are identical to the previous saved development comparison. This is an output-integrity check, not evidence of comparative efficiency. The repaired MCP search and freshness paths are covered by functional tests, not this CLI comparison.

The [source/output audit](../results/audit-2026-10-22-comparison-audit.json) passes. Both [acceptance](../results/acceptance.json) and the [decision rule](../results/decision-development.json) return **`not_passed`, exit code 1**, and product promotion remains false. This development workload is ineligible, is below the held-out sample-size requirement, and has per-case regressions. The original held-out result remains separately preserved and unpassed.

Commands, exit codes, hashes, cache conditions, and the preserved source/binary bundle path are recorded in [verification.json](../results/audit-2026-10-22-verification.json). Earlier comparison files and their scratch trees were retained. No inference was run for these repairs; historical agent outcomes remain in [agent-results.md](agent-results.md).
