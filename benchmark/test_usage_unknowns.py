"""Known total tokens remain usable without fabricating missing breakdown/cost."""
import json
from pathlib import Path
import tempfile
import unittest

from agent_trials import audit_trace


class UnknownUsageIntegrity(unittest.TestCase):
    def audit(self, *calls):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "trace.jsonl"
            events = [dict(type="model_usage", component="solver", model="fixed-model", **call) for call in calls]
            events.append(dict(type="verification", passed=False))
            path.write_text("\n".join(json.dumps(e) for e in events))
            return audit_trace(path)

    def usage(self, **overrides):
        value = dict(input_tokens=100, output_tokens=50, cached_input_tokens=None,
                     reasoning_tokens=None, cost_usd=None)
        value.update(overrides)
        return value

    def test_unknown_fields_stay_unknown_while_token_totals_are_counted(self):
        result = self.audit(self.usage())
        self.assertEqual(result["total_tokens"], 150)
        self.assertIsNone(result["cost_usd"])
        self.assertIsNone(result["reasoning_tokens"])
        self.assertIsNone(result["cached_input_tokens"])
        self.assertEqual(set(result["unmeasured_fields"]), {"cost_usd", "reasoning_tokens", "cached_input_tokens"})
        self.assertFalse(result["verification_passed"])

    def test_one_unknown_call_makes_the_full_cost_unknown(self):
        result = self.audit(self.usage(cost_usd=.1), self.usage())
        self.assertEqual(result["total_tokens"], 300)
        self.assertIsNone(result["cost_usd"])

    def test_input_output_totals_cannot_be_unknown_or_fractional(self):
        for invalid in [None, -1, True, 1.5, float("inf")]:
            with self.assertRaises(ValueError):
                self.audit(self.usage(input_tokens=invalid))

    def test_known_breakdowns_must_still_fit_their_totals(self):
        with self.assertRaises(ValueError):
            self.audit(self.usage(cached_input_tokens=101))
        with self.assertRaises(ValueError):
            self.audit(self.usage(reasoning_tokens=51))
        with self.assertRaises(ValueError):
            self.audit(self.usage(cost_usd=float("nan")))


if __name__ == "__main__":
    unittest.main()
