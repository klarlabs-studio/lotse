"""The preregistered decision rule must fail closed and report every case."""
import copy
import json
from pathlib import Path
import unittest
from tempfile import TemporaryDirectory

from decision import evaluate

BASE = Path(__file__).resolve().parents[1]
PREREG = json.loads((BASE / "benchmark/preregistration.json").read_text())
QUALITY = PREREG["guardrails"]["metrics"]


def row(query, budget, tokens, recall, base_tokens=1800, base_recall=1.0):
    return {"id": query, "budget": budget, "tools": {
        "cand": {**{m: recall for m in QUALITY}, "output_tokens": tokens, "median_ms": 5},
        "graphify": {**{m: base_recall for m in QUALITY}, "output_tokens": base_tokens,
                     "median_ms": 9}}}


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.queries = [f"q{i:02d}" for i in range(40)]
        self.expected = {(q, b): True for q in self.queries for b in (2000, 8000)}
        self.rows = [row(q, b, 1500, 1.0) for q in self.queries for b in (2000, 8000)]

    def run_rule(self, rows=None, role="held_out", expected=None):
        prereg = copy.deepcopy(PREREG)
        prereg["workloads"]["held_out"]["minimum_queries"] = 40
        prereg["workloads"]["held_out"]["pilot_required_queries"] = {}
        return evaluate(self.rows if rows is None else rows,
                        self.expected if expected is None else expected,
                        "cand", prereg, role)

    def test_production_minimum_blocks_an_otherwise_clear_small_win(self):
        rows = self.rows[:60]
        expected = {(r["id"], r["budget"]): True for r in rows}
        result = evaluate(rows, expected, "cand", PREREG, "held_out")
        self.assertEqual(result["status"], "not_passed")
        self.assertTrue(result["primary"]["passed"])
        self.assertTrue(all(g["non_inferior"] for g in result["guardrails"]))
        self.assertIn("preregistered minimum", " ".join(result["blockers"]))

    def test_frozen_pilot_size_is_enforced_for_scored_queries(self):
        prereg = copy.deepcopy(PREREG)
        prereg["workloads"]["held_out"]["minimum_queries"] = 30
        prereg["workloads"]["held_out"]["pilot_required_queries"] = {"cand": 41}
        result = evaluate(self.rows, self.expected, "cand", prereg, "held_out")
        self.assertEqual(result["status"], "not_passed")
        self.assertEqual(result["sample_size"]["required_scored_queries"], 41)
        self.assertIn("Insufficient scored queries", " ".join(result["blockers"]))

    def test_clear_held_out_win_passes_but_never_authorizes_promotion_alone(self):
        result = self.run_rule()
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["product_promotion_allowed"])
        self.assertEqual(len(result["cases"]), 80)

    def test_development_workload_cannot_pass(self):
        result = self.run_rule(role="development")
        self.assertEqual(result["status"], "not_passed")
        self.assertIn("development", " ".join(result["blockers"]))

    def test_small_quality_noise_within_margin_does_not_block(self):
        for r in self.rows[:4]:
            r["tools"]["cand"]["code_file_hint_recall_at_5"] = 0.9
        self.assertEqual(self.run_rule()["status"], "passed")

    def test_systematic_quality_loss_blocks_token_win(self):
        for r in self.rows:
            r["tools"]["cand"]["code_file_hint_recall_at_5"] = 0.8
        result = self.run_rule()
        self.assertEqual(result["status"], "not_passed")
        failed = [g for g in result["guardrails"] if not g["non_inferior"]]
        self.assertTrue(all(g["metric"] == "code_file_hint_recall_at_5" for g in failed))

    def test_severe_losses_on_too_many_queries_block(self):
        for r in self.rows[:16]:
            for m in QUALITY:
                r["tools"]["cand"][m] = 0.0
        result = self.run_rule()
        self.assertFalse(result["tail"]["passed"])
        self.assertEqual(result["status"], "not_passed")

    def test_no_token_reduction_blocks(self):
        for r in self.rows:
            r["tools"]["cand"]["output_tokens"] = 1800
        result = self.run_rule()
        self.assertFalse(result["primary"]["passed"])
        self.assertEqual(result["status"], "not_passed")

    def test_missing_duplicate_and_invalid_measurements_reject(self):
        broken = copy.deepcopy(self.rows)
        broken[0]["tools"]["cand"]["output_tokens"] = float("nan")
        for rows in (self.rows[1:], self.rows + self.rows[:1], broken):
            self.assertEqual(self.run_rule(rows)["status"], "not_passed")

    def test_inapplicable_queries_are_excluded_from_quality_only(self):
        expected = dict(self.expected)
        for b in (2000, 8000):
            expected[("q00", b)] = False
        rows = copy.deepcopy(self.rows)
        for r in rows[:2]:
            for m in QUALITY:
                r["tools"]["cand"][m] = r["tools"]["graphify"][m] = None
        result = self.run_rule(rows, expected=expected)
        self.assertEqual(result["status"], "not_passed")
        self.assertEqual(result["primary"]["queries"], 40)
        self.assertEqual(result["guardrails"][0]["queries"], 39)

    def test_experimental_arms_split_primary_alpha(self):
        # Exactly 6 of 40 queries reduce tokens: one-sided p = 2^-6 = 0.0156.
        for i, r in enumerate(self.rows):
            r["tools"]["cand"]["output_tokens"] = 1700 if i % 2 == 0 and i < 12 else 1800
        self.assertTrue(self.run_rule()["primary"]["passed"])
        result = evaluate(self.rows, self.expected, "cand", PREREG, "held_out", family=5)
        self.assertEqual(result["primary"]["alpha"], 0.01)
        self.assertFalse(result["primary"]["passed"])

    def test_reports_planning_sample_size_from_observed_variance(self):
        rows = copy.deepcopy(self.rows)
        for i, r in enumerate(rows):
            r["tools"]["cand"]["code_file_hint_recall"] = 1.0 if i % 4 else 0.5
        planning = evaluate(rows, self.expected, "cand", PREREG, "development")["planning"]
        self.assertGreaterEqual(planning["required_queries"],
                                PREREG["workloads"]["held_out"]["minimum_queries"])



class RescoreTests(unittest.TestCase):
    def test_judged_files_join_the_relevant_set_for_every_tool(self):
        from decision import rescore
        rows = [{"id": "q", "budget": 2000, "tools": {
            "cand": {"cited_paths": ["a.go", "b.go"], "cited_path_order": ["b.go", "a.go"]},
            "graphify": {"cited_paths": ["a.go"], "cited_path_order": ["a.go"]}}}]
        cases = {"q": {"id": "q", "evidence": [{"path": "a.go"}]}}
        judgments = {"q": {"b.go": {"relevant": True, "reason": "r"}}}
        [scored] = rescore(rows, cases, judgments)
        self.assertEqual(scored["tools"]["cand"]["code_file_hint_recall"], 1.0)
        self.assertEqual(scored["tools"]["graphify"]["code_file_hint_recall"], 0.5)
        self.assertEqual(scored["tools"]["graphify"]["code_file_hint_recall_at_5"], 0.5)
        self.assertNotIn("relevant", rows[0]["tools"]["cand"], "input rows are not mutated")


class ReportTests(unittest.TestCase):
    def test_development_runs_preserve_heldout_and_latest_history(self):
        from reports import write_decision
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            held = {"status": "not_passed", "workload": "held_out"}
            dev = {"status": "not_passed", "workload": "development"}
            archived = write_decision(held, root)
            write_decision(dev, root)
            self.assertEqual(json.loads(archived.read_text()), held)
            self.assertEqual(json.loads((root / "results/decision-heldout.json").read_text()), held)
            self.assertEqual(json.loads((root / "results/decision-development.json").read_text()), dev)
            self.assertEqual(json.loads((root / "results/decision.json").read_text()), dev)

class PreregistrationTests(unittest.TestCase):
    def test_driver_arms_match_the_frozen_definitions(self):
        from compare_graphify import EXPERIMENTAL_ARMS, GRAF_ARMS, describe
        arms = {**PREREG["arms"]["accepted"], **PREREG["arms"]["experimental"]}
        self.assertEqual({k: describe(k) for k in GRAF_ARMS}, arms)
        self.assertEqual(set(EXPERIMENTAL_ARMS), set(PREREG["arms"]["experimental"]))

if __name__ == "__main__":
    unittest.main()
