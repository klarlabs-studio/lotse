# Paired agent trials

Two preregistered batches over the same 20 synthetic fault-repair tasks, solver, and spend cap. Batch `t2` is reported first.

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

Batch `t2` tested the smaller payload and the search-only control. The open question is whether graf beats search and reads at all. Answering it needs harder, natural held-out tasks, with `search_only` preregistered as a comparator rather than a control. For retrieval, any floor-stop variant must still pass the recall guardrails on a new held-out query set.
