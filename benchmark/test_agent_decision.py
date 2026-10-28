"""The agent-trial rule must fail closed, count every attempt, and pair by task."""
import json
from pathlib import Path
import unittest

import agent_decision

from agent_decision import evaluate, pass_hat_k

BASE = Path(__file__).resolve().parents[1]
AGENT = json.loads((BASE / "benchmark/preregistration.json").read_text())["agent_trials"]
RULE = AGENT["rule"]
TASKS = [f"task{i:02d}" for i in range(20)]
PAIR = {"arms": ["graf", "graphify"], "candidates": ["graf"], "controls": [], "comparator": "graphify"}
TRIO = {"arms": ["graf_floor", "search_only", "graphify"], "candidates": ["graf_floor"],
        "controls": ["search_only"], "comparator": "graphify"}
NATURAL = {"arms": ["graf_floor", "search_only", "graphify"], "candidates": ["graf_floor"], "controls": [],
           "comparators": ["search_only", "graphify"]}


def summary(task, arm, k, passed=True, cost=0.004, tokens=80_000):
    return {"task": task, "arm": arm, "trial": f"t-{task}-{arm}-{k}", "passed": passed,
            "usage_complete": True, "infrastructure_error": False,
            "tool_binary_sha256": arm[0] * 64,
            "audit": {"total_tokens": tokens, "cost_usd": cost, "unmeasured_fields": []}}


def batch(graf_cost=0.003, graphify_cost=0.004, graf_pass=lambda t, k: True, costs=None):
    costs = costs or {"graf": graf_cost, "graphify": graphify_cost}
    rows = []
    for task in TASKS:
        for k in range(1, 6):
            for arm, cost in costs.items():
                passed = graf_pass(task, k) if arm != "graphify" else True
                rows.append(summary(task, arm, k, passed, cost))
    return rows


class PassHatKTests(unittest.TestCase):
    def test_a_reduced_batch_scores_only_its_planned_attempts(self):
        spec = {"minimum_paired_trials_per_task_and_arm": 5}
        self.assertEqual(agent_decision.attempts(spec, {}), 5)
        self.assertEqual(agent_decision.attempts(spec, {"attempts_per_task_and_arm": 2}), 2)
        rows = [{"trial": f"b-t-a-{k}"} for k in (1, 2, 3, 5)]
        kept, excluded = agent_decision.planned_attempts(rows, 2)
        self.assertEqual([r["trial"] for r in kept], ["b-t-a-1", "b-t-a-2"])
        self.assertEqual(excluded, 2)

    def test_unbiased_estimator(self):
        self.assertEqual(pass_hat_k(5, 5, 5), 1.0)
        self.assertEqual(pass_hat_k(4, 5, 5), 0.0)
        self.assertAlmostEqual(pass_hat_k(4, 5, 2), 6 / 10)
        self.assertAlmostEqual(pass_hat_k(3, 5, 1), 0.6)


class AgentDecisionTests(unittest.TestCase):
    def test_batches_are_preregistered(self):
        self.assertEqual({k: AGENT["batches"]["t"][k] for k in PAIR}, PAIR)
        self.assertEqual({k: AGENT["batches"]["t2"][k] for k in TRIO}, TRIO)
        self.assertEqual({k: AGENT["batches"]["t3"][k] for k in NATURAL}, NATURAL)
        self.assertEqual(AGENT["batches"]["t3"]["catalog"], "benchmark/natural-tasks.json")
        t5 = AGENT["batches"]["t5"]
        self.assertEqual((t5["candidates"], t5["comparators"]), (["graf_orient"], ["graphify_orient", "search_only"]))
        self.assertEqual(t5["catalog"], AGENT["batches"]["t4"]["catalog"])
        from task_session import ARMS
        self.assertTrue(set(t5["arms"]) <= set(ARMS))

    def test_a_candidate_must_beat_every_comparator(self):
        beats_one = batch(costs={"graf_floor": 0.003, "search_only": 0.002, "graphify": 0.004})
        result = evaluate(beats_one, TASKS, 5, RULE, NATURAL)
        versus = result["comparisons"]["graf_floor"]["versus"]
        self.assertTrue(versus["graphify"]["passed"])
        self.assertFalse(versus["search_only"]["passed"])
        self.assertFalse(result["passed"])
        self.assertEqual(versus["graphify"]["primary"]["alpha"], RULE["primary"]["alpha"])
        beats_both = batch(costs={"graf_floor": 0.002, "search_only": 0.003, "graphify": 0.004})
        self.assertTrue(evaluate(beats_both, TASKS, 5, RULE, NATURAL)["passed"])

    def test_cheaper_and_equally_successful_passes(self):
        result = evaluate(batch(), TASKS, 5, RULE, PAIR)
        self.assertTrue(result["passed"], result["reasons"])
        self.assertEqual(result["arms"]["graf"]["attempts"], 100)
        self.assertEqual(len(result["tasks"]), 20)

    def test_more_expensive_candidate_fails_primary(self):
        result = evaluate(batch(graf_cost=0.005), TASKS, 5, RULE, PAIR)
        self.assertFalse(result["comparisons"]["graf"]["versus"]["graphify"]["primary"]["passed"])
        self.assertFalse(result["passed"])

    def test_success_loss_beyond_margin_fails_guardrail(self):
        result = evaluate(batch(graf_pass=lambda t, k: k > 2), TASKS, 5, RULE, PAIR)  # 60% vs 100%
        self.assertFalse(result["comparisons"]["graf"]["versus"]["graphify"]["success"]["passed"])
        self.assertFalse(result["passed"])

    def test_failed_attempts_still_count_toward_cost(self):
        rows = batch()
        rows[0] = summary(TASKS[0], "graf", 1, passed=False, cost=0.5)
        result = evaluate(rows, TASKS, 5, RULE, PAIR)
        task = next(t for t in result["tasks"] if t["task"] == TASKS[0])
        self.assertAlmostEqual(task["graf"]["mean_cost_usd"], (0.5 + 4 * 0.003) / 5)

    def test_a_winning_control_cannot_pass_the_batch(self):
        rows = batch(costs={"graf_floor": 0.005, "search_only": 0.002, "graphify": 0.004})
        result = evaluate(rows, TASKS, 5, RULE, TRIO)
        self.assertTrue(result["comparisons"]["search_only"]["passed"])
        self.assertEqual(result["comparisons"]["search_only"]["role"], "control")
        self.assertFalse(result["passed"])
        rows = batch(costs={"graf_floor": 0.002, "search_only": 0.005, "graphify": 0.004})
        self.assertTrue(evaluate(rows, TASKS, 5, RULE, TRIO)["passed"])

    def test_alpha_is_split_across_every_tested_arm(self):
        result = evaluate(batch(costs={"graf_floor": 0.003, "search_only": 0.003, "graphify": 0.004}),
                          TASKS, 5, RULE, TRIO)
        self.assertAlmostEqual(result["comparisons"]["graf_floor"]["versus"]["graphify"]["primary"]["alpha"],
                               RULE["primary"]["alpha"] / 2)
        self.assertAlmostEqual(result["comparisons"]["graf_floor"]["versus"]["graphify"]["success"]["alpha"],
                               RULE["success"]["alpha"] / 2)

    def test_a_solver_timeout_counts_at_its_least_favorable_cost_bound(self):
        def timed_out(row):
            return {**row, "passed": False, "cost_bounds_usd": [0.001, 0.5],
                    "audit": {**row["audit"], "cost_usd": 0.001, "unmeasured_fields": ["reasoning_tokens"]}}
        rows = batch()
        rows[0], rows[1] = timed_out(rows[0]), timed_out(rows[1])  # task00 attempt 1 of graf, then graphify
        result = evaluate(rows, TASKS, 5, RULE, PAIR)
        self.assertFalse(any("usage" in r for r in result["reasons"]), result["reasons"])
        task = next(t for t in result["tasks"] if t["task"] == TASKS[0])
        self.assertAlmostEqual(task["graf"]["mean_cost_usd"], (0.5 + 4 * 0.003) / 5)
        self.assertAlmostEqual(task["graphify"]["mean_cost_usd"], (0.001 + 4 * 0.004) / 5)
        self.assertEqual([b["counted_usd"] for b in result["solver_timeouts_least_favorable"]], [0.5, 0.001])

    def test_only_reasoning_may_be_unmeasured_in_a_bounded_timeout(self):
        rows = batch()
        rows[0] = {**rows[0], "cost_bounds_usd": [0.001, 0.5],
                   "audit": {**rows[0]["audit"], "unmeasured_fields": ["cached_input_tokens", "reasoning_tokens"]}}
        result = evaluate(rows, TASKS, 5, RULE, PAIR)
        self.assertTrue(any("usage" in r for r in result["reasons"]))

    def test_missing_trial_or_incomplete_usage_blocks(self):
        result = evaluate(batch()[1:], TASKS, 5, RULE, PAIR)
        self.assertFalse(result["passed"])
        self.assertTrue(any("missing" in r for r in result["reasons"]))
        rows = batch()
        rows[3] = {**rows[3], "usage_complete": False, "audit": None}
        result = evaluate(rows, TASKS, 5, RULE, PAIR)
        self.assertFalse(result["passed"])
        self.assertTrue(any("usage" in r for r in result["reasons"]))

    def test_infrastructure_errors_or_changed_binaries_block(self):
        rows = batch()
        rows[5] = {**rows[5], "infrastructure_error": True}
        result = evaluate(rows, TASKS, 5, RULE, PAIR)
        self.assertFalse(result["passed"])
        self.assertTrue(any("infrastructure" in r for r in result["reasons"]))
        rows = batch()
        rows[2] = {**rows[2], "tool_binary_sha256": "b" * 64}
        result = evaluate(rows, TASKS, 5, RULE, PAIR)
        self.assertFalse(result["passed"])
        self.assertTrue(any("binary" in r for r in result["reasons"]))
        legacy = [{k: v for k, v in r.items() if k != "infrastructure_error"} for r in batch()]
        self.assertFalse(evaluate(legacy, TASKS, 5, RULE, PAIR)["passed"])

    def test_duplicate_or_unplanned_trial_blocks(self):
        rows = batch()
        self.assertFalse(evaluate(rows + [rows[0]], TASKS, 5, RULE, PAIR)["passed"])
        self.assertFalse(evaluate(rows + [summary("extra", "graf", 1)], TASKS, 5, RULE, PAIR)["passed"])
        self.assertFalse(evaluate(rows + [summary(TASKS[0], "graf_floor", 1)], TASKS, 5, RULE, PAIR)["passed"])


if __name__ == "__main__":
    unittest.main()
