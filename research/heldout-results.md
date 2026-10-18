# Held-out retrieval decision (first run)

**Outcome: no arm passes the preregistered decision rule. Nothing is promoted.** Every case, metric, and test is in [decision.json](../results/decision.json). The rule is [preregistration v9](../benchmark/preregistration.json).

## Conditions

- **Workload.** 123 frozen held-out queries, 111 with code evidence, over the pinned Cobra, Flask, and Fastify snapshots, at 2K and 8K budgets. Three Claude subagents drafted them from source alone, with no access to graf, Graphify, or retrieval output. This is weaker independence than human authoring.
- **Relevance.** Author labels plus 134 files judged relevant out of 1,751 pooled from the top ten of every tool. The judges were blind to which system cited a file. An operator audit of 12 judgments agreed on 11.
- **Comparator.** Pinned code-only Graphify. There is no accepted graf version yet.
- **Run.** One invocation per tool and case, under machine load. Tokens and recall are deterministic and audited. Latency is not reported from this run.

## Results

The primary metric is mean delivered-token reduction against Graphify at 2K. A positive value means graf delivers fewer tokens. The primary α is 0.05 for the accepted arms and 0.05/11 for each experimental arm. Guardrails are one-sided non-inferiority tests with a 0.10 margin, Holm-corrected, on code-file recall and on recall among the first five and first ten files, each at both budgets.

| Arm | Token reduction at 2K | Primary | Guardrails non-inferior | Severe-loss queries |
| --- | ---: | --- | ---: | ---: |
| `locate` (accepted) | −168.7 | fail | 6/6 | 0 |
| `locate --file-score damped` | −169.3 | fail | 6/6 | 0 |
| `locate --file-score damped --stop floor` | +1,169.2 | pass | 5/6 | 1 |
| `locate --stop floor` | +1,162.5 | pass | 4/6 | 2 |
| `locate --file-score damped --stop gap` | +1,517.7 | pass | 2/6 | 6 |
| `locate --stop gap` | +1,529.5 | pass | 2/6 | 9 |
| `locate` (path weight 3) | −169.0 | fail | 6/6 | 0 |
| `locate` (English stopwords) | −184.3 | fail | 6/6 | 0 |
| `locate` (English stopwords + full identifiers) | −184.8 | fail | 6/6 | 0 |
| `locate` (no stopwords) | −198.5 | fail | 2/6 | 0 |
| `search` (accepted) | −178.5 | fail | 3/6 | 4 |
| `search --stop gap` | +560.9 | pass | 0/6 | 13 |
| `search --stop floor` | −44.4 | fail | 2/6 | 6 |

## Interpretation within these conditions

- Accepted `locate` was non-inferior to Graphify on every quality guardrail. Its mean code-file recall was higher by 0.15 at 2K and 0.10 at 8K. It still delivers more tokens than Graphify, because it fills the budget.
- Stopping rules cut about 1,100–1,500 tokens per query at 2K, but they lose recall. The loss is mainly in total recall at 8K, where Graphify's output is longest. The closest arm, damped scoring with the floor stop, failed only that guardrail: −0.122 against a −0.10 margin.
- The analyzer arms did not improve ranking on held-out queries. The current stopword list stays.
- These results cover code-file discovery on three repositories. They do not measure answer quality or agent task efficiency. The paired agent trials are a separate gate.

## Next steps

A changed threshold or a new arm must be preregistered and evaluated on a new held-out set. Tuning on this set would contaminate it.
