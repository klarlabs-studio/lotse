# Paired agent trials

Three preregistered batches with the same solver and spend cap. Batches `t` and `t2` use 20 synthetic fault-repair tasks; batch `t3` uses 18 natural tasks mined from real upstream fixes. The newest batch is reported first.

## Post-hoc diagnostic: where t8's and t9's cost gaps come from

Exploratory and not preregistered. It was run after both decisions were recorded, using `benchmark/agent_cost_diagnostic.py` on the preserved trial logs, with no model calls. Rows are in [agent-cost-diagnostic-t9.json](../results/agent-cost-diagnostic-t9.json) and [agent-cost-diagnostic-t8.json](../results/agent-cost-diagnostic-t8.json).

| Batch | Tasks every attempt of both arms solved | graf cost on those | Graphify cost on those | graf cost, all tasks | Graphify cost, all tasks |
| --- | ---: | ---: | ---: | ---: | ---: |
| t9 | 27 / 36 | $0.291 | $0.292 | $0.670 | $0.544 |
| t8 | 28 / 36 | $0.431 | $0.434 | $0.664 | $0.649 |

- **On routine tasks the orientations cost the same.** On the tasks both arms always solve, graf and Graphify are within 1%. The agent reads the changed file first in most attempts either way: 57 of 72 graf attempts against 61 of 72 with Graphify in t9, and 53 against 62 in t8. In t9 the issue text itself names a changed file (its basename or stem) on 24 of 36 tasks. Typical attempts have no slack left: the median attempt took 11 turns in both arms.
- **The gap comes from a few hard tasks.** In t9 one task, nodemon-1320, accounts for about two thirds of the $0.126 gap. graf's two attempts there took 28 and 45 turns ($0.042 and $0.112) and both passed. Graphify's took 20 and 36 turns and one passed. On drf-9707 graf passed both attempts at higher cost, while Graphify failed both more cheaply. The cost metric does not credit a costlier success over a cheaper failure, so solving more hard tasks shows up as extra cost.
- **graf's first file was less often the changed file.** This held in both batches, although graf's file ranking won the offline localization screens. In t9 the changed file was graf's top file on 16 of 36 tasks, lower in the list on 13 and absent on 7. Tasks where it was listed but not first had the largest mean gap: +$0.0037 per attempt against +$0.0009 when it was first.
- **graf attempts searched and retrieved more:** 209 against 184 searches and 49 against 13 retrievals in t9. With longer sessions, each turn re-reads a larger cached context: $0.00066 against $0.00058 per turn.

**Reading (hypothesis, untested).** A shorter or better-ranked orientation cannot win on cost here. On most tasks the issue text already leads the agent to the right file, and the remaining cost comes from long sessions on hard tasks. Agent savings would have to come from shortening those sessions (for example, fewer exploratory search and retrieval turns after a missed first file), not from the orientation payload. Any such change needs its own preregistered screen and agreed budget before a claim.

## Batch t9: screen-qualified file list with function pointers versus Graphify (Haiku, fresh tasks); fails on cost

**Outcome: the candidate `graf_pointer_orient` fails the preregistered rule.** It cost more than Graphify's orientation, not less: mean −$0.00175 per task (p 0.96), $0.670 in total against $0.544, about 23% more. Success was non-inferior: 62/72 against 59/72, p < 0.001. It also used more tokens: 15.22M against 13.07M (secondary, p 0.83). This is the third fresh-task batch in a row (t7, t8, t9) in which a lean graf orientation does not establish agent savings against Graphify. Every task and arm summary is in [agent-decision-t9.json](../results/agent-decision-t9.json).

### Conditions

- **Tasks.** 36 fresh natural tasks from seven repositories never used by any graf benchmark, frozen in v110 (catalog sha256 `2f0847b9…`) before any solver saw them:

  | Language | Tasks |
  |---|---|
  | Go | 10 (fasthttp 8, lo 2) |
  | Python | 16 (loguru 7, drf 5, pydantic 4) |
  | JavaScript | 10 (moment 8, nodemon 2) |

  - **Selection.** The v95 fill rule, as in t8. Every webpack candidate failed the fail-to-pass check by timing out under the frozen v109 runner. They were rejected as the rule requires; the runner was not changed after the fact.
- **How the candidate was chosen.** Two offline localization screens on held-out tasks, with no solver:
  - `file_code_screen` (v102) qualified the `file_graph_code` file ranking.
  - `file_pointer_screen` (v104) qualified `file_pointers`: that ranking plus 2, 1 and 1 function pointers (name and line range) under its first three files, with no code.
  - One `file_code_screen` log row (gorm-7790's file ranks for four arms, no unit result) was seen while checking the run's output path, before the pointer screen ran. This is recorded in its preregistration.
  - `file_code_screen` crashed once on poetry-3022 after the process was suspended, and resumed from its 814 completed rows.
- **Payload parity.** The shipped binary's payload was compared with the one the screen scored, before any t9 model call and without gold. It was identical on 1,449 of 1,459 screen tasks and on 30 of 36 t9 tasks. All the t9 differences are moment tasks: on five the eighth file differs, and on moment-6116 the binary splits a 4,500-line bundle into units differently. t9 tests the shipped binary as defined.
- **Design.** 2 attempts per task and arm, 144 attempts in a seeded random order (seed 20261028), budget agreed on 2026-10-27.
  - **Solver.** Claude Code 2.1.312 (pinned copy) running `claude-haiku-5-5` on the subscription. 80-turn cap, $0.50 per attempt, 1,200 s trial timeout, list-price cost. 2.1.312's Haiku 5.5 prices are identical to 2.1.311's.
  - **Verifier deadline and bounded prefix.** Both on, as in t8.
- **Arms.**
  - `graf_pointer_orient`: `graf triage --lean --text --budget 1500` from `.research/bin/graf-c3fee50` (sha256 `813a3307…`). The median orientation was 94 cl100k tokens.
  - `graphify_orient`: Graphify 0.9.97 orientation, a new baseline revision (the latest release at freeze, so the comparator differs from t8's 0.9.96), requested at 1,500 tokens and capped there. The median orientation was 1,490 tokens, cut to the cap on 30 of 36 tasks.
- **Harness changes since t8 (both arms alike).** Every verifier and broker-tool command now runs under `run_group`, which kills every descendant still running when the command ends (issue #1: Playwright's browser escaped the process group). Output goes to files instead of pipes. This adds about 0.1–0.3 s of wall time per tool call, which is not gated.
- **Completeness.** All 144 attempts are complete with full usage accounting. There were no infrastructure aborts and no reruns, and no attempt hit the solver timeout, so no cost was bounded.

### Results

| Arm | Passes / attempts | Success (Wilson 95%) | Total cost (list, USD) | Total tokens |
| --- | ---: | --- | ---: | ---: |
| `graf_pointer_orient` | 62 / 72 | 86.1% (76.3–92.3%) | 0.670 | 15.22M |
| Graphify 0.9.97 orientation | 59 / 72 | 81.9% (71.5–89.1%) | 0.544 | 13.07M |

| `graf_pointer_orient` versus | Mean cost reduction per task | Cost p (one-sided sign flip) | Cheaper tasks | Success difference (margin 0.10) | Pass |
| --- | ---: | ---: | ---: | --- | --- |
| Graphify orientation | −$0.00175 (costs more) | 0.96 | 16 / 36 | +0.042, non-inferior (p < 0.001) | no |

- **Cost per task.** graf cost more than 1.5× Graphify on 3 tasks, the worst being nodemon-1320 at about 2.2×. It was up to 43% cheaper on others (loguru-1502).
- **Success per task.** graf passed more attempts on 3 tasks (drf-9707 2 against 0, moment-6143, nodemon-1320) and fewer on 1 (loguru-1527).

**By language (exploratory, not preregistered):**

| Language | `graf_pointer_orient` passes, cost, tokens | Graphify passes, cost, tokens |
| --- | --- | --- |
| Go | 14/20, $0.155, 3.96M | 14/20, $0.134, 3.39M |
| Python | 28/32, $0.234, 4.80M | 27/32, $0.206, 4.45M |
| JavaScript | 20/20, $0.280, 6.46M | 18/20, $0.203, 5.23M |

graf cost more in all three languages, by the widest margin on JavaScript.

### Reading

- **Better localization did not become cheaper agent runs.** The pointer payload won its offline localization screen, but here graf cost more on every language. A shorter orientation with better-ranked files did not stop the agent from spending more turns and tokens than it did with Graphify's longer one.
- **Three batches, no cost advantage.** t7, t8 and t9 all fail on cost. No graf orientation is promoted, and agent savings against Graphify remain unestablished.

## Batch t8: replication of the lean file-list orientation versus Graphify (Haiku, fresh tasks); fails on cost

**Outcome: the candidate `graf_lean_orient` fails the preregistered rule again.** It was not cheaper than Graphify's orientation (mean −$0.00021 per task, p 0.66). Success was non-inferior: 61/72 against 62/72, p 0.005. t7's 32% token saving did not replicate: graf used 14.32M tokens against Graphify's 14.41M (secondary, p 0.47). No attempt hit the trial timeout, so no cost was bounded and the v93 rule never applied. On these tasks, a file-list orientation does not establish agent savings against Graphify. Every task and arm summary is in [agent-decision-t8.json](../results/agent-decision-t8.json).

### Conditions

- **Tasks.** 36 fresh natural tasks from nine repositories never used by any graf benchmark, frozen in v94–v101 before any solver saw them:

  | Language | Tasks |
  |---|---|
  | Go | 14 (zap 5, validator 7, mux 2) |
  | Python | 12 (requests 1, tqdm 1, uvicorn 2, pytest 8) |
  | JavaScript | 10 (handlebars 2, prettier 8) |

  - **Selection.** The t7 protocol, filled per language under the v95 rule: newest merge first, at most 8 per repository and 16 per language. Python and JavaScript ran out of candidates before 16.
  - **Yield.** 55 of the candidates were judged bugs from the issue text alone, and 44 passed the fail-to-pass check. The rejections are listed in the preregistration.
  - **Duplicate.** Two tqdm pull requests close the same issue; only the newer one was kept.
  - **Catalog.** sha256 `9a4f0b35…`.
- **Pre-run corrections (no solver had run):**
  - **pytest runtime (v99, v100).** pytest's runtime lacked the generated `_version.py`, so every pytest candidate failed on unmodified code. The v99 fix still failed, because the module was not bound to the package. The v100 fix serves it through the import system. Only pytest's candidates were re-checked.
  - **Minimum task count (v101).** It was missing and set to t7's 36 at the freeze, when the yield of 36 was already known.
  - **Payload prefix (v101).** A no-model dry run found Graphify's pytest orientation empty: its first line, a 7,078-character traversal header, alone exceeded the 1,500-token budget, and payloads keep only complete lines. From t8, such a first line keeps its longest prefix within the budget, for both arms. This can only add comparator context.
- **Design.** 2 attempts per task and arm, 144 attempts in a seeded random order (seed 20261027).
  - **Solver.** Claude Code 2.1.311 (pinned copy; it came out after v94 froze 2.1.310) running `claude-haiku-5-5` on the subscription. 80-turn cap, $0.50 per attempt, 1,200 s trial timeout, list-price cost.
  - **Prices.** 2.1.311's built-in Haiku 5.5 prices are identical to 2.1.310's.
  - **Verifier deadline.** On (v94): the broker fits verifier runs inside the trial timeout.
- **Arms.**
  - `graf_lean_orient`: as in t7, the file list from `graf triage --budget 1500` (binary `graf-a9c9e4d`) and nothing else.
  - `graphify_orient`: Graphify 0.9.96 orientation, a new baseline revision with its own virtualenv and snapshot, capped at 1,500 tokens.
- **Completeness.** All 144 attempts are complete. Three runs were infrastructure aborts, set aside and rerun from scratch under the completeness rule:
  - one Graphify index rebuild on prettier exceeded its 180 s limit under host load;
  - two pytest runs, one per arm, were interrupted when the disk filled. The batch stopped after 61 attempts and was resumed once space was freed.
- **Host.** Another project's jobs kept the load high (load average up to about 60) for part of the run.

### Results

| Arm | Passes / attempts | Success (Wilson 95%) | Total cost (list, USD) | Total tokens |
| --- | ---: | --- | ---: | ---: |
| `graf_lean_orient` | 61 / 72 | 84.7% (74.7–91.2%) | 0.664 | 14.32M |
| Graphify 0.9.96 orientation | 62 / 72 | 86.1% (76.3–92.3%) | 0.649 | 14.41M |

| `graf_lean_orient` versus | Mean cost reduction per task | Cost p (one-sided sign flip) | Cheaper tasks | Success difference (margin 0.10) | Pass |
| --- | ---: | ---: | ---: | --- | --- |
| Graphify orientation | −$0.00021 (costs more) | 0.66 | 20 / 36 | −0.014, non-inferior (p 0.005) | no |

**By language (exploratory, not preregistered):**

| Language | `graf_lean_orient` passes, cost, tokens | Graphify passes, cost, tokens |
| --- | --- | --- |
| Go | 23/28, $0.223, 4.71M | 23/28, $0.226, 5.29M |
| Python | 20/24, $0.229, 4.85M | 20/24, $0.234, 5.44M |
| JavaScript | 18/20, $0.212, 4.77M | 19/20, $0.189, 3.68M |

graf used about 11% fewer tokens on Go and Python. On JavaScript it used 30% more, which offsets that saving; the extra came mainly from prettier tasks.

### Reading

- **Two batches, no cost advantage.** t7 and t8 both fail on cost, and t8 does not reproduce t7's token saving, so that saving was not robust. At Haiku 5.5 prices most tokens are cache reads, so the orientation's size barely moves cost.
- **Orientation length alone is unlikely to win.** A file list of a few dozen tokens cost about the same as Graphify's 1,500-token orientation.
- **Next.** A better file list (a development screen on branch `feat/triage-file-recall` raised triage's top-5 file accuracy from 0.584 to 0.731 on the spent localization catalogs) needs its own held-out screen and agent batch before any claim.

## Batch t7: lean file-list orientation versus Graphify (Haiku, fresh tasks); fails on cost

**Outcome: the candidate `graf_lean_orient` fails the preregistered rule.** Under the v93 accounting it was not significantly cheaper than Graphify's orientation (mean −$0.00089 per task, p 0.50). Success was non-inferior: 65/92 against 63/92, p < 0.001. It used 32% fewer tokens (secondary, p < 0.001), but cache reads make up most of that saving, and at Haiku 5.5's cache-read price ($0.01 per million) they cost almost nothing. The failure turns on one bounded attempt (see "Sensitivity" below), so this is a close fail, not evidence of no effect. It does not establish agent savings against Graphify. Every task and arm summary is in [agent-decision-t7.json](../results/agent-decision-t7.json).

### Conditions

- **Tasks.** 46 fresh natural tasks mined after the design was frozen (v87–v91), none of them used in any earlier batch: fiber 8, testify 6, starlette 8, black 8, eslint 8 and dayjs 8. Selection followed the t4 protocol: issue-only bug judgment, then a fail-to-pass check (base passes, fault fails without timing out, restored passes). Rejections are disclosed in the preregistration. Catalog sha256 `8e1d37a7…`.
- **Design.** 2 attempts per task and arm, 184 attempts in a seeded random order (seed 20261026). The solver was Claude Code 2.1.310 (pinned copy) running `claude-haiku-5-5` on the subscription, with an 80-turn cap, a $0.50 cap per attempt, a 1,200 s trial timeout and list-price cost.
- **Arms.**
  - `graf_lean_orient` gives only the file list from `graf triage --budget 1500` (binary `graf-a9c9e4d`, sha256 `9d54581b…`) as "Likely files", with no code excerpts. That is 34–83 tokens in a dry run.
  - `graphify_orient` is unchanged: Graphify 0.9.95 orientation capped at 1,500 tokens.
  - Graphify is the only comparator, by the operator's choice for this batch.
- **Completeness.** All 184 attempts are complete.
  - **Rule change, v93.** The batch stopped after 108 runs because three runs of `starlette-1102-graphify_orient-1` were classed as infrastructure aborts. The agent's edits had made starlette's streaming tests hang, so each broker test call ran to the verifier's 180 s limit, and the trial timeout fell during one of them. The broker answered every call, so these were the solver's own timeouts.
  - **What v93 does.** It scores such timeouts by the verifier, the same way for both arms. It was recorded before the remaining 76 runs, but it is a rule change after data had been seen. While diagnosing, it was seen that those Graphify attempts failed.
  - **Which runs count.** The first run of each attempt is the trial of record. The later reruns are kept in `.research/trials-aside`.
  - **Bounded cost.** These timeouts end without Claude Code's usage report, so their cost is only bounded: between the exactly streamed input and cache cost and the $0.50 cap. The decision counts Graphify's at the lower bound and graf's at the upper bound. Three attempts are bounded this way, all on starlette-1102: both Graphify attempts and graf's attempt 2.
- **Host.** Tool-call timeouts occurred in 4 attempts. Load was unusually high for part of the run (load average ~70, from another project's job).

### Results

| Arm | Passes / attempts | Success (Wilson 95%) | Total cost (list, USD) | Total tokens |
| --- | ---: | --- | ---: | ---: |
| `graf_lean_orient` | 65 / 92 | 70.7% (60.7–79.0%) | 1.271 | 18.6M |
| Graphify 0.9.95 orientation | 63 / 92 | 68.5% (58.4–77.1%) | 1.189 | 27.3M |

Total cost counts the bounded timeouts as the decision does: graf's at $0.50, and Graphify's at $0.127 and $0.058.

| `graf_lean_orient` versus | Mean cost reduction per task | Cost p (one-sided sign flip) | Cheaper tasks | Success difference (margin 0.10) | Pass |
| --- | ---: | ---: | ---: | --- | --- |
| Graphify orientation | −$0.00089 (costs more) | 0.50 | 29 / 46 | +0.022, non-inferior (p < 0.001) | no |

- **Per task.**
  - **Cost:** `graf_lean_orient` was at least a third cheaper than Graphify on 9 tasks, with eslint-21421 the cheapest at 0.27×. It was more than 1.5× dearer only on starlette-1102 (2.7×, the bounded attempt), and at most 1.36× dearer on all other tasks.
  - **Success:** it passed fewer attempts on 3 tasks and more on 6.
- **Per repository.** Summed cost was lower for graf in every repository except starlette. The largest difference was eslint, at $0.179 against $0.311. Starlette was $0.565 against $0.249, and that gap comes almost entirely from starlette-1102.
- **Where the token saving goes.** Across complete runs, the two arms used:

  | Token type | graf | Graphify | Price (USD per million) |
  |---|---|---|---|
  | Cache reads | 14.4M | 21.2M | 0.01 |
  | Cache writes | 1.9M | 2.2M | 0.20 |
  | Output | 0.49M | 0.57M | 0.50 |

  So most of the token reduction is in the cheapest kind of token.

### Sensitivity (exploratory, not preregistered)

These checks were computed after the decision, and they cannot turn the fail into a pass.

- With every bounded timeout at its lower bound, graf's mean saving is +$0.0033 per task (p 0.003).
- Without starlette-1102, it is +$0.0027 (p 0.006).

Graf's starlette-1102 attempt 2 cost between $0.115 and $0.50, and the preregistered rule counts it at $0.50. Its true cost is unknown, because thinking tokens are not in the stream. The result is therefore fragile in both directions: the candidate failed because of how one timeout is costed, and a repeat on fresh tasks would be needed before any claim.

### What this means for the goal against Graphify

- A file list without excerpts is the first graf orientation to show a large, consistent token reduction against Graphify at non-inferior success.
- On Haiku 5.5 the dollar saving is small, because the saving falls on cheap cache reads. Here the saving is also not significant under the conservative accounting.
- A next step that would test the claim properly:
  - repeat this arm on fresh tasks with a trial timeout that cannot fall inside a verifier call (or a verifier limit shorter than the remaining trial time);
  - or use a model whose cache reads are priced closer to input, where the token reduction would matter more in dollars.

  Either needs a new budget agreement.

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
