"""Acceptance checks must expose losses and reject incomplete comparisons."""
import copy
import unittest

from acceptance import QUALITY, evaluate


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.expected = {("one", 2000): True, ("two", 2000): False}
        self.rows = []
        for name, applicable in (("one", True), ("two", False)):
            metrics = {k: 1.0 if applicable else None for k in QUALITY}
            self.rows.append({"id": name, "budget": 2000, "tools": {
                "graf_locate": {**metrics, "output_tokens": 80, "median_ms": 10},
                "graphify": {**metrics, "output_tokens": 100, "median_ms": 20}}})

    def run_gate(self, rows=None, expected=None):
        return evaluate(self.rows if rows is None else rows,
                        self.expected if expected is None else expected, "graf_locate")

    def test_diagnostic_win_cannot_authorize_product_claim(self):
        result = self.run_gate()
        self.assertEqual(result["status"], "passed")
        self.assertFalse(result["product_promotion_allowed"])

    def test_aggregate_gain_does_not_hide_one_case_loss(self):
        self.rows[0]["tools"]["graf_locate"]["output_tokens"] = 101
        result = self.run_gate()
        self.assertLess(result["delivered_text_tokens"]["candidate"], 200)
        self.assertEqual(result["status"], "not_passed")
        self.assertEqual(result["regressing_cases"], 1)

    def test_quality_loss_blocks_token_gain(self):
        for metric in QUALITY:
            with self.subTest(metric=metric):
                rows = copy.deepcopy(self.rows)
                rows[0]["tools"]["graf_locate"][metric] = 0.5
                self.assertEqual(self.run_gate(rows)["status"], "not_passed")

    def test_missing_duplicate_extra_and_empty_workloads_reject(self):
        for rows, expected in ((self.rows[:1], self.expected),
                               (self.rows + self.rows[:1], self.expected),
                               (self.rows, {("one", 2000): True}), ([], {})):
            self.assertEqual(self.run_gate(rows, expected)["status"], "not_passed")

    def test_unknown_nonfinite_and_invalid_values_reject(self):
        for bad in (None, True, -1, float("nan"), float("inf"), "80"):
            rows = copy.deepcopy(self.rows)
            rows[0]["tools"]["graf_locate"]["output_tokens"] = bad
            self.assertEqual(self.run_gate(rows)["status"], "not_passed")
        del self.rows[0]["tools"]["graf_locate"][QUALITY[0]]
        self.assertEqual(self.run_gate()["invalid_cases"], 1)

    def test_tie_is_not_improvement_and_null_is_only_explicitly_inapplicable(self):
        for row in self.rows:
            row["tools"]["graf_locate"]["output_tokens"] = 100
        self.assertEqual(self.run_gate()["status"], "not_passed")
        self.rows[0]["tools"]["graf_locate"][QUALITY[0]] = None
        self.assertEqual(self.run_gate()["invalid_cases"], 1)


if __name__ == "__main__":
    unittest.main()
