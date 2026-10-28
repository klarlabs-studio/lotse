# Held-out retrieval decision (first run)

**Outcome: no arm passes the preregistered decision rule. Nothing is promoted.** Every case, metric, and test is in [decision.json](../results/decisions/held_out-12c303a70d9347be5c8090c2c661a147a9971a290a81528b2a7d23cad5cef2e3.json). The rule is [preregistration v9](../results/preregistrations/45c65b0831dd4bd191aa7d7d6a80ae155172d3ce34b3b6099145311adfd6ca24.json).

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


## v62: fresh confirmation preparation (no retrieval run)

Prepared a source-only question-authoring packet for newly pinned Zerolog
(`20ca4734f29c37a6781c3c502442c349b306dee3`, Go), Rich
(`9d8f9a372cc5916fd4781fec207ced7ddac2f08f`, Python), and Pino
(`b29dbe97820630e7128e464c50263b427965ec99`, JavaScript). These identities
are absent from the recorded prior benchmark input/snapshot inventory; this
local check is not proof of global independence. Public archives and hashes
are retained. The packet contains 581 source files,
file hashes and authoring instructions, with no retrieval outputs, candidate
implementation or tuning notes. Packet hash: `22a032fa5959f0357cbc893308b8e43f544f376da6cde9ddd0fbd1f53f1c457a`.

The prospective plan targets 123 questions, 111 with code evidence, satisfying
the current v61/v59 pilot minimum of 110 scored queries. It retains the 32-arm
historical primary correction, six Holm quality guardrails and severe-loss
cap. V61 is the candidate, v59 an experimental ablation control, and unchanged
pinned code-only Graphify the comparator; no accepted graf release exists.
Author-selected anchors remain exploratory and are not shared Graphify quality.

Added `benchmark/confirmation.py` to export the isolated packet and validate
question schema, repository quotas, duplicate/historical questions, exact
source hashes/anchors, provenance declarations, negative-control rationales,
and the scored-query minimum. It rejects altered packet requirements and
source/symlink substitution. A declaration cannot prove independence or
relevance completeness; a separate source review and later blind pooled
judgments remain required. All 199 Python checks pass (14 new packet checks).

**Preparation complete; question workload not frozen, comparison not run.**
Independent source-only authorship and review are still pending. The existing
comparison driver is restricted to the original three repositories and must
be generalized, tested and frozen with the new question hash before collection.
The old held-out workload is preserved and must not be relabeled fresh.
No retrieval implementation/default change, no new acceptance decision, no
agent model calls, and no new performance claims. V61 remains experimental;
the existing `acceptance.json` and `decision.json` remain not_passed.

Spec: `benchmark/confirmation-v62.json`; source packet:
`.research/heldout-v62-authoring/`; readiness:
`results/confirmation-v62-readiness.json`; frozen preparation:
`results/preregistrations/v62-855a0150cf711bac94faac17433c3910cbcac28ad1d720037f6f9694ae114534.json`.


### v63: generalized harness parity, not confirmation

The authenticated run-plan driver supports frozen repository manifests, questions, source manifests, per-arm binaries, selected arms and outputs. A three-arm run on the exposed 30-question development workload compared frozen Graf v61 selective evidence, v59 rescue control and pinned native code-only Graphify. All 120 Graf query/budget/arm contexts match the saved v61 run byte for byte. The comparison audit passed for 60 query/budget cases and 180 timing samples. The Python suite passed 211 tests.

At 2K, mean output tokens were v61 1631.43, v59 1557.33 and Graphify 2410.37; shared code-file hint recall was 98.15%, 98.15% and 87.04%. At 8K, tokens were 3985.40, 3896.30 and 13000.93, with the same recall. Graphify raw scratch-path headers differ from prior runs and remain preserved. These are development diagnostics; no fresh-workload or agent-efficiency conclusion follows. Graphify navigation hints do not establish source-anchor or documentation-task performance.

`acceptance.py` and `decision.py` both returned 1 (`not_passed`), preserved in `results/harness-v63-{acceptance,decision}.json` and current aliases. No accepted Graf release exists. One sequential repetition, fresh indexes/subprocesses, uncontrolled OS caches; latency inference disabled. Frozen inputs and tool hashes are in preregistration v63 and `benchmark/harness-v63-plan.json`. Raw outputs, commands and timings are in `results/harness-v63-comparison.json`; audit/report and execution logs are retained.


### v64: fresh source-reviewed workload frozen before retrieval

Three isolated GPT-6-luna authors drafted 41 questions per repository for the new pinned Zerolog, Rich and Pino sources. A first full-batch author attempt failed; it is retained in provenance. Three different fresh GPT-6-luna sessions reviewed every question using only the source packet, with follow-up repairs. This AI separation is weaker evidence of independence than independent human authoring. Model token/spend accounting is unavailable from collaboration tools; these authoring calls are not an agent-efficiency benchmark.

The mechanical freeze gate rejected an intermediate submission: 30 Zerolog anchors did not occur on their start line, and 37 Pino positive result labels had been replaced with answer text. Source reviewers corrected both, and the successful freeze validates every field, range, anchor, source hash, category quota and historical exact-question duplicate check. All source-level review blockers are resolved. The Pino review retains the caveat that two tasks examine literal documented/internal default values without showing a user-visible behavior difference. Original submissions, review changes and rejected staging are preserved.

Preregistration v64 freezes 123 questions (111 code-scored; minimum/pilot requirement 110), 581 supported source files, the unchanged v61 candidate/v59 experimental control binaries, pinned native code-only Graphify, shared cl100k 2K/8K budgets, one sequential paired repetition and resource limits. No latency inference. All fresh input and tool hashes are authenticated by `benchmark/confirmation-v64/run-plan.json`; source sets match the exported packet exactly. Query SHA-256: `cbbdef34c9d16d5e9ffbfda1969914ff0fbaa8f5953a2408ca3b096b0d8d9f29`.

The frozen comparison completed with 246 query/budget cases and 738 timing samples; the output audit passed. `acceptance.py` returned 1 (`not_passed`), including 74 v61 case regressions under the initial author-label diagnostic. Blind pooled source judgments are required before final quality scoring. The first full-repository judging drafts failed substantive semantic review despite complete JSON coverage; those attempts are preserved and excluded. Replacement isolated cheap-model sessions review smaller source-only batches. A premature `decision.py` invocation returned 1 because completed pooled judgments were absent; the prior v63 decision alias remained unchanged. Agent savings remain unmeasured and product promotion is blocked. See `results/confirmation-v64-readiness.json` and `.research/evidence/confirmation-v64-run/`.


### v64 result: retrieval rule passed, product promotion blocked

The frozen comparison and blind pooled review are complete: 123 questions (111 code-scored), three pinned repositories, 246 query/budget cases, 738 timing samples and 1,575 judged query/file pairs. Source/output audit and pool checks passed. Raw outputs, commands, every timing sample, build records, source/input hashes and cache conditions remain preserved in the comparison and execution evidence. No retrieval implementation or default changed.

Both v61 and v59 passed the preregistered held-out retrieval rule. At the primary 2K budget, v61's mean paired token reduction was 501.24 tokens (24.4% of Graphify's mean), permutation p ≈ 0.000010 versus family-adjusted alpha 0.0015625. All six Holm-corrected non-inferiority guardrails passed at the frozen 0.10 margin. Neither Graf arm had a preregistered severe loss (zero total file recall where Graphify's was positive); the 95% Wilson upper bound is 3.35%, below 15%. This is a retrieval-workload result, not product or agent-efficiency acceptance.

| Budget | Arm | Mean output tokens | File recall | Recall@5 | Recall@10 | Budget overruns |
|---|---|---:|---:|---:|---:|---:|
| 2000 | Graf v61 selective | 1549.96 | 93.59% | 57.12% | 80.94% | 0 / 123 |
| 2000 | Graf v59 control | 1504.57 | 93.59% | 57.12% | 80.94% | 0 / 123 |
| 2000 | Pinned Graphify (code-only) | 2051.20 | 81.71% | 52.61% | 69.90% | 19 / 123 |
| 8000 | Graf v61 selective | 3901.97 | 93.89% | 56.67% | 80.94% | 0 / 123 |
| 8000 | Graf v59 control | 3827.30 | 93.89% | 56.67% | 80.94% | 0 / 123 |
| 8000 | Pinned Graphify (code-only) | 12460.76 | 83.66% | 52.61% | 69.90% | 56 / 123 |

Shared quality is code-file hint recall: Graf source excerpts and Graphify navigation hints differ, so these scores establish neither shared source-anchor recall nor answer accuracy. Graphify is the unchanged native code-only baseline; documentation performance is unmeasured. Shared tokenizer and requested budgets were unchanged; Graphify's raw budget overruns are retained, not silently truncated. Fresh indexes/subprocesses, prebuilt unit caches and uncontrolled OS caches; one repetition, no latency inference. Mean token counts include all 123 questions; quality excludes 12 negative controls whose task outcomes are unmeasured.

Aggregate gains do not erase losses. v61 has 118 query/budget cases with a lower quality metric or higher token count: 87 have at least one lower shared quality score (44 unique questions), and 49 have higher token counts, with overlap. All are listed in the report. v59 has 116 such cases (the same 87 quality cases and 47 token cases). Rich all-file recall is lower for Graf: 92.16% versus 92.91% at 2K, and 92.61% versus 97.67% at 8K. Zerolog top-5 recall is also lower, 63.42% versus 65.27%. Neither fact is hidden by the pooled pass.

v59 and v61 have identical shared file-quality metrics in every paired case; v59 emits fewer mean tokens at both budgets (1504.57 versus 1549.96 at 2K; 3827.30 versus 3901.97 at 8K). v61's extra same-file call evidence therefore has no demonstrated benefit on these shared metrics. Source-excerpt usefulness and agent outcomes remain unmeasured; no accepted Graf release exists and both remain experimental.

The first repository-wide AI judging drafts failed semantic review despite complete pair coverage. They were rejected and archived. Replacement source-only cheap-model reviewers completed small disjoint batches; only a certified 10-query Zerolog subset was retained from the original review. Follow-up checks repaired missing partial-mechanism labels, malformed references and a copied wrong-file reason. Exact coverage, all source hashes and cited line bounds passed centrally. These isolated AI judgments are weaker than independent human annotation; declarations and schema checks cannot prove semantic correctness or independence. Requested reviewer model was GPT-6-luna; one original partial reviewer self-reported a conflicting model identity, recorded in provenance rather than treated as verified backend metadata. Provider tokens/spend are unavailable, and annotation attempts are not agent-efficiency trials.

`acceptance.py` returned 1 (`not_passed`): the per-case diagnostic still has regressions. Its frozen generic “not a preregistered held-out workload” blocker is inapplicable to v64 and is explicitly disclosed, not edited after seeing data. `decision.py` first returned 1 without complete judgments, then returned 0 with the certified pool; both invocations are preserved. The final held-out decision is `passed` for both arms, while `product_promotion_allowed` remains false. Agent savings are **unmeasured**. Next freeze fresh natural agent tasks and compare Graf with Graphify plus source reads under identical solver/model/task/spend limits, accounting for all payloads, calls and failures. Any retrieval tuning on these now-exposed questions needs a new unseen confirmation set.

Evidence: [report](../results/confirmation-v64-report.json), [decision](../results/confirmation-v64-decision.json), [per-case diagnostic](../results/confirmation-v64-acceptance.json), [pooled diagnostic](../results/confirmation-v64-pooled-diagnostic.json), raw comparison `results/confirmation-v64-comparison.json` (archived; see [raw-archive.json](../results/raw-archive.json)), [certified batch provenance](../results/confirmation-v64-judging-batches/), [rejected attempts](../results/confirmation-v64-judging-attempts/), and `.research/evidence/confirmation-v64-run/`.
