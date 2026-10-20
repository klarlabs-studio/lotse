"""The agent-trial rule must fail closed, count every attempt, and pair by task."""
import json
from pathlib import Path
import unittest

from agent_decision import evaluate, pass_hat_k

BASE = Path(__file__).resolve().parents[1]
RULE = json.loads((BASE / "benchmark/preregistration.json").read_text())["agent_trials"]["rule"]
TASKS = [f"task{i:02d}" for i in range(20)]


def summary(task, arm, k, passed=True, cost=0.004, tokens=80_000):
    return {"task": task, "arm": arm, "trial": f"t-{task}-{arm}-{k}", "passed": passed,
            "usage_complete": True, "infrastructure_error": False,
            "tool_binary_sha256": "a" * 64,
            "audit": {"total_tokens": tokens, "cost_usd": cost, "unmeasured_fields": []}}


def batch(graf_cost=0.003, graphify_cost=0.004, graf_pass=lambda t, k: True):
    rows = []
    for task in TASKS:
        for k in range(1, 6):
            rows.append(summary(task, "graf", k, graf_pass(task, k), graf_cost))
            rows.append(summary(task, "graphify", k, True, graphify_cost))
    return rows


class PassHatKTests(unittest.TestCase):
    def test_unbiased_estimator(self):
        self.assertEqual(pass_hat_k(5, 5, 5), 1.0)
        self.assertEqual(pass_hat_k(4, 5, 5), 0.0)
        self.assertAlmostEqual(pass_hat_k(4, 5, 2), 6 / 10)
        self.assertAlmostEqual(pass_hat_k(3, 5, 1), 0.6)


class AgentDecisionTests(unittest.TestCase):
    def test_cheaper_and_equally_successful_passes(self):
        result = evaluate(batch(), TASKS, 5, RULE)
        self.assertTrue(result["passed"], result["reasons"])
        self.assertEqual(result["arms"]["graf"]["attempts"], 100)
        self.assertEqual(len(result["tasks"]), 20)

    def test_more_expensive_candidate_fails_primary(self):
        result = evaluate(batch(graf_cost=0.005), TASKS, 5, RULE)
        self.assertFalse(result["primary"]["passed"])
        self.assertFalse(result["passed"])

    def test_success_loss_beyond_margin_fails_guardrail(self):
        fails = batch(graf_pass=lambda t, k: k > 2)  # 60% vs 100%
        result = evaluate(fails, TASKS, 5, RULE)
        self.assertFalse(result["success"]["passed"])
        self.assertFalse(result["passed"])

    def test_failed_attempts_still_count_toward_cost(self):
        rows = batch()
        rows[0] = summary(TASKS[0], "graf", 1, passed=False, cost=0.5)
        result = evaluate(rows, TASKS, 5, RULE)
        task = next(t for t in result["tasks"] if t["task"] == TASKS[0])
        self.assertAlmostEqual(task["graf"]["mean_cost_usd"], (0.5 + 4 * 0.003) / 5)

    def test_missing_trial_or_incomplete_usage_blocks(self):
        result = evaluate(batch()[1:], TASKS, 5, RULE)
        self.assertFalse(result["passed"])
        self.assertTrue(any("missing" in r for r in result["reasons"]))
        rows = batch()
        rows[3] = {**rows[3], "usage_complete": False, "audit": None}
        result = evaluate(rows, TASKS, 5, RULE)
        self.assertFalse(result["passed"])
        self.assertTrue(any("usage" in r for r in result["reasons"]))

    def test_infrastructure_errors_or_changed_binaries_block(self):
        rows = batch()
        rows[5] = {**rows[5], "infrastructure_error": True}
        result = evaluate(rows, TASKS, 5, RULE)
        self.assertFalse(result["passed"])
        self.assertTrue(any("infrastructure" in r for r in result["reasons"]))
        rows = batch()
        rows[2] = {**rows[2], "tool_binary_sha256": "b" * 64}
        result = evaluate(rows, TASKS, 5, RULE)
        self.assertFalse(result["passed"])
        self.assertTrue(any("binary" in r for r in result["reasons"]))
        legacy = [{k: v for k, v in r.items() if k != "infrastructure_error"} for r in batch()]
        self.assertFalse(evaluate(legacy, TASKS, 5, RULE)["passed"])

    def test_duplicate_or_unplanned_trial_blocks(self):
        rows = batch()
        self.assertFalse(evaluate(rows + [rows[0]], TASKS, 5, RULE)["passed"])
        self.assertFalse(evaluate(rows + [summary("extra", "graf", 1)], TASKS, 5, RULE)["passed"])


if __name__ == "__main__":
    unittest.main()
