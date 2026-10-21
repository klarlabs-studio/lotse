# Offline localization screen

**Outcome: `ast_graph` qualifies for an agent batch; nothing is promoted.** The other three candidates fail. AST function units with one-hop call-graph propagation put a function the real fix changed among their first five cited units for 36.5% of 395 held-out issues. That beats `graf_floor` (27.6%) and Graphify (29.1%), with significant paired tests against both. This is a screen, with no solver and no cost measurement, so it supports no efficiency claim. Its job is to decide which design earns an agent trial.

- **Rule:** `localization_screen` in [preregistration v15–v17](../benchmark/preregistration.json).
- **Data:** [localization-decision-heldout.json](../results/localization-decision-heldout.json) (decision and summaries) and [localization-heldout.json](../results/localization-heldout.json) (every task and arm).
- **Code:** [localization.py](../benchmark/localization.py).

## Conditions

- **Tasks.** The catalog has 415 tasks ([localization-tasks.json](../benchmark/localization-tasks.json), sha256 `04e422f9…`). Each one is a merged, issue-closing pull request in Cobra, Flask, or Fastify that modifies a non-test source file and stays within 10 files and 400 lines. The 18 dev tasks are the batch `t3` tasks and were used only for debugging. Of the 397 held-out tasks, 2 were excluded because their only changed file is under `.github/`, outside the corpus, leaving 395: Cobra 57, Flask 136, Fastify 202. There is no bug judgment: 87 issues carry a bug label, and the rest are features or other changes.
- **Corpus and query.** The corpus is the pre-fix snapshot (the merge's first parent), with the same file filter as all graf benchmarks. The query is the issue text (title and body, at most 4,000 characters).
- **Gold.** The gold lines are the pre-fix lines the fix replaced or deleted, plus the lines on either side of each insertion. A gold unit is the function, or 24-line module window, that contains a gold line. The fix location is one valid answer, not the only one.
- **Budget.** Every arm answers once, within 2,000 cl100k tokens.
- **Primary metric (unit Acc@5).** Cited spans are mapped to code units in citation order, and the metric is whether a gold unit is among the first five. Each candidate is tested against `graf_floor` and Graphify with an exact one-sided McNemar test. A candidate must beat both (intersection-union), with Holm correction across the four candidates at a family alpha of 0.05.
- **Arms.** Parameters are round defaults set before any task was scored.

  | Arm | Retrieval |
  | --- | --- |
  | `graf` | Current graf |
  | `graf_floor` | `--file-score damped --stop floor` |
  | `graf_floor_ids` | `graf_floor` with the issue reduced to its title plus code-like tokens (BLIZZARD-style rewrite) |
  | Graphify | Pinned, code-only graph hints |
  | `file_bm25` | Ranked files only |
  | `ast_units` | BM25 over tree-sitter function units, returning whole units |
  | `ast_graph` | `ast_units`, with each score raised by 0.5 × the best score among its call-graph neighbours |
  | `ast_bounded` | `ast_units` with long functions split into 24-line pieces |

## Results (395 held-out tasks)

| Arm | unit Acc@1 | **unit Acc@5** | unit Acc@10 | file Acc@1 | file Acc@5 | gold line shown | mean payload |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| graf | 0.114 | 0.284 | 0.365 | 0.195 | 0.451 | 0.205 | 1,985 |
| graf_floor | 0.122 | 0.276 | 0.377 | 0.210 | 0.453 | 0.175 | 1,356 |
| graf_floor_ids | 0.149 | 0.344 | 0.446 | 0.291 | 0.562 | 0.243 | 994 |
| Graphify (code-only hints) | 0.091 | 0.291 | 0.400 | 0.309 | **0.684** | — | 1,923 |
| file_bm25 (files only) | — | — | — | 0.129 | 0.362 | — | 176 |
| ast_units | 0.144 | 0.268 | 0.291 | 0.177 | 0.349 | 0.291 | 1,691 |
| **ast_graph** | **0.258** | **0.365** | 0.387 | **0.327** | 0.499 | **0.372** | 1,520 |
| ast_bounded | 0.152 | 0.306 | 0.342 | 0.185 | 0.413 | 0.319 | 1,834 |

Each arm's p is the larger of its two one-sided McNemar tests; wins are the discordant tasks that went to the candidate or to the comparator.

| Candidate | vs graf_floor (wins, p) | vs Graphify (wins, p) | p | Holm α | Result |
| --- | --- | --- | ---: | ---: | --- |
| ast_graph | 62 vs 27, p = 0.0001 | 84 vs 55, p = 0.009 | 0.009 | 0.0125 | **qualifies** |
| graf_floor_ids | 45 vs 18, p = 0.0005 | 84 vs 63, p = 0.049 | 0.049 | 0.0167 | fails (Graphify) |
| ast_bounded | 33 vs 21, p = 0.067 | 73 vs 67, p = 0.34 | 0.34 | 0.025 | fails |
| ast_units | 31 vs 34, p = 0.69 | 65 vs 74, p = 0.80 | 0.80 | 0.05 | fails |

**unit Acc@5 by repository:**

| | graf_floor | graf_floor_ids | Graphify | ast_units | ast_graph | ast_bounded |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Cobra (57) | 0.42 | **0.51** | 0.25 | 0.32 | 0.35 | 0.40 |
| Flask (136) | 0.43 | 0.47 | 0.29 | 0.48 | **0.59** | 0.48 |
| Fastify (202) | 0.13 | 0.21 | **0.31** | 0.11 | 0.22 | 0.16 |

## What this shows, within these conditions

- **Whole AST functions alone do not help.** `ast_units` ties `graf_floor` (31 vs 34 discordant). Splitting long functions (`ast_bounded`) recovers some ground. This matches the literature: unbounded function chunks are a weak representation.
- **The call graph is what helps.** Adding one-hop call propagation to the same units raises Acc@5 from 0.268 to 0.365 (p < 0.0001) and nearly doubles Acc@1 (0.144 to 0.258). It also cites fewer units (about 5, against about 46 for graf and Graphify) and shows a gold line verbatim in 37% of tasks, so it is the most "edit-ready" answer.
- **The query is the other lever.** Reducing the issue to code-like tokens improved graf on every metric and cut its payload from 1,356 to 994 tokens. It beat `graf_floor` significantly but fell short of the bar against Graphify.
- **Graphify is best at files.** Its file Acc@5 is 0.684, and it is best on Fastify. Its function-level order is traversal order, which explains its weak Acc@1.
- **Results depend on the repository.** On Fastify every arm is weak, and Graphify leads. On Flask, `ast_graph` leads clearly; on Cobra, graf with rewritten queries leads.
- **Bug-labelled issues (exploratory, not preregistered).** On the 87 issues carrying a bug label, `ast_graph` (0.21) does not beat Graphify (0.25). This overlaps with the repository effect.
- **Absolute rates are low.** Even the best arm misses the fix's functions in its first five units on about two-thirds of issues, so an agent will still have to search.

## Replication on six new repositories

**Outcome: both `ast_graph` and `graf_floor_ids` replicate.** On 1,106 issue-closing pull requests in gin, urfave/cli, click, jinja, express, and undici, each candidate beats both `graf_floor` and Graphify on unit Acc@5 after Holm correction. None of these repositories was used to develop or select an arm. The rule is `localization_screen.replication` in [preregistration v20](../benchmark/preregistration.json), frozen before the catalog was built. Data: [localization-decision-replication.json](../results/localization-decision-replication.json) and [localization-replication.json](../results/localization-replication.json).

- **Conditions.** These are the same arms, parameters, metric, and budget as the held-out screen. The catalog ([localization-replication-tasks.json](../benchmark/localization-replication-tasks.json), sha256 `58200858…`) holds 1,106 tasks: undici 485, click 240, urfave/cli 182, jinja 96, gin 87, express 16. No task was excluded.
- **Binary.** graf ran from a pinned copy (sha256 `e0ff0ae1…`), byte-identical to the audited baseline on 1,631 `locate`/`search` outputs.
- **Interruptions.** The run stopped twice and resumed without rescoring finished tasks:
  - urfave-cli-2229's issue text starts with `--verbose`, which graf parsed as an option. Queries are now passed after `--`, and output is unchanged otherwise.
  - The Claude Code session ended mid-run.

  No interim rates were looked at.

| Arm | unit Acc@1 | **unit Acc@5** | unit Acc@10 | file Acc@1 | file Acc@5 | gold line shown | mean payload |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| graf | 0.074 | 0.241 | 0.307 | 0.187 | 0.459 | 0.190 | 1,935 |
| graf_floor | 0.088 | 0.250 | 0.321 | 0.222 | 0.498 | 0.158 | 1,144 |
| graf_floor_ids | 0.137 | 0.289 | **0.379** | **0.286** | 0.578 | 0.176 | **961** |
| Graphify (code-only hints) | 0.076 | 0.257 | 0.333 | 0.229 | **0.627** | 0.001 | 2,207 |
| ast_units | 0.104 | 0.224 | 0.259 | 0.164 | 0.380 | 0.262 | 1,701 |
| **ast_graph** | **0.153** | **0.293** | 0.327 | 0.242 | 0.455 | **0.325** | 1,623 |
| ast_bounded | 0.112 | 0.245 | 0.311 | 0.181 | 0.417 | 0.289 | 1,791 |

| Candidate | vs graf_floor (wins, p) | vs Graphify (wins, p) | p | Holm α | Result |
| --- | --- | --- | ---: | ---: | --- |
| ast_graph | 124 vs 76, p = 0.0004 | 177 vs 137, p = 0.014 | 0.014 | 0.025 | **replicates** |
| graf_floor_ids | 94 vs 50, p = 0.0002 | 173 vs 137, p = 0.023 | 0.023 | 0.05 | **replicates** |

**unit Acc@5 by repository:**

| | graf_floor | graf_floor_ids | Graphify | ast_graph |
| --- | ---: | ---: | ---: | ---: |
| click (240) | 0.30 | 0.32 | 0.31 | **0.42** |
| express (16) | 0.56 | **0.69** | 0.38 | 0.56 |
| gin (87) | 0.30 | 0.33 | 0.26 | **0.36** |
| jinja (96) | 0.28 | 0.33 | 0.26 | 0.32 |
| undici (485) | 0.25 | **0.27** | 0.24 | 0.23 |
| urfave/cli (182) | 0.13 | 0.21 | 0.21 | **0.22** |

### What the replication adds

- **The call-graph effect holds out of sample.** `ast_graph` again beats whole units alone (0.293 vs 0.224) and nearly doubles graf_floor's Acc@1 (0.153 vs 0.088). Its margins are smaller than on Cobra, Flask, and Fastify: +4.3 points over `graf_floor` and +3.6 over Graphify here, against +8.9 and +7.4 before.
- **Query rewriting now also beats Graphify.** `graf_floor_ids` passed both tests. It has the best Acc@10 and file Acc@1, with the smallest payload of any graf arm (961 tokens).
- **The two candidates do not differ from each other** (p = 0.42 and 0.64, exploratory). The rewrite is a query-side change and the call graph is an index-side change, so combining them is the obvious next arm. That needs its own preregistered screen.
- **Graphify remains best at file recall** (file Acc@5 0.627), and on undici every arm is within 4 points.
- **All absolute rates are low:** the best arm misses the fix's units in its first five on about 70% of issues.

## Deviations and disclosures

- After the dev run (debugging only), v16 changed the primary metric from "any hit in the payload" to Acc@5. "Any hit" rewards arms that cite about 46 pointers. v16 also added `ast_bounded` and `graf_floor_ids`, motivated by the [literature review](code-representation-review.md). No held-out task had been scored at that point.
- The first held-out run stopped at task 281 on a gold file outside the corpus. I then ran the decision script on the 280 completed tasks and saw interim rates. After that, v17 added a path-only exclusion rule and made the decision script refuse partial splits. The arms, metric, and rule were not changed.
- A second run lost its results file when the disk filled. Results are now written atomically, and the full held-out split was rerun from scratch. The decision above uses that complete rerun.

## Next step

Run a preregistered agent batch with `ast_graph` as the candidate, against `search_only` and Graphify, on new natural tasks. Its value to an agent depends on whether a 37% function hit with code shown cuts turns enough to pay for its ~1.5K-token payload; batch `t3` showed that payload costs persist. Two further ideas are untested and would need a new held-out screen before any use:
- Combining the identifier rewrite with `ast_graph`.
- Using Graphify-style file signals for Fastify-like codebases.
