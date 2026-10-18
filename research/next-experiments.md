# Proposed next experiments

**Status:** the evaluation design below was adopted on 2026-10-18 as the [decision rule](protocol.md#decision-rule) and frozen in [preregistration.json](../benchmark/preregistration.json). The candidate interventions are planned experiments, not accepted changes. Evidence and limitations for each proposal are in [evidence.md](evidence.md#evidence-for-the-next-retrieval-decisions).

## Why the current gate cannot be passed by retrieval changes alone

The latest [acceptance report](../results/acceptance.json) blocks `search` on 53 of 60 cases and `locate` on 31 of 60. At 2K, every `search` case and 25 `locate` cases lose on delivered tokens. graf packs until the budget is full (median about 1,990 tokens), while Graphify's native output is shorter (median about 1,808). Better ranking cannot fix that: a packer that fills the budget always delivers more tokens than a shorter output. `locate` at 8K fails on 6 cases, 5 of them on recall among the first five files, which is an ordering problem.

The gate also has statistical limits. It requires zero regression on each of 60 cases, judged by incomplete, author-selected labels. The two budgets share their queries, so there are 30 independent units, and those 30 queries have already been used for tuning. No statistical procedure can confirm a zero difference, so a better system can fail through label noise alone, and passing would not establish superiority.

## Candidate interventions

Arms A1, A2, and B1 are implemented behind opt-in CLI flags and run as separate arms by `benchmark/compare_graphify.py` (preregistration version 4). B2 and C1–C3 change the index and remain to be implemented. Test one factor at a time against current graf and pinned Graphify. Freeze every parameter below before any held-out run. The values come from the cited literature or are round defaults, not tuning on the 30 exploratory queries. Changing a value after seeing held-out results creates a new, separately labeled experiment.

### A. Stopping rule (affects delivered tokens)

| Arm | Rule | Source of the idea | Falsified when |
| --- | --- | --- | --- |
| A0 | Current: pack until the budget is full | — | — |
| A1 | Largest relative gap: over sorted candidate scores s₁…sₙ, cut at the largest (sᵢ − sᵢ₊₁)/s₁ among the first ⌈0.9n⌉ positions, with at least 3 results, then add 2 more; the budget remains a hard cap | Adaptive-k (Taguchi et al. 2025) | Quality is worse than A0 beyond the non-inferiority margin, or delivered tokens do not fall |
| A2 | Relative score floor: stop at the first candidate scoring below 0.5·s₁ | Score-distribution truncation (Arampatzis et al. 2009), simplified | As for A1 |

For `locate`, apply the rule to file scores. Report the cut position for every case. Adaptive-k kept about 70% of relevant passages on QA, so a recall loss is the expected risk.

### B. File ranking for `locate` (affects ordering)

| Arm | File score | Source of the idea | Falsified when |
| --- | --- | --- | --- |
| B0 | Current: best window score (max) | SweRank's max aggregation; Dai and Callan | — |
| B1 | Damped top three: s₁ + 0.5·s₂ + 0.25·s₃ | Akkalyoncu Yilmaz et al. 2019 | No gain in recall among the first five files, or the gain tracks file length |
| B2 | Path field: BM25F with path weight 3 and window text weight 1, frequencies combined before saturation | Robertson and Zaragoza 2009; BLUiR | No gain, or losses where query terms are generic path words |

B2 changes the index schema and requires a rebuild path.

### C. Tokenization (affects matching)

| Arm | Change | Source of the idea | Falsified when |
| --- | --- | --- | --- |
| C0 | Current list, including request-phrasing words such as `test` and `implementation` | — | — |
| C1 | A published general English stopword list, applied to queries and documents | BLUiR; Fan et al. 2023 | Quality is worse than C0 beyond the margin |
| C2 | No stopwords; BM25's IDF alone down-weights common terms | BLUiR | As for C1 |
| C3 | C1 plus the lowercased full compound identifier indexed alongside its parts | BLUiR | No gain, or document-length effects reduce quality |

## Evaluation design (adopted)

Adopted 2026-10-18. Parameters the proposal gave only as examples were fixed in `benchmark/preregistration.json` on the same day, before any held-out data existed.

1. **Split the queries.** The 30 existing queries become a development and pilot set. They are used only to estimate the variance of paired differences.
2. **Held-out set.** Use independently authored requests, frozen before any arm runs. Size the set from the pilot variances, with a floor of 70. The first pilot (`results/decision.json`, development workload) found per-query recall differences with standard deviations up to about 0.41, because most queries label one or two code files. At the preregistered 0.05 margin, that implies roughly 410–430 queries. Preregistration version 3 therefore widens the margin to 0.10, which implies about 105–110 queries; the held-out minimum is 110. These are normal-approximation planning estimates, not cited results. Treat budgets as a repeated factor, not as extra queries.
3. **Judgments.** Pool the union of the top ten files from every arm and Graphify. A judge who did not tune graf, and who does not see which system returned each file, labels the pool. Report the unjudged fraction.
4. **Primary metric and test.** Preregister one primary efficiency metric, for example delivered tokens at a stated budget. Test the per-query mean difference with a one-sided paired permutation test at α = 0.05.
5. **Quality guardrails.** Use non-inferiority tests with fixed margins on code-file recall and recall among the first five and ten files, with Holm correction across guardrails.
6. **No hidden losses.** Publish every case. Add a tail guardrail: at most a preset share of queries may suffer a severe loss, such as losing every labeled file. Report a confidence interval for that share.
7. **Latency.** Use 10–20 alternating fresh-process invocations per arm and case, with separate cold-cache and warm-cache conditions. Report the median with a bootstrap interval.
8. **Agent trials.** Run the same solver, model, and spend caps for every arm. Count every attempt. Use at least 20 tasks with at least 5 paired trials per task and arm. Report success with Wilson intervals, report pass^k, and require non-inferiority on success with superiority on total cost. Natural held-out issues are still required before any product claim.
9. **Comparators.** Run against pinned Graphify and the last accepted graf version, as AGENTS.md already requires.

The current design (30 tuned queries, 3 timing samples, 6 × 3 planned agent trials) remains useful for diagnostics, variance pilots, and detecting gross failures. It cannot support superiority, latency, or agent-efficiency claims.

## Decisions needed

1. Who authors and judges the held-out requests.
2. The agent solver, model, and spend caps.
