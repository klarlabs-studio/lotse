# Paired agent trials

Three preregistered batches with the same solver and spend cap. Batches `t` and `t2` use 20 synthetic fault-repair tasks; batch `t3` uses 18 natural tasks mined from real upstream fixes. The newest batch is reported first.

## Offline screen: triage misses on the t10/t11 tasks (v126); 15 listed files pass, development only

**Outcome: listing 15 likely files instead of 8 passes the preregistered screen.** It is a development result on tasks already used, not evidence of agent savings. The screen replays the first `triage` call of each of the 190 `graf_installed` attempts in t10 and t11, with the agent's own query and budget, in the task's prepared workspace. A hit means triage names a faulted file. No model was called. The script is `benchmark/triage_replay.py`.

| Configuration | Hits (of 190) | File MRR | Median tokens | Verbatim issue hits (of 95) |
|---|---|---|---|---|
| graf-t10, 8 files (baseline) | 136 | 0.473 | 1,326 | 61 |
| path weight 3 (index flag) | 136 (2 gained, 2 lost) | 0.493 | 1,327 | 62 |
| **15 files** (`.research/bin/graf-triage15`) | **157** (21 gained, 0 lost; McNemar p = 5e-7) | 0.483 | 1,367 | 68 |
| Graphify 0.9.97 `graphify query`, same text | 119 | n/a | 1,773 | n/a |

- **Why 15.** A diagnostic build that listed every ranked file showed that in 21 of the 55 calls without a faulted file in the list, one ranked 9th to 15th. Removing triage's score floor did not help (133). The other 34 misses rank lower or are not ranked at all: the path, not the code, identifies the file (faker locale providers, highlightjs language files, stylelint rule folders), or the fix touches a changeset or a file no query term reaches. Ranking those is still open.
- **Bias.** The list length was chosen after seeing ranks on these same 190 calls, and a longer list raises recall almost by construction. The passed rule (v126: +10 points, McNemar p < 0.05, tokens within 10%, at most 5% of hits lost) guards tokens and losses, not that choice.
- **What it does not show.** That agents read fewer files or spend less. A longer list can also send agents to more files. Only a batch on fresh tasks, frozen with an agreed budget, can show that; until then the change stays experimental.
- **Results.** [triage-replay-baseline.json](../results/triage-replay-baseline.json), [triage-replay-pathweight3.json](../results/triage-replay-pathweight3.json), [triage-replay-triage15.json](../results/triage-replay-triage15.json); diagnostic builds' full rankings in [triage-replay-diag-floor.json](../results/triage-replay-diag-floor.json) and [triage-replay-diag-full.json](../results/triage-replay-diag-full.json) (scratch builds, not candidates).

## Batch t11 (v125): graf or Graphify installed, 47 pointer-free natural tasks; fails

**Outcome: `graf_installed` fails the preregistered rule.** It is not significantly cheaper than either comparator:
- against `builtin`: it cost more on average, $0.0017 per task (one-sided paired sign flip p = 0.743);
- against `graphify_installed`: $0.0005 cheaper per task, p = 0.475.

Success is non-inferior against both (margin 0.10; p = 0.022 and 0.013). Both comparisons had to pass, so graf stays experimental. Every attempt completed: 282 of 282, with usage complete, no infrastructure errors, no reruns and no protected-file edits. The decision is in [agent-decision-t11.json](../results/agent-decision-t11.json).

| Arm | Passed | Total cost | Median per attempt | Tokens |
|---|---|---|---|---|
| `builtin` | 76 / 94 | $1.294 | $0.0107 | 33.7 M |
| `graphify_installed` | 74 / 94 | $1.499 | $0.0147 | 43.2 M |
| `graf_installed` | 73 / 94 | $1.451 | $0.0113 | 37.6 M |

- **Workload.** The 47 tasks of the v124 catalog: 16 Go, 15 Python (the pool ran out), 16 JavaScript, from 15 repositories graf was never tuned on. Every issue is pointer-free under v120: it names no faulted file and shares no code-shaped identifier with the fault. node-red was excluded before any of its outcomes (v123): its source lies under `node_modules`, which the harness and both indexers skip.
- **Setup.** As t10, except Claude Code 2.1.314 (the latest on 2026-10-30). Graphify 0.9.97; graf `.research/bin/graf-t10` (main `7a3b88a`, unchanged since). `claude-haiku-5-5`, $0.50 and 1,200 s per attempt, two attempts per task and arm.
- **Against `builtin`:** graf was cheaper on 19 of 47 tasks. The largest gap is `mongoose-11913`, where graf spent $0.236 over two attempts (one pass) against `builtin`'s $0.074 (none). Tokens: graf used 41,000 more per task (p = 0.819, secondary, not gated).
- **Against `graphify_installed`:** graf was cheaper on 35 of 47 tasks and used 59,000 fewer tokens per task (p = 0.088, secondary, not gated), but the cost difference is not significant.
- **Every agent used the installed tool.** Every graf attempt called `triage` (94), and graf attempts also called `outline` 68, `units` 49 and `search` 48 times. They still made 362 `Read` and 313 `Grep` calls, against 434 and 481 under `builtin`. Every Graphify attempt ran `graphify` through Bash (542 calls). `builtin` attempts tried Bash 19 times; it is not offered to that arm.
- **Isolation.** Two `graphify_installed` attempts read Claude Code's saved oversized outputs outside the workspace (`lazygit-3721` attempt 2, 4 answered; `mongoose-16072` attempt 2, 3 answered). Under the isolation rule both fail; both are counted as failures above. All other outside requests were refused (7 `builtin`, 4 `graphify_installed`, 5 `graf_installed`).

### Post-hoc diagnostics (exploratory, no model calls)

- **Pointer-free issues did not make locating the fault hard.** Under `builtin` the agent read a faulted file after a median of 2 tool calls, the same as Graphify (3) and graf (2) ([t11-localization-diagnostic.json](../results/t11-localization-diagnostic.json), `benchmark/t11_localization_diagnostic.py`). Removing literal pointers left the conceptual ones, as v120's limit warned.
- **graf's triage named a faulted file in 59 of 94 attempts.** When it did, the attempt cost $0.0144 on average and passed 52 times, and the agent read a faulted file after a median of 1 call. When it did not (35), the attempt cost $0.0171, passed 21 times, and took a median of 5 calls to reach the faulted file.
- **Search and read payloads are 15-16% of cost in every arm** ([agent-headroom-diagnostic.json](../results/agent-headroom-diagnostic.json)): `builtin` 16.0%, Graphify 16.2%, graf 15.4%. Output (29-35%) and conversation history (29-32%) dominate. Even a retrieval tool that removed search payloads entirely could not save more than about a sixth of cost on this workload, which is less than the variance between attempts.

### Per task

Passes out of 2 and mean cost per attempt.

| Task | builtin | graphify_installed | graf_installed |
|---|---|---|---|
| `ansible-80128` | 2/2, $0.0060 | 2/2, $0.0090 | 2/2, $0.0071 |
| `caddy-5836` | 2/2, $0.0083 | 2/2, $0.0098 | 2/2, $0.0077 |
| `caddy-6392` | 1/2, $0.0135 | 1/2, $0.0127 | 0/2, $0.0095 |
| `caddy-7396` | 2/2, $0.0287 | 2/2, $0.0214 | 2/2, $0.0210 |
| `caddy-7596` | 2/2, $0.0099 | 2/2, $0.0093 | 2/2, $0.0091 |
| `caddy-7724` | 2/2, $0.0069 | 2/2, $0.0073 | 2/2, $0.0062 |
| `caddy-7741` | 2/2, $0.0121 | 2/2, $0.0174 | 2/2, $0.0109 |
| `caddy-8127` | 2/2, $0.0082 | 2/2, $0.0093 | 2/2, $0.0066 |
| `excelize-1414` | 2/2, $0.0156 | 2/2, $0.0232 | 2/2, $0.0181 |
| `excelize-2126` | 2/2, $0.0079 | 2/2, $0.0069 | 2/2, $0.0212 |
| `excelize-2426` | 2/2, $0.0037 | 2/2, $0.0053 | 2/2, $0.0063 |
| `faker-2161` | 2/2, $0.0188 | 2/2, $0.0170 | 2/2, $0.0181 |
| `faker-2315` | 2/2, $0.0192 | 2/2, $0.0230 | 2/2, $0.0138 |
| `gh-cli-11097` | 2/2, $0.0246 | 2/2, $0.0208 | 2/2, $0.0148 |
| `gh-cli-12683` | 2/2, $0.0093 | 2/2, $0.0095 | 2/2, $0.0077 |
| `gh-cli-9132` | 0/2, $0.0439 | 2/2, $0.0252 | 0/2, $0.0241 |
| `highlightjs-4196` | 0/2, $0.0118 | 0/2, $0.0180 | 0/2, $0.0118 |
| `highlightjs-4226` | 2/2, $0.0118 | 0/2, $0.0221 | 1/2, $0.0203 |
| `highlightjs-4231` | 2/2, $0.0066 | 2/2, $0.0074 | 1/2, $0.0078 |
| `highlightjs-4261` | 0/2, $0.0572 | 0/2, $0.0261 | 0/2, $0.0205 |
| `lazygit-3444` | 2/2, $0.0103 | 2/2, $0.0168 | 2/2, $0.0114 |
| `lazygit-3721` | 2/2, $0.0205 | 1/2, $0.0310 | 2/2, $0.0382 |
| `lazygit-4321` | 2/2, $0.0153 | 1/2, $0.0248 | 1/2, $0.0169 |
| `locust-3438` | 2/2, $0.0021 | 2/2, $0.0033 | 2/2, $0.0025 |
| `mitmproxy-5891` | 0/2, $0.0089 | 0/2, $0.0162 | 0/2, $0.0103 |
| `mongoose-11913` | 0/2, $0.0372 | 0/2, $0.0305 | 1/2, $0.1178 |
| `mongoose-12739` | 2/2, $0.0107 | 2/2, $0.0120 | 2/2, $0.0131 |
| `mongoose-13760` | 0/2, $0.0155 | 0/2, $0.0211 | 0/2, $0.0197 |
| `mongoose-15120` | 2/2, $0.0332 | 2/2, $0.0423 | 1/2, $0.0300 |
| `mongoose-15192` | 1/2, $0.0053 | 2/2, $0.0059 | 2/2, $0.0069 |
| `mongoose-15494` | 1/2, $0.0226 | 1/2, $0.0308 | 1/2, $0.0315 |
| `mongoose-15653` | 2/2, $0.0118 | 2/2, $0.0197 | 2/2, $0.0232 |
| `mongoose-16072` | 1/2, $0.0204 | 1/2, $0.0288 | 2/2, $0.0137 |
| `networkx-6336` | 2/2, $0.0056 | 2/2, $0.0069 | 2/2, $0.0063 |
| `networkx-6920` | 2/2, $0.0176 | 2/2, $0.0216 | 2/2, $0.0153 |
| `networkx-6921` | 2/2, $0.0091 | 2/2, $0.0102 | 2/2, $0.0096 |
| `networkx-7720` | 2/2, $0.0034 | 2/2, $0.0041 | 2/2, $0.0066 |
| `networkx-8188` | 2/2, $0.0129 | 1/2, $0.0228 | 2/2, $0.0178 |
| `pre-commit-2002` | 2/2, $0.0056 | 2/2, $0.0098 | 2/2, $0.0108 |
| `scrapy-7042` | 2/2, $0.0047 | 2/2, $0.0048 | 2/2, $0.0043 |
| `scrapy-7604` | 2/2, $0.0031 | 2/2, $0.0036 | 2/2, $0.0030 |
| `stylelint-8134` | 2/2, $0.0023 | 2/2, $0.0038 | 2/2, $0.0028 |
| `stylelint-8818` | 2/2, $0.0076 | 2/2, $0.0109 | 2/2, $0.0096 |
| `stylelint-8983` | 2/2, $0.0034 | 2/2, $0.0052 | 2/2, $0.0047 |
| `stylelint-9008` | 2/2, $0.0030 | 2/2, $0.0046 | 2/2, $0.0032 |
| `yt-dlp-15017` | 2/2, $0.0155 | 2/2, $0.0311 | 1/2, $0.0123 |
| `yt-dlp-7544` | 0/2, $0.0155 | 0/2, $0.0262 | 0/2, $0.0214 |

## Feasibility screen: pointer-free tasks (v120); passes

**Outcome: there are enough natural tasks whose issue does not point at the code to build a separate workload, so a batch on it can be proposed.** v120 froze the definition and the rule before any count. Of the 1,392 automatic-criteria candidates that no catalog has used, 240 are pointer-free (17%). The rule needed 90, from at least 6 repositories in each of Go, Python and JavaScript; there are 42 from 13 Go repositories, 59 from 16 Python repositories and 108 from 15 JavaScript repositories, plus 31 TypeScript. t10's repositories alone give 91. No solver ran and no test ran. Rows are in [pointer-free-screen.json](../results/pointer-free-screen.json), and the script is `benchmark/pointer_free_screen.py`.

- **Definition.** The issue names no faulted file (path, file name, or a stem of four or more characters as a whole word). It also shares no code-shaped identifier with the faulted code: names with `_`, a digit or an inner capital, and lowercase names of six or more characters that are not dictionary words.
- **Limit.** The criterion removes literal pointers, not conceptual ones. An issue about a "logarithmic" scale, fixed in `scale.logarithmic.js`, passes because the stem `scale.logarithmic` never appears. Pointer-free tasks are lexically harder to locate, not guaranteed hard.
- **Scope.** Any result on this workload covers pointer-free natural tasks only, not issue-driven tasks in general.

## Batch t10 (v119): Claude Code with graf or Graphify installed, 48 fresh natural tasks; fails

**Outcome: `graf_installed` fails the preregistered rule.** It is not significantly cheaper than either comparator:
- against `builtin`: one-sided paired sign flip p = 0.288;
- against `graphify_installed`: p = 0.117.

Success is non-inferior against both. The rule required both comparisons to pass (intersection-union, alpha 0.05), so graf stays experimental. Every attempt completed: 288 of 288, with usage complete, no infrastructure errors and no protected-file edits. The decision is in [agent-decision-t10.json](../results/agent-decision-t10.json).

| Arm | Passed | Total cost | Median per attempt | Tokens |
|---|---|---|---|---|
| `builtin` | 84 / 96 | $1.679 | $0.0082 | 35.8 M |
| `graphify_installed` | 81 / 96 | $1.275 | $0.0102 | 36.3 M |
| `graf_installed` | 85 / 96 | $1.186 | $0.0074 | 31.9 M |

- **Setup.** Claude Code 2.1.312 on `claude-haiku-5-5`. Each tool was installed as its README says:
  - Graphify 0.9.97 through `graphify update .` and `graphify claude install`, with Bash allowed only as `graphify`;
  - graf (`.research/bin/graf-t10`, main `7a3b88a`) through `graf index`, `graf mcp` and the README's CLAUDE.md snippet.

  Attempts were capped at $0.50 and 1,200 s, two per task and arm. The tasks are the 48 of the v118 catalog, 16 per language.
- **Against `graphify_installed`:**
  - graf was cheaper on 33 of 48 tasks, saving $0.0009 per task on average;
  - token reduction p = 0.050, a secondary measure that is not gated;
  - it solved 4 more attempts.
- **Against `builtin`:**
  - graf was cheaper on 22 of 48 tasks;
  - the mean difference is driven by one task. `builtin` spent $0.63 on `caddy-8235` (two failed attempts), while graf spent $0.12 and Graphify $0.10;
  - without that task (post-hoc), `builtin` costs $1.051 and graf $1.068.
- **Every agent used the installed tool.**
  - Every graf attempt called `triage` at least once. Over 1,410 tool calls, graf attempts made 286 `Read` and 277 `Grep` calls, against 369 and 427 under `builtin`.
  - Every Graphify attempt ran `graphify` through Bash (500 calls).
- **Isolation.** Claude Code saved 12 oversized tool outputs into its own project folder outside the workspace, in 7 `builtin` attempts and 5 installed-arm attempts. When the agent then tried to read them, the request was refused (18 requests, 0 answered), as the isolation rule requires. The saved outputs are kept in each trial's `claude-code-project-folder`.
- **Per task.** Passes out of 2 and mean cost per attempt, in the order `builtin` / `graphify_installed` / `graf_installed`:

| Task | builtin | graphify_installed | graf_installed |
|---|---|---|---|
| `bubbletea-1711` | 2/2, $0.0045 | 2/2, $0.0055 | 2/2, $0.0046 |
| `bubbletea-1828` | 2/2, $0.0073 | 2/2, $0.0160 | 2/2, $0.0094 |
| `caddy-8235` | 0/2, $0.3142 | 0/2, $0.0488 | 0/2, $0.0590 |
| `caddy-8240` | 2/2, $0.0079 | 2/2, $0.0139 | 2/2, $0.0050 |
| `caddy-8247` | 0/2, $0.0124 | 0/2, $0.0205 | 1/2, $0.0223 |
| `caddy-8250` | 1/2, $0.0082 | 1/2, $0.0111 | 2/2, $0.0077 |
| `caddy-8254` | 2/2, $0.0045 | 2/2, $0.0076 | 2/2, $0.0057 |
| `caddy-8258` | 2/2, $0.0035 | 2/2, $0.0034 | 2/2, $0.0025 |
| `caddy-8265` | 2/2, $0.0115 | 2/2, $0.0082 | 2/2, $0.0059 |
| `caddy-8283` | 2/2, $0.0242 | 1/2, $0.0388 | 2/2, $0.0222 |
| `faker-2082` | 2/2, $0.0064 | 2/2, $0.0071 | 2/2, $0.0062 |
| `faker-2280` | 2/2, $0.0042 | 2/2, $0.0049 | 2/2, $0.0048 |
| `faker-2322` | 2/2, $0.0246 | 2/2, $0.0240 | 2/2, $0.0187 |
| `faker-2361` | 2/2, $0.0082 | 2/2, $0.0081 | 2/2, $0.0070 |
| `faker-2377` | 2/2, $0.0129 | 2/2, $0.0105 | 2/2, $0.0103 |
| `faker-2398` | 2/2, $0.0061 | 2/2, $0.0078 | 2/2, $0.0049 |
| `faker-2399` | 2/2, $0.0074 | 2/2, $0.0079 | 2/2, $0.0054 |
| `faker-2426` | 2/2, $0.0059 | 2/2, $0.0064 | 2/2, $0.0072 |
| `lazygit-1964` | 1/2, $0.0352 | 2/2, $0.0521 | 2/2, $0.0511 |
| `lazygit-2427` | 1/2, $0.0223 | 0/2, $0.0242 | 1/2, $0.0258 |
| `lazygit-4890` | 2/2, $0.0098 | 2/2, $0.0145 | 2/2, $0.0092 |
| `lazygit-5266` | 0/2, $0.0159 | 0/2, $0.0186 | 0/2, $0.0136 |
| `lazygit-5823` | 2/2, $0.0665 | 2/2, $0.0335 | 2/2, $0.0380 |
| `mongoose-112` | 2/2, $0.0187 | 2/2, $0.0206 | 1/2, $0.0253 |
| `mongoose-16532` | 2/2, $0.0137 | 2/2, $0.0138 | 2/2, $0.0111 |
| `mongoose-16539` | 2/2, $0.0052 | 2/2, $0.0104 | 2/2, $0.0069 |
| `mongoose-16560` | 2/2, $0.0026 | 2/2, $0.0041 | 2/2, $0.0027 |
| `mongoose-16561` | 2/2, $0.0029 | 2/2, $0.0048 | 2/2, $0.0042 |
| `mongoose-16573` | 2/2, $0.0108 | 2/2, $0.0119 | 2/2, $0.0137 |
| `mongoose-16601` | 2/2, $0.0124 | 1/2, $0.0183 | 0/2, $0.0123 |
| `multer-814` | 2/2, $0.0063 | 2/2, $0.0104 | 2/2, $0.0104 |
| `multer-950` | 2/2, $0.0056 | 2/2, $0.0065 | 2/2, $0.0071 |
| `networkx-8471` | 2/2, $0.0079 | 2/2, $0.0093 | 2/2, $0.0071 |
| `networkx-8885` | 2/2, $0.0074 | 2/2, $0.0084 | 2/2, $0.0049 |
| `networkx-8891` | 2/2, $0.0042 | 2/2, $0.0058 | 2/2, $0.0066 |
| `networkx-8898` | 2/2, $0.0029 | 2/2, $0.0042 | 2/2, $0.0029 |
| `pre-commit-1972` | 0/2, $0.0161 | 0/2, $0.0162 | 0/2, $0.0115 |
| `pre-commit-2970` | 2/2, $0.0029 | 2/2, $0.0035 | 2/2, $0.0039 |
| `pre-commit-3514` | 2/2, $0.0027 | 2/2, $0.0037 | 2/2, $0.0027 |
| `pre-commit-3706` | 2/2, $0.0026 | 2/2, $0.0048 | 2/2, $0.0033 |
| `stylelint-7181` | 2/2, $0.0113 | 2/2, $0.0147 | 2/2, $0.0323 |
| `stylelint-9119` | 2/2, $0.0126 | 2/2, $0.0138 | 2/2, $0.0166 |
| `stylelint-9172` | 1/2, $0.0189 | 0/2, $0.0112 | 2/2, $0.0239 |
| `stylelint-9221` | 2/2, $0.0050 | 2/2, $0.0094 | 2/2, $0.0065 |
| `stylelint-9325` | 2/2, $0.0079 | 2/2, $0.0059 | 2/2, $0.0085 |
| `stylelint-9422` | 2/2, $0.0100 | 2/2, $0.0071 | 2/2, $0.0065 |
| `stylelint-9527` | 2/2, $0.0180 | 2/2, $0.0164 | 2/2, $0.0074 |
| `task-3040` | 2/2, $0.0075 | 2/2, $0.0087 | 2/2, $0.0079 |

### Post-hoc diagnostic (t10)

Exploratory and not preregistered. It was run after the decision with `benchmark/agent_headroom_diagnostic.py`, whose tool lists now include the installed tools' names; the t5–t9 rows are unchanged.

**Code search and reads were a small part of the cost under the installed tools.** The share was:
- 11.7% under `builtin`;
- 16.3% under `graphify_installed`;
- 15.6% under `graf_installed`.

The 40–54% that the built-in replay estimated did not appear. That replay assumed whole-file reads, but agents with Claude Code's own `Read` mostly passed `offset` or `limit`: 250 of 369 reads under `builtin`, 207 of 261 under Graphify and 224 of 286 under graf. Most of the cost is the agent's output and carried history (49–64%) and the base context (11–17%).

- **The costliest attempts carry the gaps.** Under `builtin`, the costliest 10% of attempts carry 56% of the cost; under graf, 37%.
- **The first edit comes after a median of 4–5 calls** in every arm.
- **Unexplained cost under `builtin`.** The attribution there covers 74% of the audited cost, against 97–100% in the installed arms. Of the $0.43 that is not attributed, $0.41 comes from `caddy-8235`.
- **Localization is close to free on these tasks.**
  - In every arm, the median attempt reads a faulted file after 1–2 tool calls, and the first tool result already names that file.
  - The issue text explains why. 46 of the 48 issues share an identifier of six or more characters with the faulted code; 13 give its full path and 11 more its file name.
  - graf's `triage` named a faulted file in 77 of 96 attempts. Attempts where it missed cost no more: $0.0117 on average, against $0.0125 when it named one.
- **Cost goes into fixing and failing.** Failed attempts carry 47% of the cost under `builtin`, 28% under Graphify and 21% under graf.
- **What this implies.** The headroom that motivated t10 is not there on these tasks. A tool that finds code faster or returns smaller results cannot win by much when the issue already points at the code. The remaining differences come from how many calls an attempt takes to fix the fault. Rows are in [t10-localization-diagnostic.json](../results/t10-localization-diagnostic.json), and the script is `benchmark/t10_localization_diagnostic.py`.

## Post-hoc estimate: the same sessions with Claude Code's built-in Grep and Read (t5–t9)

**Finding: replayed with the built-in tools' result sizes, the recorded sessions would cost 1.36–1.73 times as much, and search and read results would rise from 14–20% to 40–54% of cost.** For the two `search_only` arms the share is 50.6% and 51.8% (51.1% pooled), just over the bar stated before the first run (at least half), so a batch against a built-in-tools baseline is worth proposing. No model was called. This is exploratory and not preregistered, and it supports no efficiency or superiority claim. Rows are in [agent-native-replay.json](../results/agent-native-replay.json), and the script is `benchmark/agent_native_replay.py`.

- **Method.** Every one of the 1,048 attempts keeps its own calls, and each search and read result keeps its place in the session; only its size changes. The built-in defaults come from the pinned Claude Code 2.1.312 binary. A read becomes Read's default: the first 2,000 lines of the file in `cat -n` form, refused over 25,000 tokens (that read then keeps its broker size), and free again while the file is unchanged. A search becomes Grep's default `files_with_matches` (paths only, at most 250). In a `content`-mode variant, it is instead up to 250 numbered lines, cut at 500 columns, with a result over 20,000 characters replaced by a preview. Grep's flags are reproduced: `--hidden`, version-control directories excluded, and `--max-columns 500`. Both variants agree within 4 points.
- **Reads drive it.** Grep's paths-only default is smaller than the broker's search output; reading whole files is what multiplies cost. The broker's 80-line, 2,000-token reads are why every arm in our batches was already cheap: pooled over `search_only`, broker cost is 0.60 times the replayed built-in cost (0.66 over all arms).
- **Limits.** Agents with built-in tools would not make the same calls. They can pass `offset` and `limit`, and with paths-only search they would need more reads. Files an attempt edited are measured in their final state. The replay cannot tell which way these limits move the total, so it bounds nothing; only a batch with a built-in-tools arm measures it.
- **What this implies.** Against how agents work by default, there is room for a code-search tool to save: the waste is whole-file reads, not search. Under the benchmark's broker every arm already gets capped reads for free, which is why graf, Graphify and plain search all tie there.

## Post-hoc diagnostic: how much of agent spend code search can save (t5–t9)

**Finding: in these batches, code-search results cause 14–20% of an attempt's cost, and 12–18% before the first edit. That share bounds what any code-search tool could save, graf or Graphify.** The rest is the agent's own output (24–37%, mostly thinking), its carried history (25–34%), and the base context of prompt, tools and orientation (7–16%). No model was called. This is exploratory and not preregistered, so it supports no efficiency or superiority claim. Rows are in [agent-headroom-diagnostic.json](../results/agent-headroom-diagnostic.json), and the script is `benchmark/agent_headroom_diagnostic.py`.

- **Method.** A tool result is written to the prompt cache once and re-read on every later model call. Its cost is its size times the write price, plus its size times the read price for each later call. Sizes come from each call's own cache usage, split between the results that arrived since the previous call in proportion to their cl100k sizes. Output comes from the final usage record, because streamed messages carry partial counts. Prices are the frozen Haiku list prices. The attribution covers 79–100% of each arm's audited cost; calls billed at the long-context rate are not separated, so that is the likely gap.
- **The no-tool control is cheapest where it ran.** `search_only` costs $0.83 (t5) and $0.74 (t6) in total, against $0.91 and $0.86 for graf and $1.14 and $0.94 for Graphify. Its search-and-read share (18–20%) is close to the retrieval arms' share in the same batches (14–18%), so the orientations did not replace search; they added base context.
- **Agents reach the first edit quickly.** The median is 3–5 model calls of 7–9, in every arm. graf's orientations reach it about one call sooner in t5–t7, without a lower cost.
- **The expensive tail is failed fixing, not searching.** The costliest 10% of attempts (104) carry 42% of all cost, with a median of 23 calls. Only 38 of the 104 passed. Their search-and-read share is 16%, no higher than the rest.
- **Repository size barely moves it.** The harness caps each search at 2,000 tokens by default (8,000 at most) and 10 hits per file, and each read at 200 lines, so a larger repository cannot enlarge a result; it can only add calls. Across the 166 tasks (27 to 9,578 files per workspace), the search share rises only weakly with size (Spearman 0.22; 16% in the smallest quarter, 20% in the largest). The largest repository, prettier (9,578 files, about half the size of the scale diagnostic's VS Code at 18,830 and Kubernetes at 22,111), has a search share of 18.5% over 32 attempts, with a median of 5 calls to the first edit.
- **What this implies.** A tool that only made search results smaller can save at most about a fifth of spend in this setup. Large savings would have to come from fewer model calls, and the long sessions spend their calls on edit-test loops after localization. Exploratory, with development data from older binaries and solver versions; it does not transfer to other models or tasks without measurement.

## Offline screen: `graf find` replayed on t7–t9 search-then-read pairs; fails, no batch

**Outcome: `graf find` fails the v112 readiness screen, so no agent batch runs.** `graf find` is a regex search that returns each match's enclosing function (branch `feat/find-units`, commit 95c9106). The screen tested whether one `find` call would have delivered the code agents read right after a search, so it could replace the search-and-read pair. It would have made only 14.4% of the following reads redundant (rule: at least 40%). Its output was not smaller than the search plus reads it would replace (median token ratio 1.05; rule: at most 1.0). Every agent pattern was valid for graf's regex engine. `find` stays experimental. Rows are in [find-replay-screen.json](../results/find-replay-screen.json).

- **Data.** Development data only: 462 search-then-read pairs with 652 following reads, before the first edit, in every attempt of t7, t8 and t9 (both arms). The script is `benchmark/find_replay.py`. The tasks' faulty states were rebuilt without a model, and no pair was skipped.
- **By batch and arm:** 10–21% of reads redundant and median token ratio 0.93–1.13. The screen is not close to passing in any group.
- **Why (post-hoc, from the rows):**
  - **Broad patterns.** Agents' searches are often wide alternations, such as `^class |max_string_width|…`. Within the budget, `find` fills its output with earlier files' functions before it reaches the file the agent then read; 69 pairs had zero recall.
  - **Wide reads.** Agents typically read 80–140-line windows, more than one function, so even a correct block covers only part of the read.
- **Scope.** This is a replay of recorded sessions. An agent given `find` might search differently, and this screen cannot measure that. Under the preregistered rule, that possibility does not justify a batch.

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


## Current triage workflow: batch t6 (2026-10-22)

Prepared, not executed. Preregistration v27–v28 freezes a separate current-build comparison of `triage → outline → targeted reads` against pinned code-only Graphify with source reads and search-only. The old t4/t5 plans and t5 binary remain unchanged; t4 has 60 of 720 summaries and t5 has none.

The t6 plan has 48 existing natural tasks × five attempts × three arms (720 trials), sequentially interleaved with seed 20261024. Claude Code 2.1.307 and model `claude-haiku-5-5` are frozen across arms. Each attempt has a $0.50 usage-equivalent cap and 1,200-second timeout. The task-paired cost superiority and success non-inferiority tests retain the existing rule. This reused catalog is a regression diagnostic and cannot authorize promotion, even if its statistical rule eventually passes. Persistent product MCP is outside its scope.

The current binary is `.research/bin/graf-t6`, SHA-256 `f827c273265ccb027a0fab332624398821aa26afb379c8b5d591d69a1796d45b`. Sources, initial and final manifests, commands, and the full fixed trial order are preserved under `.research/evidence/agent-t6/`. The initial local check failed because the broker expected a legacy JSON index; v28 records the schema-2 export validation repair before any solver calls. That failed local check is retained.

[Local preflight](../results/agent-t6-preflight.json) confirms identical source snapshots for all three arms, bounded orientations from both retrieval backends, and functioning current-graf outline/read operations. All 140 Python tests pass. These are fixture checks, not comparative efficiency evidence.

Automatic approval review rejected launching the first external Claude trial because the request did not explicitly authorize transferring repository source and issue text to that solver. No t6 model call ran. [Run status](../results/agent-t6-status.json) records the blocker; [decision](../results/agent-decision-t6.json) remains unpassed with missing trials and promotion blocked. No new agent savings are measured.

After explicit transfer approval, resume with `.research/venv/bin/python benchmark/run_agent_batch.py --batch t6`; then run `.research/venv/bin/python benchmark/agent_decision.py --batch t6`. Keep raw failures and usage; do not infer a win from a partial batch.

The user subsequently reported that Claude has reached its weekly limit (not independently verified; reset time unknown). This is an additional execution blocker. No solver or model was substituted, no trials ran, and the frozen comparison remains unchanged.


## Codex subscription diagnostic (2026-10-22)

A separate structured-action solver adapter now invokes `codex exec` through the existing ChatGPT login, with GPT-6 Luna and low reasoning. Each invocation returns one broker action as JSON; the parent harness performs source reads, retrieval, edits and verification. Every invocation receives the full issue, available broker operations and action/result history. This new protocol is not pooled with historical Claude batches. The pinned Claude t6 binary and plan remain unchanged.

Preregistration v29 froze `codex-d1`: three reused natural tasks (`gin-2718`, `click-1884`, `undici-5520`), one attempt per arm, comparing current pinned graf orientation, code-only pinned Graphify orientation plus source reads, and search-only. The first invocation stopped because the parser misclassified an unstable-feature startup warning. It consumed 7,275 input tokens (including 3,456 cached) and 36 output tokens; no broker action was dispatched, and harness verification failed. The remaining eight trials were not run. Raw artifacts and the failed plan remain preserved; see [failure accounting](../results/codex-d1-failure-audit.json).

Before further task calls, v30 froze `codex-d2` with the corrected adapter, explicit JSON-action examples and the same nine-case diagnostic. The graf executable is pinned separately at `.research/bin/graf-codex-d1` (SHA-256 `580a2ea276d082cbecf5035e9eb84aaf59fd126fb3eef5edaad8b81ce609dbf2`); Graphify remains commit `86c57be8204673c2a1b9849a3292a7dd3ab373d8`. Source manifests, implementation snapshots and frozen orders are under `.research/evidence/codex-d1/` and `.research/evidence/codex-d2/`. Both sets retain a shuffled sequential competitor order, isolated workspaces/indexes, shared warm OS/dependency caches and uncontrolled reported provider-cache hits.

Limits are identical across arms: 12 requested actions, 60 seconds per invocation, 600 seconds per attempt, an observed 50,000-token stop threshold per attempt and 300,000 across the batch. Observed thresholds are not hard generation caps: the final invocation or attempt can overshoot. No dollar cap can be enforced from an unmeasured subscription charge. Prompts, commands, raw JSONL, stderr, full broker payloads, initial/final source hashes, verification and token totals are retained. Invalid actions still retain any reported usage. Native tool events invalidate a run. Provider, infrastructure and missing-usage errors stop the batch instead of silently replacing attempts.

This is a workflow diagnostic on three reused public tasks. CLI invocation counts and reported input/cache/output/reasoning tokens are available; internal provider request counts, subscription charges and API-equivalent costs are unknown. Full agent-efficiency acceptance and product promotion remain unpassed regardless of descriptive token differences. Results are recorded in [the Codex diagnostic report](../results/codex-d2.json); missing trials and every failed attempt remain visible. This protocol measures neither persistent product MCP nor Rust/documentation task quality.


The d2 batch was also stopped after review found that multiple action messages in one invocation could cause the adapter to select a later test request while skipping an earlier edit request. Its two completed trials and one interrupted trial are invalid for retrieval comparison. The interrupted workspace was graded without resuming its solver. One interrupted invocation lacks final usage; its cost is unknown, not zero.

Preregistration v31 freezes `codex-d3`, a smaller paired protocol validation on `click-1884` alone (three arms, one attempt each). The adapter executes the first JSON action and records how many later action messages were discarded; all invocation usage is counted. Arguments are a JSON object with normal newline/tab encoding. A synthetic exact-edit test verified that the model's replacement matches tab-indented multiline source. Limits are eight actions, 60 seconds per invocation, 600 seconds per attempt, an observed 150,000-token threshold per attempt and 450,000 for the batch; the increase from d2 follows the recorded pilot evidence and precedes all d3 calls. These thresholds can overshoot on the final invocation or attempt. Earlier failed plans remain in `codex_diagnostic_history`, with immutable preregistration copies and raw artifacts. The latest paired diagnostic report is [codex-d3.json](../results/codex-d3.json); it cannot support superiority or agent-efficiency acceptance.


The d3 pilot completed all three attempts on identical initial source manifests:

| Arm | Verifier success | Reported input + output tokens | Cached input subset | CLI invocations | Wall seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| graf current orientation + reads | 0/1 | 111,058 | 52,480 | 5 | 41.86 |
| pinned code-only Graphify + reads | 0/1 | 166,742 | 94,336 | 7 | 63.75 |
| search + reads | 0/1 | 185,335 | 123,904 | 7 | 70.07 |

Graf made an incomplete edit and finished after a failing test. Graphify and search-only stopped after exceeding the observed per-attempt token threshold; the last invocation's usage was retained even though its action was not dispatched. The batch total was 463,135 reported tokens, exceeding the 450,000 observed stop threshold on its last attempt. This is a stopped diagnostic, not a successful agent comparison or a token-saving claim. Only one previously exposed Python task was used, and every arm failed. Shared warm OS/dependency caches and uncontrolled provider cache hits apply; workspaces and indexes were isolated. Wall time includes orientation, index builds triggered during orientation or tools, and final trusted verification; it excludes workspace preparation. Index build samples are also retained separately.

[Detailed accounting](../results/codex-d3-accounting.json) retains broker payload totals, failed tool requests, discarded action messages, CLI invocation counts and source parity. Complete final CLI usage was available for all 19 pilot invocations, but internal provider request counts and subscription charges remain unknown. Fresh CLI turns repeatedly include the complete history; their large context overhead and continued extra action messages make this prototype unsuitable for scaling the batch yet. Future protocol changes require a new preregistration before further task calls.

[Setup accounting](../results/codex-setup-accounting.json) separately preserves the synthetic smoke tests and invalid d1/d2 runs: at least 179,694 reported tokens, with two invocations lacking final usage (one initialization failure and one interrupted call). Unknown usage is not counted as zero. Neither this setup total nor the invalid trials contribute to comparative conclusions. All 147 Python integrity tests passed, including seven Codex adapter tests; `git diff --check` passed. These checks validate their fixtures, not comparative efficiency. Product promotion and full agent-efficiency acceptance remain unpassed.


## Native Codex MCP diagnostic (2026-10-22)

A separate adapter now runs one ephemeral `codex exec` per attempt and exposes the existing task broker through typed MCP tools. It does not replay a custom JSON action history or discard model action messages. Built-in shell, browser, apps, plugins, image and agent tools are disabled; the stable tool host must be enabled for this installed CLI to route MCP calls. The experimental Code Mode switches are explicitly disabled. Every completed native MCP action must match the independent broker log, including arguments and repeated/failed requests; unexpected tool items invalidate the attempt. Internal provider request accounting remains unavailable.

Synthetic smoke calls are preserved under `.research/evidence/codex-native-smoke/`: the first failed with the stable host disabled; the second discovered an advertised but unimplemented fixture search; the third repaired the tab-indented fixture and passed verification using five native calls. All usage remains recorded, including both failures. This fixture success does not establish natural-task repair quality.

Preregistration v32 froze `codex-m1`, but local preflight found that resolving the Python venv symlink selected the base interpreter without benchmark dependencies. No natural-task model call ran. [The zero-call report](../results/codex-m1.json) remains incomplete. Before task calls, v33 froze `codex-m2` with the venv path preserved and dependency checks. All 152 Python integrity tests passed. Earlier plans, raw outputs, pinned graf binary and Graphify revision are retained unchanged.

The m2 plan uses the same exposed `click-1884` task, one attempt each for graf orientation plus reads, pinned code-only Graphify plus reads, and search-only, with GPT-6 Luna at low reasoning. Each arm has 16 dispatched broker requests and a 180-second process-group timeout. Excess and post-finish requests are logged and refused. A 150,000-token per-attempt threshold is observed only after the entire native turn; the batch stops before another attempt once its observed 450,000-token threshold is reached. These are not live generation or dollar caps. The protocol, action limit and timing cap differ from d3, so cross-protocol differences cannot isolate the adapter's effect.

Sources, commands, raw JSONL, stderr, full broker request/reply payloads, verification and implementation snapshots remain under `.research/evidence/codex-m2/` and `.research/trials/codex-m2-*/`. Arms run sequentially in frozen shuffled order, with isolated workspaces/indexes, shared warm OS/dependency caches and uncontrolled provider-cache hits. Wall time includes orientation and its index build, subsequent tool-triggered rebuilds, CLI startup and final verification; workspace preparation is excluded. Index build timing samples are also retained separately. [The native diagnostic report](../results/codex-m2.json) keeps full agent-efficiency acceptance and product promotion false regardless of descriptive differences.


The native m2 pilot completed all three trials without protocol/accounting errors:

| Arm | Verifier success | Reported input + output tokens | Cached input subset | Native broker requests | CLI invocations | Wall seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| graf orientation + reads | 0/1 | 58,465 | 44,288 | 4 | 1 | 15.26 |
| pinned code-only Graphify + reads | 0/1 | 102,218 | 77,952 | 7 | 1 | 35.08 |
| search + reads | 0/1 | 99,594 | 86,016 | 7 | 1 | 35.21 |

Every native request matched the independent broker log, and all source manifests matched. All 18 broker requests succeeded at the transport/operation level; none was blocked. All three agents called finish, but their edits failed the trusted task verifier. No action, time, or observed token threshold was reached. The 260,277-token batch is a diagnostic on unsuccessful repairs; its lower descriptive token count for graf is not an agent-efficiency win. The changed protocol and limits also prevent attributing differences from d3 to context handling alone.

[Native accounting](../results/codex-m2-accounting.json) retains each case, payload totals, cache counts, timing and source parity. [Native setup accounting](../results/codex-native-setup-accounting.json) separately records 67,986 reported tokens across three synthetic smoke calls, including both failures. Internal provider request counts and subscription charges remain unknown. Keep this solver configuration experimental; validate repair quality in a new frozen solver diagnostic before scaling to fresh held-out tasks. No graf feature or product acceptance status changes from this pilot.

The separate [offline fixture control](../results/codex-m2-fixture-control.json) failed before repair and passed after reversing the frozen catalog faults, with protected tests unchanged. No solver call was made for this check, and it is excluded from the comparison. This confirms a passing reference repair exists for this verifier; it does not establish comparative retrieval quality.


Preregistration v34 froze `codex-m3` before any m3 task calls. The only planned solver change from m2 was GPT-6 Luna reasoning effort from low to medium. The reused task, source manifest, instructions, pinned graf/Graphify/search binaries, sequential arm order, tokenizer, output budgets, one attempt per arm, 16-request limit, 180-second timeout and observed token thresholds remained identical. Earlier unsuccessful cases remain separate. All frozen implementation hashes passed preflight; no implementation changed during this run.

| Arm | Verifier success | Reported input + output tokens | Cached input subset | Native broker requests | Wall seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| graf orientation + reads | 0/1 | 82,882 | 56,576 | 5 | 17.53 |
| pinned code-only Graphify + reads | 0/1 | 125,007 | 106,880 | 6 | 26.76 |
| search + reads | 1/1 | 124,405 | 92,544 | 11 | 23.08 |

All three attempts completed with identical initial source manifests, complete reported invocation usage and matching native/broker action logs. There were no failed or blocked broker operations, infrastructure errors or reached resource limits. Each arm used one CLI invocation. The batch consumed 332,294 reported tokens. Shared warm OS/dependency caches, uncontrolled provider-cache hits and the m2 timing boundaries apply. Internal provider request counts and charges remain unknown.

Graf retrieved the target `version_option` function and read its source, then finished without an edit or test call. Graphify edited and tested, but its repair failed. Search-only edited twice and tested twice before passing trusted verification. This establishes that the cheap solver can repair this fixture under these limits; it does not establish a reliable success rate or explain the difference between arms. Premature termination and the influence of additional retrieval context are hypotheses for further diagnostics. The lower graf token count accompanies a failed repair and cannot support a saving claim. A single exposed task and a single stochastic attempt cannot establish an effect from changing reasoning effort.

[The m3 report](../results/codex-m3.json) and [detailed accounting](../results/codex-m3-accounting.json) preserve every outcome. Commands, raw streams, complete tool payloads, verification and hashes remain under `.research/evidence/codex-m3/` and `.research/trials/codex-m3-*/`. Full agent-efficiency acceptance and product promotion remain false. This agent solver diagnostic does not replace the existing paired retrieval acceptance/decision artifacts. Do not scale this configuration yet or retry these IDs.


Preregistration v35 froze `codex-m4` before task calls: the same GPT-6 Luna medium configuration, exposed `click-1884` task, pinned backends, source hashes, sequential order and resource limits as m3, with a uniform test-before-finish policy and explicit matching solver instructions. A normal finish requires a completed test on the current source. Successful edits invalidate earlier tests; unsuccessful edits do not. Behavioral failure still counts as a completed test, so this policy does not require passing verification or suppress failures. A refused finish leaves the session open, consumes an action, and is preserved in the independent MCP request/reply log. The parent still grades every attempt at termination. Action exhaustion and infrastructure failure override this policy. Refused requests are counted separately from closed/limit requests; native reply-content token totals include refused replies. Transport payload token totals differ from the existing serialized Session-event payload totals and must not be added together.

All 154 Python integrity tests passed, including tests of policy parity across all three arms, refusal accounting, action exhaustion, and retesting after successful edits. The earlier m1–m3 implementation snapshots and results remain preserved. The new policy is a solver-harness experiment; no graf product retrieval code changed. The native runner passed frozen preflight; evidence is under `.research/evidence/codex-m4/` and results under `results/codex-m4.json`. All promotion gates remain false regardless of this reused task's outcome.


The m4 pilot completed every planned attempt:

| Arm | Verifier success | Reported input + output tokens | Cached input subset | Native broker requests | Wall seconds |
| --- | ---: | ---: | ---: | ---: | ---: |
| graf orientation + reads | 1/1 | 146,700 | 116,352 | 9 | 41.96 |
| pinned code-only Graphify + reads | 0/1 | 139,039 | 112,768 | 9 | 46.89 |
| search + reads | 0/1 | 104,048 | 68,096 | 9 | 24.04 |

All source manifests matched, each attempt used one CLI invocation, all native calls matched the independent broker request log, and all invocation usage was available. No broker errors, policy refusals, closed/limit blocks, infrastructure errors, or resource-limit stops occurred. The batch consumed 389,787 reported tokens. Shared warm OS/dependency caches and uncontrolled provider-cache hits apply; cached input remains a subset of total input. Timing includes orientation/index construction, CLI startup, tool-triggered rebuilds and trusted final verification, excluding workspace preparation. Provider-internal requests, subscription charges and API-equivalent costs remain unknown.

Graf tested the faulty source, edited twice, and passed on its third test before a passing finish. Graphify and search-only each edited and tested, then finished despite a failing behavioral test. All three complied with test-before-finish, so this run did not exercise a policy refusal. The passing graf attempt establishes fixture feasibility for this configuration. It does not establish reliable performance or a causal policy effect: one reused task was attempted once per arm, and search-only succeeded in m3 while graf failed. Graf also used more reported tokens than either failed comparator in m4. Do not infer useful token savings or pool results across changed protocols.

[The m4 report](../results/codex-m4.json) and [accounting](../results/codex-m4-accounting.json) preserve every outcome, both payload measures, test failures, source parity, commands and timing. Complete raw evidence remains in the frozen evidence directory and per-arm trial directories. Product promotion and full agent-efficiency acceptance remain false. Existing retrieval acceptance/decision artifacts were not replaced by this solver diagnostic. No further model calls followed m4.


## Development solver validation (2026-10-23)

Preregistration v36 freezes `codex-v1`: `gin-2718` (Go), `click-1884` (Python), and `undici-5520` (JavaScript), with two attempts per task and three approaches (18 planned trials). These are the same three exposed development tasks selected for earlier d1/d2 diagnostics, not fresh held-out cases. Selection was recorded before new offline fixture controls or model outcomes; no substitutions are allowed. Every faulted fixture failed and every reference repair passed, with protected tests unchanged. [Fixture-control evidence](../results/codex-v1-fixture-controls.json) is offline and excluded from comparative metrics.

The solver retains GPT-6 Luna at medium reasoning, native typed MCP, the m4 test-before-finish policy and instructions, pinned graf binary and code-only Graphify revision. The frozen seed shuffles task/repetition groups, then competitors within each group; all competitors run sequentially. Per attempt: 16 broker requests (including failures and refused finishes), 180 seconds for the native Codex process, and a 150,000-token observed threshold. The batch threshold is 2,700,000 observed tokens. Token thresholds are checked after a complete native turn and can overshoot. Orientation/index work and final verification have separate existing tool timeouts and are included in wall time; workspace preparation is excluded. Workspaces/indexes are isolated, OS/dependency caches are shared and warm (including offline controls), and provider cache hits are uncontrolled and recorded as an input-token subset. No dollar or internal-request cap is measurable.

Solver readiness requires all 18 unique cases with complete usage, no adapter/infrastructure/accounting/limit error and an agent-called finish, plus at least four of six verifier passes for every approach and at least one of two passes for every task/approach. Both conditions must hold. Failed tool operations and policy refusals stay in the logs and counts. Every graf failure paired with a passing comparator is listed. Repetitions are nested within tasks; three task units do not establish statistical superiority. This descriptive readiness gate never overrides false product-promotion and full agent-efficiency gates. Provider/infrastructure/accounting errors stop the batch; behavioral failures alone do not. Missing cases remain missing, with no retries or replacements.

All 158 Python tests passed, including workload completeness, duplicate/missing case rejection, per-task losses hidden by aggregate pass counts, missing usage/forced finish, and adjacent comparator ordering. Frozen configuration, implementation snapshots, source hashes, order and preflight are under `.research/evidence/codex-v1/`, with an immutable v36 preregistration copy. The runner preserves all model streams, commands, tool payloads, verifier output and timing samples in per-attempt directories and updates [the validation report](../results/codex-v1.json). No accepted graf baseline exists.


All 18 planned trials completed. No provider, infrastructure, missing-usage or native/broker log-matching error stopped the batch; all per-task source manifests matched across both repetitions and every approach. All 18 CLI invocations have final reported usage. The total was 2,611,964 input-plus-output tokens, including 2,139,648 cached input tokens as a subset. Internal provider request counts and monetary charges remain unknown.

| Approach | Verifier passes | Reported input + output tokens | Native broker requests | Total wall seconds |
| --- | ---: | ---: | ---: | ---: |
| graf orientation + reads | 3/6 | 1,070,662 | 63 | 173.02 |
| pinned code-only Graphify + reads | 4/6 | 907,362 | 56 | 208.80 |
| search + reads | 2/6 | 633,940 | 49 | 121.24 |

Every paired task/repetition is shown below. Each entry is verifier outcome / reported tokens; repetitions belong to the same task unit and are not independent task samples.

| Task | Repetition | graf + reads | Pinned Graphify + reads | Search + reads |
| --- | ---: | ---: | ---: | ---: |
| gin-2718 | 1 | fail / 127,210 | pass / 61,228 | pass / 56,494 |
| gin-2718 | 2 | pass / 96,330 | pass / 81,545 | fail / 80,530 |
| click-1884 | 1 | pass / 211,831 | pass / 177,474 | pass / 178,754 |
| click-1884 | 2 | pass / 165,547 | pass / 181,278 | fail / 73,064 |
| undici-5520 | 1 | fail / 265,612 | fail / 295,601 | fail / 115,833 |
| undici-5520 | 2 | fail / 204,132 | fail / 110,236 | fail / 129,265 |

Graf failed `gin-2718` repetition 1 while both Graphify and search-only passed: one paired case with two passing comparators. Both graf Python repairs passed; one Go repair passed; both JavaScript repairs failed. All six JavaScript repairs failed across approaches. Graphify passed every Go and Python attempt. Aggregate totals must not hide this graf quality loss. In this development run graf used more reported tokens and passed fewer repairs than Graphify; this is not a significance result or a general ranking. Search-only used fewer tokens but passed fewer repairs. No useful agent-saving claim follows from these results.

Eight attempts exceeded the 150,000-token observed per-attempt threshold; all their reported usage and outcomes remain counted. One Graphify JavaScript attempt made a seventeenth request that the broker blocked under the 16-request limit. Three agents ended without calling finish (two Graphify JavaScript attempts and one search-only Go attempt); the parent graded all three. There were 168 native broker requests, two operation-level error replies (including that block), and no finish-policy refusals. Failed behavioral tests are distinct from broker operation errors. All cache and timing conditions above apply. The 2,700,000 observed batch threshold was not reached; no cases were replaced or retried.

Both the protocol and behavioral readiness conditions failed. No approach achieved at least one JavaScript pass, graf and search-only also fell below four total passes, and token-limit violations/forced finishes prevented protocol readiness. Product promotion and full agent-efficiency acceptance remain false. Existing retrieval acceptance/decision artifacts were not replaced by this agent solver validation.

[Full accounting](../results/codex-v1-accounting.json) preserves every case, paired source parity, raw outcomes, payload measures, timing and severe quality losses. The frozen v36 [solver report](../results/codex-v1.json) includes the readiness decision. Offline accounting is reproducible with `.research/venv/bin/python .research/evidence/codex-v1/summarize.py`; it makes no model calls. Keep this workflow experimental and use these development failures for diagnosis before a separately frozen validation. Do not scale to held-out promotion or revise these outcomes.
### Offline development failure audit (2026-10-23)

The saved v36 artifacts were audited without running a model, verifier, retrieval comparison or changing a trial. Reproduce with `.research/venv/bin/python benchmark/audit_codex_validation.py --report results/codex-v1.json --output results/codex-v1-failure-audit.json`. The [audit](../results/codex-v1-failure-audit.json) records every case, operations, edit paths, requested read overlap, failed-test feedback and SHA-256 hashes of the input artifacts.

Across 18 attempts, nine repairs failed and three agents omitted finish. All 19 failed test calls delivered their complete verifier stdout/stderr under the existing prefix policy; truncation did not hide their failures. Eight failed test calls had no subsequent successful edit. These are call-level counts, not eight distinct attempts. Every JavaScript test call showed both the unexpected connection and the expected argument error. One JavaScript attempt made no edit or test and claimed it lacked tools to change source, although edit was available.

The JavaScript reference rejects foreign FormData in `lib/core/request.js` during request construction, before connection setup. Four attempts edited dispatcher h1/h2 code too late; another edited util/fetch code without repairing the constructor check; the remaining attempt did not edit. No attempt restored the required early rejection. The Go failure in graf repetition 1 and the search-only Go failure retained incorrect address/trust behavior. These are repair failures, not evidence of unavailable fixture dependencies: the independently recorded inverse-fault controls pass.

Provider-reported tokens greatly exceed the separate cl100k broker-reply counts. These counts use different tokenizers and boundaries. Requested source-line overlap exists in several cases, but does not measure delivered duplicate text, and cannot explain the input growth by itself. Provider-internal prompt composition remains unknown; cached input is already included in reported totals. No context-saving estimate follows.

An optional `show_action_budget` experiment now appends remaining tool calls to actual native broker replies, including errors, policy refusals and blocked calls, and explains the total in solver instructions. Low-budget feedback reminds the solver to reserve test/finish calls; it never changes verification or requires a pass to finish. The option applies identically to graf, pinned Graphify plus reads and search-only, is disabled by default, and has not been evaluated with models. All 160 Python tests pass, including budget accounting and default compatibility. The old frozen runner correctly rejects the changed implementation hashes; v36 artifacts and implementation snapshots remain intact. A new frozen plan is required before another batch. Both promotion gates remain unpassed.

### Budget-feedback solver diagnostic (2026-10-23)

Preregistration v37 (`codex-v2`) enabled the optional remaining-action feedback and matching initial instructions uniformly for graf, pinned code-only Graphify plus source reads, and search-only. The model remained GPT-6 Luna medium. Tasks, two repetitions, shuffled adjacent arm order/seed, frozen sources/backends, finish policy, 16-request limit and observed 150,000-token attempt / 2,700,000-token batch thresholds matched v36. All three new faulted/reference controls passed before calls; the prior 160 Python tests cover the unchanged implementation. No tuning, retries, replacements or limit increases followed new results. Immutable plan: [v37](../results/preregistrations/v37-98194c1125db28a4fd886a0f5c7b6c190a87a63901c3602b712e612f75a30bcf.json).

The run stopped at the frozen batch threshold after **13/18 attempts**, using **2,767,997 reported input-plus-output tokens** (including 2,379,904 cached input tokens). Thresholds are observed after a native turn, so the last turn overshot. All 13 invocations have usage and matching native/broker counts; 161 native tool requests include one operation error. Ten attempts exceeded the observed per-attempt token threshold. No request was blocked or finish refused. Graf Python repetition 1 stopped without agent-called finish; parent verification graded it as a failure. Both protocol and behavioral readiness failed, and both product/agent-efficiency gates remain false.

Only four task/repetition groups have all three approaches. Their shared results are:

| Approach | Verifier passes | Reported tokens | Native tool requests | Wall seconds |
|---|---:|---:|---:|---:|
| graf | 1/4 | 892,301 | 45 | 103.75 |
| Pinned code-only Graphify plus reads | 2/4 | 1,034,234 | 57 | 166.71 |
| Search-only | 2/4 | 756,168 | 52 | 116.82 |

Every case, including missing cases, is retained below. Each completed cell shows verifier result / reported tokens; no pass is inferred for a missing attempt.

| Task / repetition | graf | Graphify plus reads | Search-only |
|---|---|---|---|
| Python click-1884 / 1 | FAIL / 97,808 | PASS / 228,700 | PASS / 163,621 |
| Python click-1884 / 2 | PASS / 145,265 | PASS / 242,429 | PASS / 171,958 |
| JavaScript undici-5520 / 1 | FAIL / 357,293 | FAIL / 273,060 | FAIL / 199,413 |
| JavaScript undici-5520 / 2 | FAIL / 291,935 | FAIL / 290,045 | FAIL / 221,176 |
| Go gin-2718 / 1 | MISSING | MISSING | MISSING |
| Go gin-2718 / 2 | MISSING | MISSING | PASS / 85,294 |

The extra search-only Go result is unmatched and excluded from the shared table. Graf Python repetition 1 is a severe quality loss against both comparators. All JavaScript repairs still fail. Lower aggregate graf tokens than Graphify on the four complete groups accompany fewer correct repairs, so they do not establish useful agent savings or satisfy acceptance.

For historical context on these same four groups, v36 graf passed 2/4 using 847,122 tokens; Graphify passed 2/4 using 764,589; search-only passed 1/4 using 496,916. V37 does not demonstrate better efficiency or readiness. These separate stochastic runs are not randomized policy controls and cannot isolate a causal effect of feedback. Missing Go cases prohibit a complete three-task comparison with v36. There is no accepted graf baseline.

All observed source manifests match the freeze. Every actual MCP reply contained the correct remaining count, including the operation error. Feedback contributed 3,371 cl100k tokens already included in native reply payload counts; do not add them again. Implementation hashes remained unchanged during calls. Isolated workspaces/indexes used shared warm OS/dependency caches, including controls; provider caches were uncontrolled and cached input counted. Timing includes orientation/index work, native startup, tools and final verification, excluding workspace preparation. Full raw commands, source manifests, outputs and timing samples remain under `.research/trials/codex-v2-*`; snapshots, controls, order and audits are under `.research/evidence/codex-v2`.

See [raw summaries](../results/codex-v2.json), [accounting and missing cases](../results/codex-v2-accounting.json), [failure audit](../results/codex-v2-failure-audit.json), and [offline controls](../results/codex-v2-fixture-controls.json). The audit found all 19 failed test replies complete, with six failed test calls lacking a later successful edit. Internal provider request counts and charges remain unknown. This is an unsuccessful development solver diagnostic, not a product promotion, documentation-quality result, significance test or token-saving claim. Keep feedback experimental and disabled by default. No retrieval comparison was run, so existing retrieval acceptance/decision artifacts are unchanged. No further model calls were made after the batch stop.

### Offline context and capability audit (2026-10-23)

No model, verifier or retrieval backend was invoked for this audit. The [v36 context inventory](../results/codex-v1-context-audit.json) and [v37 context inventory](../results/codex-v2-context-audit.json) preserve hashes of every inspected artifact. Reproduce either with `.research/venv/bin/python benchmark/audit_codex_context.py --report results/codex-v2.json --spec .research/evidence/codex-v2/spec.json --output results/codex-v2-context-audit.json`, substituting `v1` for `v2` for the earlier batch.

In each batch, five of six JavaScript attempts received the literal faulty body-acceptance conditional in a tool response. Several read the request-construction block but still attempted later dispatcher fixes. Visibility does not prove understanding; this evidence rules out a general claim that the retriever never exposed the relevant code. The v37 Python graf attempt that stopped without editing retrieved `version_option` three times and read part of it. Its source replies also contained upstream descriptions of the API's frozen feature scope. Whether that prose influenced the solver is unknown: there is no captured reasoning establishing a cause. The two attempts claiming inability to edit did not attempt an edit operation; their final wording alone does not establish a denied tool or missing capability.

The historical native request logs contain tools/call exchanges, not startup tools/list replies. The audit reconstructs tool definitions only when both schema dependencies match frozen hashes; edit/test/finish appear in every arm's reconstructed definitions. This is not proof of what the historical client received. New broker instrumentation records actual initialization, discovery and notification messages with their exact replies in `mcp-requests.jsonl.control.jsonl`, separately from action requests. Tests verify the expected arm-specific capabilities, notifications, unchanged discovery replies, and zero action consumption. No historical evidence is backfilled.

For v37's 13 completed attempts, the visible inventory is 23,831 cl100k tokens of saved prompts, 2,821 of saved instructions, 6,862 of canonically serialized reconstructed tool definitions, 75,311 of native request parameters and 140,285 of actual native reply text. Orientation is already contained in saved prompts when applicable. These fields describe different serialization boundaries and are not provider billing categories. The inventory has no provider framing, hidden prompt, per-request replay or cache attribution; do not subtract it from the 2,767,997 provider-reported tokens to label an overhead source or estimate savings. Actual historical schema delivery remains unmeasured.

V37 contains 11 repeated retrieved unit headers and 63 repeated delivered read lines; v36 contains three and 165 respectively. Neither batch contains a completely identical retrieval reply. Unit headers can recur with different surrounding text or changed source. Read-line repetition is keyed by file, source hash, line and delivered text, not requested ranges. These differently sized batches are inventory diagnostics, not an efficiency comparison, and repeated text is not measured recoverable token savings.

All 164 Python tests pass, including source-version-aware line counting and discovery logging. No solver instructions, retrieval ranking, budgets or acceptance criteria changed. The broker implementation now differs from the v37 freeze, so another run would require a new recorded plan and immutable snapshots. Both promotion gates remain unpassed, v37 remains stopped with five missing cases, and no new model calls were made.

### Discovery validation integrity follow-up (2026-10-23)

Future native trials now require captured broker `tools/list` replies matching the arm's complete expected definitions, including descriptions and input schemas. Missing, malformed, failed, paginated, duplicate or mismatched advertisements make the trial protocol-invalid and stop the batch; a token-threshold error cannot hide this failure. Successful discovery records are stored in each trial summary. This establishes what the broker advertised, not whether the client consumed or understood it. Historical readiness semantics and recorded failures remain unchanged.

The offline context audit now inventories captured definitions directly without requiring current schema files to match historical ones. Only absent historical captures permit reconstruction, still guarded by both frozen schema dependency hashes. Invalid captures cannot silently fall back to reconstruction. Captured and reconstructed inventories have separate fields; unavailable values remain null, and totals include the number of cases contributing to each field. Repeated discovery replies each contribute their serialized definitions, without implying provider replay or token savings.

All 171 Python tests pass, covering each arm, schema/envelope corruption, missing captures, historical reconstruction, direct captured-schema accounting and the versioned readiness requirement. Both historical inventories reproduced with their original counts; existing audit files and immutable snapshots were preserved. See [offline integrity results](../results/codex-discovery-integrity.json). `run_codex_mcp_batch.py --dry-run` exits nonzero with the explicit requirement for a new freeze containing `require_tool_discovery: true` and the validator's implementation hash. This is expected: v37 remains stopped and incomplete. No model, verifier or retrieval backend was invoked; existing retrieval acceptance/decision artifacts remain unchanged. Both promotion gates remain unpassed.

### Graphify progress report (2026-10-23)

The [reproducible progress report](../results/codex-graphify-progress.json) derives shared totals from the original v36/v37 reports and frozen specs, preserving their hashes, every trial summary, and all missing cases. Reproduce with `.research/venv/bin/python benchmark/report_codex_comparison.py --output results/codex-graphify-progress.json`. It does not invoke a solver or retrieval backend, change the preregistration, or overwrite any historical evidence or acceptance decision. Duplicate/unplanned cases are rejected; incomplete usage stays unknown. Failed and protocol-invalid attempts still contribute to shared totals. A group enters shared totals only when all three planned arms are recorded.

| Separate development diagnostic | graf correct | Pinned code-only Graphify plus reads correct | graf reported tokens | Graphify reported tokens |
| --- | ---: | ---: | ---: | ---: |
| v36, last complete batch | 3/6 | 4/6 | 1,070,662 | 907,362 |
| v37, complete shared groups only | 1/4 | 2/4 | 892,301 | 1,034,234 |

Graf uses 18.0% more reported tokens with fewer correct repairs in v36. V37 uses 13.7% fewer tokens in its four complete groups, also with fewer correct repairs; five planned trials are missing. These are separate configurations on exposed development tasks, not pooled evidence or a causal test of feedback. Cached input is included. Provider internal requests/charges remain unknown, historical discovery is reconstructed, solver readiness fails, and no product or agent-efficiency promotion follows.

The next substantive improvement must address useful repair completion under a bounded context budget. Relevant code was already visible in most failed JavaScript attempts, so improving file discovery alone is unlikely to resolve the observed failures. A uniform workflow hypothesis about acting on located code and failing-test evidence should be isolated before another separately frozen, cheap-model paired diagnostic. Avoid more model spending on the unchanged v37 configuration. Retrieval quality also remains an independent blocker: the saved 2K development diagnostic has default locate recall 94.44% versus Graphify 96.30%; its 8K realized output budgets differ, preventing equal-budget superiority claims. See [retrieval evidence and caveats](audit-repairs-2026-10-22.md).

### Comparator source audit caveat (2026-10-23)

A [source audit of pinned Graphify](next-experiments.md#pinned-graphify-source-audit-2026-10-23) found that our query adapter is not native-CLI-equivalent: it omits the CLI loader's edge-direction markers, uses library-default traversal depth 3 instead of CLI depth 2, and omits the graph-path header. A two-node fixture reproduces reversed caller/callee text through the adapter while the pinned CLI renders the stored call direction correctly. [Raw commands, outputs and hashes](../results/graphify-source-audit-2026-10-23.json) are preserved; the integrity diagnostic exits 1. All 95 previously recorded Graphify Python source hashes still match the frozen baseline.

The v36/v37 verifier outcomes and token totals remain observed results under the adapter configuration recorded for those experiments. They do not establish native-CLI parity or full Graphify product performance. There is no evidence attributing the repair losses to this mismatch. Resolve comparator invocation parity under a new recorded adapter revision before another model batch; retain all historical cases and the existing Graphify commit. No model calls, product changes, baseline replacements or paired efficiency evaluations were made for this source audit. Existing acceptance/decision artifacts remain unchanged and unpassed.

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
