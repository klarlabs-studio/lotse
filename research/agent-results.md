# Paired agent trials

Three preregistered batches with the same solver and spend cap. Batches `t` and `t2` use 20 synthetic fault-repair tasks; batch `t3` uses 18 natural tasks mined from real upstream fixes. The newest batch is reported first.

## Post-hoc diagnostic: why t5's cost gain over Graphify did not repeat in t6

Exploratory, not preregistered, and computed after both batches finished. It cannot support or reject any claim; it only guides the next design. Both batches ran the same 48 tasks, with 2 attempts per arm and Haiku. Infrastructure-aborted attempts are excluded.

- **The graf arm did not get worse; the Graphify arm got cheaper.** Summed per-task mean cost was $0.456 for graf in t5 and $0.431 in t6. For Graphify orientation it was $0.572 and $0.470; for search_only, $0.413 and $0.372. The t6 − t5 difference on the same task has a standard deviation of about $0.012 in every arm, with means between −$0.0005 and −$0.0021, so the binary change is not detectable at this size. t5's p 0.020 rested partly on an expensive Graphify draw.
- **Power.** The paired graf-vs-Graphify saving per task was $0.0024 (sd 0.011) in t5, $0.0008 (sd 0.011) in t6, and $0.0016 pooled. A one-sided 0.05 test at 80% power on the pooled effect would need about 300 tasks at 2 attempts. Against search_only the mean saving is negative in both batches.
- **Orientation recall.** graf's "Likely files" list contained a fixed file for 62% of tasks (t5) and 69% (t6); 52% and 60% within the first five. Only 35% of its code excerpts came from a fixed file in either batch. Graphify's BFS dump named a fixed file's full path somewhere for 94%, but it lists many more files, so this is not a matched recall metric.
- **Where cost diverges.** Grouping tasks by whether graf's list contained a fixed file (pooled, attempts per arm in parentheses):

  | Group | Arm | Turns | Tokens | Pass rate |
  |---|---|---|---|---|
  | hit (126) | graf | 10.0 | 102k | 0.65 |
  | hit (126) | Graphify | 10.4 | 112k | 0.65 |
  | hit (126) | search_only | 11.2 | 98k | 0.66 |
  | miss (66) | graf | 19.0 | 463k | 0.76 |
  | miss (66) | Graphify | 19.3 | 481k | 0.70 |
  | miss (66) | search_only | 18.3 | 358k | 0.67 |

  On tasks graf orients well, all three arms cost about the same, so a good orientation leaves little to save. On the tasks it misses (the harder ones), graf takes as many turns as search_only but carries about 30% more tokens, more than the 1,500-token orientation alone explains, so its tool payloads likely add context on every turn. The grouping is confounded with task difficulty.

Implications for a next candidate (to be preregistered on fresh tasks before any run): make the orientation cheaper rather than larger (for example a file list without excerpts), cut graf tool payloads on long runs, and improve file recall (the TypeScript screen's file_graph_ids ranking is the strongest retrieval-only result so far). A screen sized like t5/t6 cannot detect effects of this size; the next screen needs either many more tasks or a larger expected effect.

## Batch t6: current triage binary screen (Haiku); no significant difference

**Outcome: the candidate `graf_current_orient` fails the screen.** It was not significantly cheaper than Graphify's orientation (p 0.48) or `search_only` (p 0.98; on average it cost more). Success was non-inferior against both. The current binary does not repeat t5's cost win over Graphify: the t5 binary (`graf-t5`) was significantly cheaper than Graphify, while the current one (`graf-t6`) is not. Every task and arm summary is in [agent-decision-t6.json](../results/agent-decision-t6.json).

### Conditions

- **Tasks and design.** As in t5: the 48 natural t4 tasks, 2 attempts per task and arm, 288 attempts in a seeded random order (seed 20261024). It also uses the same solver: Claude Code 2.1.310 (pinned copy), `claude-haiku-5-5` on the subscription, an 80-turn cap, a 1,200 s timeout and list-price cost.
- **Arms.** `graf_current_orient` uses the t5 `graf_orient` protocol with the separately pinned current binary `.research/bin/graf-t6`, sha256 `f827c273…` (HEAD a9c9e4d plus audit repairs; sources in `.research/evidence/agent-t6/source`). `graphify_orient` and `search_only` are rerun in this batch, as in t5.
- **Completeness.** All 288 planned attempts are complete, with full usage accounting.
  - **One infrastructure abort was set aside and rerun.** Graphify's orientation index build hit its 180 s timeout before any model call, under a host load average of about 110–130 (v86).
  - **One summary was rebuilt from preserved artifacts without a rerun (v85):** `t6-undici-5684-graf_current_orient-2`. Its first finish ran the hidden tests, which timed out, so the verifier recorded a failure. A filter bug had dropped that verdict and stopped the batch. That attempt's outcome was seen while diagnosing. The timeout may reflect host load rather than the agent's change; it is counted as the verifier recorded it.
  - **One further counted attempt in each of `graf_current_orient` and `graphify_orient`** had a tool call time out during the run.

### Results

| Arm | Passes / attempts | Success (Wilson 95%) | Total cost (list, USD) | Total tokens |
| --- | ---: | --- | ---: | ---: |
| `graf_current_orient` | 64 / 96 | 66.7% (56.8–75.3%) | 0.862 | 21.0M |
| Graphify 0.9.95 orientation | 66 / 96 | 68.8% (58.9–77.1%) | 0.940 | 21.9M |
| `search_only` | 63 / 96 | 65.6% (55.7–74.4%) | 0.744 | 17.6M |

| `graf_current_orient` versus | Mean cost reduction per task | Cost p (one-sided sign flip) | Cheaper tasks | Success difference (margin 0.10) | Pass |
| --- | ---: | ---: | ---: | --- | --- |
| Graphify orientation | $0.00082 | 0.48 | 22 / 48 | −0.021, non-inferior (p < 0.001) | no |
| `search_only` | −$0.00123 (costs more) | 0.98 | 18 / 48 | +0.010, non-inferior (p < 0.001) | no |

- **Against Graphify:**
  - **Cost:** `graf_current_orient` cost more than 50% above Graphify on 3 tasks, the worst being gin-4033 at about 2.6×. It was up to 65% cheaper on others (urfave-cli-2248).
  - **Success:** it passed fewer attempts on 3 tasks and more on 1.
- **Against `search_only`:**
  - **Cost:** it cost more than 50% above `search_only` on 10 tasks, the worst being urfave-cli-2173 at about 3.3×.
  - **Success:** it passed fewer attempts on 3 tasks and more on 4.
- **Tokens (secondary, descriptive):** 9k fewer per task than Graphify (p 0.43) and 36k more than `search_only` (p 0.95).
- **What the three screens show together:** across t4d, t5 and t6, no graf candidate beat both comparators. t5's cost win over Graphify was not repeated by the current binary. No candidate is promoted to the stronger-model confirmation, and agent savings against Graphify remain unestablished.

## Batch t5: triage orientation screen (Haiku); beats Graphify, not search_only

**Outcome: the candidate `graf_orient` fails the screen**, because the rule requires a win against both comparators (intersection-union).
- **Against Graphify orientation:** `graf_orient` passes both tests. It is significantly cheaper (p 0.020, alpha 0.05) and its success is non-inferior.
- **Against `search_only`:** it is not cheaper; on average it costs slightly more (p 0.61).

This is a screen (`agent_trials.staging`, v82/v83), so it cannot support an agent-efficiency claim either way. Every task and arm summary is in [agent-decision-t5.json](../results/agent-decision-t5.json).

### Conditions

- **Tasks.** The 48 natural t4 tasks ([natural-tasks-t4.json](../benchmark/natural-tasks-t4.json)), 2 attempts per task and arm, 288 attempts in a seeded random order.
- **Arms.**
  - `graf_orient` (candidate): `graf triage --budget 1500 --text` on the issue, appended to the prompt, plus the `retrieve` and `outline` tools. Binary `.research/bin/graf-t5`, sha256 `c95b5766…`.
  - `graphify_orient`: the pinned Graphify 0.9.95 query at 1,500 tokens, appended the same way, plus Graphify `retrieve`.
  - `search_only`: no orientation and no `retrieve` tool.
  - All arms also get read, search, edit, test and finish.
- **Solver.** Claude Code 2.1.310 (pinned copy) with `claude-haiku-5-5` on the operator's subscription, as switched in v83 before any t5 attempt ran. 80-turn cap and 1,200 s timeout. Cost is Claude Code's list-price estimate (`costUSD`).
- **Completeness.** All 288 planned attempts are complete, with full usage accounting, and every one ended with a result event; there were no turn-cap or timeout endings.
  - One infrastructure abort was set aside and rerun: a broker search hung while the disk was full, visible only as heartbeats (v84).
  - No tool call in a counted attempt timed out, despite heavy unrelated load on the host during part of the batch (v86).

### Results

| Arm | Passes / attempts | Success (Wilson 95%) | Total cost (list, USD) | Total tokens |
| --- | ---: | --- | ---: | ---: |
| `graf_orient` | 68 / 96 | 70.8% (61.1–79.0%) | 0.91 | 22.4M |
| Graphify 0.9.95 orientation | 62 / 96 | 64.6% (54.6–73.4%) | 1.14 | 24.0M |
| `search_only` | 64 / 96 | 66.7% (56.8–75.3%) | 0.83 | 18.3M |

| `graf_orient` versus | Mean cost reduction per task | Cost p (one-sided sign flip) | Cheaper tasks | Success difference (margin 0.10) | Pass |
| --- | ---: | ---: | ---: | --- | --- |
| Graphify orientation | $0.00240 | **0.020** | 28 / 48 | +0.063, non-inferior (p < 0.0001) | **yes** |
| `search_only` | −$0.00090 (costs more) | 0.61 | 18 / 48 | +0.042, non-inferior (p < 0.0001) | no |

- **Against Graphify, no task got worse.**
  - **Cost:** `graf_orient` was never more than 50% more expensive; the worst case was click-1884 at +48%.
  - **Success:** it passed fewer attempts on no task and more on 5.
- **Against `search_only`, results are mixed.**
  - **Cost:** `graf_orient` cost more than 50% above `search_only` on 3 tasks; the worst was urfave-cli-2248, at about 5.9×. It cost up to 58% less on others; the best was urfave-cli-2173.
  - **Success:** it passed fewer attempts on 3 tasks and more on 6.
- **Tokens (secondary, descriptive):**
  - Against Graphify: 16k fewer per task (p 0.26).
  - Against `search_only`: 43k more (p 0.67).
- **What this says:** triage's 1,500-token orientation is a cheaper and no-worse starting point than Graphify's, but an agent with search alone was still as cheap. Success under Haiku is about 65–71% in every arm, far below the DeepSeek t4d batch (96–98%) on the same tasks. The two screens differ in solver, arms and price basis, and are not compared with each other.

## Batch t4d: ast_graph screen (DeepSeek); no significant cost gain

**Outcome: the candidate `ast_graph` fails the screen.** On average it was cheaper than both comparators, but neither difference was significant. Success non-inferiority passed against both. This is a screen (`agent_trials.staging`, preregistration v82/v83), so a pass could only have nominated `ast_graph` for the stronger-model confirmation. A failure ends that path for this candidate as tested. Every task and arm summary is in [agent-decision-t4d.json](../results/agent-decision-t4d.json).

### Conditions

- **Tasks.** The 48 natural tasks in [natural-tasks-t4.json](../benchmark/natural-tasks-t4.json), from gin, urfave-cli, click, jinja, express and undici. The decision file's generic `scope` line about synthetic tasks does not apply to this catalog.
- **Arms.** `ast_graph` (candidate) is the localization-screen prototype: tree-sitter units, BM25 and one-hop call propagation (backend sha256 `fc85e659…`). The comparators are `search_only` (ripgrep 15.2.0, `1c579059…`) and code-only Graphify 0.9.95 (commit `92741d16…`). The test is intersection-union, at alpha 0.05 each.
- **Solver.** Claude Code 2.1.310, run from the pinned copy `.research/bin/claude-2.1.310`, with `deepseek/deepseek-v4.1-flash` through OpenRouter, an 80-turn cap and a 1200 s wall timeout. Cost is tokens priced with the frozen OpenRouter price table (`solver_prices_per_million`). The price table is DeepSeek's, so cost figures are not comparable with the Claude list-price batches.
- **Design.** 2 attempts per task and arm, 288 planned attempts in a seeded random order. t4d was planned with 5 attempts and cut to 2 in v82 for cost, without looking at outcomes. The 248 attempts 3–5 that had already run are kept and excluded.
- **Completeness.** All 288 planned attempts are complete, with full usage accounting. 280 ended normally and 8 hit the turn cap; the turn-capped attempts are counted, with their cost, as failures if unverified. Two infrastructure aborts were set aside in `.research/trials-aside/` and rerun from scratch: a lost broker reply, and an OpenRouter 402 before any model call. One attempt interrupted by an operator pause was also set aside and rerun.

### Results

| Arm | Passes / attempts | Success (Wilson 95%) | Total cost (price table, USD) | Total tokens |
| --- | ---: | --- | ---: | ---: |
| `ast_graph` | 94 / 96 | 97.9% (92.7–99.4%) | 2.794 | 85.7M |
| `search_only` | 92 / 96 | 95.8% (89.8–98.4%) | 3.080 | 100.3M |
| Graphify 0.9.95 | 93 / 96 | 96.9% (91.2–98.9%) | 3.127 | 98.8M |

| `ast_graph` versus | Mean cost reduction per task | Cost p (one-sided sign flip) | Cheaper tasks | Success difference (margin 0.10) | Pass |
| --- | ---: | ---: | ---: | --- | --- |
| `search_only` | $0.00298 | 0.135 | 24 / 48 | +0.021, non-inferior (p < 0.0001) | no |
| Graphify | $0.00347 | 0.133 | 25 / 48 | +0.010, non-inferior (p < 0.0001) | no |

The secondary token reductions are descriptive and not part of the rule. They are 152k tokens per task against `search_only` (p 0.042) and 137k against Graphify (p 0.119).

Per-task results are mixed, and the mean hides losses:
- **Cost.** `ast_graph` cost more than 50% above `search_only` on 8 tasks and above Graphify on 11; the worst was jinja-1573, at about 3.8× either comparator. It cost up to 81% less on others (click-3571 against `search_only`, jinja-2027 against Graphify).
- **Success.** `ast_graph` passed fewer attempts than both comparators on urfave-cli-2129. It passed more than `search_only` on 3 tasks and more than Graphify on 2.

### Disclosures

- **Superseded batches.** These are t4 (Claude Code 2.1.303), t4r (2.1.307), t4r2 (2.1.310 on the subscription, stopped over the plan budget), t4r3 (Haiku via OpenRouter), t4q and t4q2 (qwen3.7-flash, unmeasured timeouts). Their trials are kept and excluded, and each restart is recorded in the preregistration before new data.
- **Interim outcomes seen before completion.** Some interim outcomes were seen before t4d completed and are recorded in the preregistration: one t4d verification result during the diagnosis of a lost broker reply (that attempt was excluded and rerun), plus earlier qwen-batch log lines. No rule changed after any of them.
- **Scope.** The solver is a cheap non-Anthropic model behind a gateway. The result says nothing about Claude-model agents, and it supports no claim that graf saves agent cost against Graphify.

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

- On natural tasks with this solver, neither retriever reduced cost compared with search and reads alone, and they did not differ from each other. graf_floor's smaller payload did not turn into savings: Graphify was cheaper on only 5 tasks, but by larger amounts, and most of the gap came from the two hardest (`cobra-1257`, `fastify-4327`).
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
