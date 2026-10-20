# Paired agent trials

Three preregistered batches with the same solver and spend cap. Batches `t` and `t2` use 20 synthetic fault-repair tasks; batch `t3` uses 18 natural tasks mined from real upstream fixes. The newest batch is reported first.

## Batch t3: natural tasks; no arm separates

**Outcome: the candidate `graf_floor` fails.** It was not significantly cheaper than either comparator, `search_only` or Graphify. All three arms cost the same, within noise. Every task and arm summary is in [agent-decision-t3.json](../results/agent-decision-t3.json). The batch is `t3` in [preregistration v14](../benchmark/preregistration.json).

### Conditions

- **Tasks.** 18 natural tasks ([natural-tasks.json](../benchmark/natural-tasks.json), frozen catalog sha256 `e2867b67…`): 5 from Cobra, 3 from Flask, and 10 from Fastify. Each one reverts a real merged fix that closes an issue and predates the pin. The fix can span several places, and its tests stay hidden until grading. The agent sees the issue text verbatim. Selection followed the preregistered protocol (v12; v13 widened the size bound to 10 files and 400 lines and added drift, timeout, and mismatch rules; v14 froze the yield of 18 as the minimum). Every candidate and its outcome is in [natural-task-selection.json](../results/natural-task-selection.json).
- **Arms.** `graf_floor` (candidate): `graf locate --file-score damped --stop floor`, same binary as `t2`. Comparators: `search_only` (no `retrieve` tool) and pinned code-only Graphify. The candidate must pass against both (intersection-union), so alpha is not split: 0.05 for each test.
- **Plan.** 270 runs, 5 per task and arm, interleaved (seed 20261021), on 2026-10-20. Solver, prompt, and per-run cap ($0.50 estimated) are the same as `t2`. Cost is a list-price estimate, not a charge.
- **Run integrity.** There were no infrastructure errors, every run finished normally, and none hit the cap. The batch stopped twice on harness bugs. Both were fixed with tests (`d142f16`, `abcce0e`), and each affected trial was rebuilt from its saved artifacts without rerunning the agent:
  - `t3-fastify-6411-search_only-5`: tool calls sent alongside `finish` were refused after the verdict, and the trace audit rejected that. Result: failed.
  - `t3-cobra-1651-graphify-1`: a refused second `finish` wrote a spurious failing verdict. It was dropped. Result: passed.
- **Interim look (deviation).** After the second stop, at about 172 of 270 runs, I ran the decision script on the partial data and saw interim arm totals. The rule, tasks, and plan were not changed afterwards, and the remaining runs went ahead as scheduled. The disclosure is here because the look was not planned.

### Results

| | graf_floor | search_only | Graphify |
| --- | ---: | ---: | ---: |
| Verifier success | 83/90 (0.848–0.962) | 83/90 (0.848–0.962) | 85/90 (0.876–0.976) |
| pass^5 | 0.78 | 0.83 | 0.78 |
| Total estimated cost | $0.403 | $0.406 | $0.401 |
| Total tokens | 8.44 M | 7.66 M | 7.92 M |
| Mean `retrieve` calls per run | 0.88 | — | 0.88 |
| Mean `retrieve` payload (cl100k) | 686 | — | 1,729 |

- **graf_floor vs search_only.** Mean cost reduction +$0.00004 per task, p = 0.42; cheaper on 8 of 18 tasks. Success difference 0.00 (non-inferior, p = 0.0005). Tokens: graf_floor used 8,637 more per task. **Fail (primary).**
- **graf_floor vs Graphify.** Mean cost reduction −$0.00002 per task, p = 0.52; cheaper on 13 of 18 tasks. Success difference −0.02 (non-inferior, p = 0.009). Tokens: 5,787 more per task. **Fail (primary).**
- **Failures.** All 19 failures were wrong fixes that the agent submitted with `finish`. They cluster on `fastify-4327` (graf_floor 2/5, search_only 3/5, Graphify 4/5), `fastify-6411` (3/2/4), and `cobra-1257` (4/3/3). Twelve tasks were solved by every arm on every attempt.

### Interpretation within these conditions

- On natural tasks with this solver, neither retriever reduced cost compared with search and reads alone, and they did not differ from each other. graf_floor's smaller payload did not turn into savings: Graphify was cheaper on 5 tasks, by larger amounts, mostly the hard ones.
- The `t2` win over Graphify did not replicate on natural tasks. It should be read as specific to those synthetic tasks.
- These tasks are harder (pass^5 ≈ 0.8), but 12 of 18 are still at ceiling, and 18 tasks × 5 attempts cannot resolve per-task cost differences of a few hundredths of a cent.
- Contamination: the fixes are public and predate the solver's training cutoff, so the solver may recall them. That affects all arms equally, but it may shrink the role of retrieval.
- Nothing is promoted. graf_floor also still fails the held-out retrieval guardrail (total recall at 8K).

## Batch t2: floor-stop candidate passes; no retriever is just as cheap

**Outcome: the candidate `graf_floor` passes the agent-trial rule against Graphify. That does not authorize promotion.** It failed the held-out retrieval rule (total recall at 8K), these tasks are synthetic, and the search-only control was as cheap. Every task and arm summary is in [agent-decision-t2.json](../results/agent-decision-t2.json). The batch is `t2` in [preregistration v11](../benchmark/preregistration.json).

- **Arms.** `graf_floor`: `retrieve` runs `graf locate --file-score damped --stop floor`, the held-out arm with unchanged constants, and the same binary as batch `t`. `search_only` (control): no `retrieve` tool, and the system prompt omits it. `graphify`: as in batch `t`. 300 runs, 5 per task and arm, interleaved (seed 20261020), on 2026-10-20. There were no infrastructure errors, and no run hit the cap.
- **Alphas.** Both are split across the two tested arms: 0.025 each.

| | graf_floor | search_only (control) | Graphify |
| --- | ---: | ---: | ---: |
| Verifier success | 99/100 (0.946–0.998) | 99/100 (0.946–0.998) | 96/100 (0.902–0.984) |
| pass^5 | 0.95 | 0.95 | 0.90 |
| Total estimated cost | $0.241 | $0.236 | $0.308 |
| Total tokens | 4.17 M | 3.83 M | 5.85 M |
| Mean `retrieve` payload (cl100k) | 471 | — | 1,755 |

- **graf_floor against Graphify.** Cost reduction was $0.00067 per task (p < 0.0001), and it was cheaper on 16 of 20 tasks. Success difference +0.03 (non-inferior, p < 0.0001). Token reduction 16,751 per task (p < 0.0001). **Pass.**
- **search_only against Graphify (control).** Cost reduction was $0.00072 per task (p = 0.0002), and it was cheaper on 18 of 20 tasks. Success +0.03. It also clears both tests, but as a control it cannot pass the batch.
- **graf_floor against search_only (exploratory, not preregistered).** Mean cost difference +$0.00005, with graf_floor cheaper on 8 of 20 tasks; the difference is unresolved in either direction (p = 0.72 and 0.28). On these tasks, there is no evidence that any retrieve tool beats regex search and reads.
- **Between batches.** Graphify's cost rose from $0.267 to $0.308, and its successes fell from 100 to 96. The run-to-run variance across batches is material, which is why each batch reruns its comparator.

### What this establishes, within these conditions

- With a solver that calls retrieve about once, payload size drives cost. Cutting graf's payload from about 2K to about 470 tokens turned a loss against Graphify (batch `t`) into a significant win.
- Graphify's hints cost more than they saved on these tasks. Search and read alone were cheaper.
- graf_floor is still not promotable. It failed the held-out retrieval guardrail on total recall at 8K. It has not shown an advantage over having no retriever. And these synthetic tasks are at or near ceiling. Promotion needs natural held-out tasks hard enough to separate the arms, with a search-only control preregistered as a comparator.

## Batch t: first valid batch

**Outcome: graf (default `locate`) fails the preregistered agent-trial rule. Nothing is promoted.** The candidate was not cheaper than Graphify plus source reads. Every task and arm summary is in [agent-decision-t.json](../results/agent-decision-t.json). The rule is `agent_trials.rule` and batch `t` in [preregistration v10/v11](../benchmark/preregistration.json).

### Conditions

- **Tasks.** 20 synthetic fault-repair tasks over the pinned Cobra, Flask, and Fastify snapshots ([agent-tasks.json](../benchmark/agent-tasks.json)). Each passes at base, fails after fault injection, and passes when restored. These are harness evidence, not natural held-out issues.
- **Solver.** Claude Code 2.1.302 headless, `claude-haiku-5-5`, the same system prompt, no built-in tools, no user settings, and the task broker as its only MCP server. The per-run cap was $0.50 estimated; no run hit it.
- **Arms.** `graf`: `retrieve` runs `graf locate --text` (binary sha256 `0ea031d9…`, output-identical to the audited build on 1,631 outputs). `graphify`: `retrieve` queries pinned code-only Graphify. Both arms share `read`, `search`, `edit`, `test`, and `finish`.
- **Plan.** 5 attempts per task and arm (200 runs), in a fixed random interleaved order (seed 20261019), on 2026-10-20 under heavy unrelated machine load. Load affects wall time only. Tokens and cost come from provider-reported usage, and the trusted verifier decides success.
- **Cost.** Cost is a list-price estimate under a Claude Max subscription, not a charge.

### Results

| | graf | Graphify |
| --- | ---: | ---: |
| Verifier success | 100/100 (95% CI 0.963–1.0) | 100/100 (95% CI 0.963–1.0) |
| pass^k, k = 1..5 | 1.0 | 1.0 |
| Total estimated cost, all attempts | $0.292 | $0.267 |
| Total tokens, all attempts | 5.37 M | 4.65 M |
| Mean `retrieve` payload (cl100k) | 1,969 | 1,810 |

- **Primary (cost superiority).** Mean per-task reduction −$0.00025 (graf costs more), one-sided p = 0.97. Fail. graf was cheaper on 7 of 20 tasks.
- **Success (non-inferiority, margin 0.10).** Difference 0.00, p < 0.001. Pass.
- **Tokens (secondary).** graf used 7,206 more tokens per task on average (p = 0.92 for a reduction).
- **Biggest per-task loss.** `fastify-hooks-run-after-reply-sent`: 170k vs 81k tokens; `flask-nested-blueprint-subdomain`: 121k vs 75k.

### Interpretation within these conditions

- Both arms solved every task, so these tasks cannot tell the retrievers apart on success. They are too easy for this solver; pass^k is at ceiling.
- The agent usually made one `retrieve` call and then searched and read. A retrieve payload stays in context for every later turn. graf filled the 2K budget and Graphify returned slightly less, which matches the held-out retrieval finding that graf delivers more tokens than Graphify.
- No efficiency claim for graf follows from this batch.

### Invalid earlier batch (exploratory only)

An earlier 200-run batch is excluded. `target/` was deleted mid-batch by a process outside this project. The broker then passed the missing-binary error to the agent as an ordinary tool error, so 88 of 100 graf-arm runs used only regex search and reads. That accidental search-and-read arm cost $0.222 against $0.277 for Graphify, with 97 against 98 successes. It was not preregistered, and it ran at a different time, so it is a hypothesis only: on these tasks, a retriever's payload may cost more than it saves. The raw runs and a note are in `.research/trials/invalid-batch-2026-10-20/`. The harness now refuses to start without a hashed backend and stops on infrastructure errors.

## Next steps

Batch `t3` answered the open question from `t2`, within its conditions: on natural tasks, no retriever beat search and reads, and graf did not beat Graphify. More agent trials with this solver are unlikely to change that unless the tasks need more navigation, for example larger repositories or issues that do not name the code. For retrieval, any floor-stop variant must still pass the recall guardrails on a new held-out query set.
