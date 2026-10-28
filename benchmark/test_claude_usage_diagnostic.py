import json
import unittest

from claude_usage_diagnostic import analyze


def stream(events):
    return ("\n".join(json.dumps(e, separators=(",", ":")) for e in events) + "\n").encode()


def valid_events():
    return [
        {"type": "system", "subtype": "init", "model": "claude-test"},
        {"type": "assistant", "message": {"id": "msg-1", "model": "claude-test"}},
        {"type": "result", "subtype": "success", "is_error": False,
         "usage": {"input_tokens": 10, "cache_creation_input_tokens": 3,
                   "cache_read_input_tokens": 7, "output_tokens": 4},
         "modelUsage": {"claude-test": {"inputTokens": 10, "cacheCreationInputTokens": 3,
                                          "cacheReadInputTokens": 7, "outputTokens": 4,
                                          "costUSD": 0.001}}},
    ]


class ClaudeUsageDiagnosticTests(unittest.TestCase):
    def test_valid_trace_keeps_cached_categories_and_only_claims_diagnostic_success(self):
        result = analyze(b"\n" + stream(valid_events()))
        self.assertTrue(result["diagnostic_success"])
        self.assertEqual(result["model_usage_totals"]["inputTokens"], 10)
        self.assertEqual(result["model_usage_totals"]["inputTokensTotal"], 20)
        self.assertEqual(result["model_usage_totals"]["cacheCreationInputTokens"], 3)
        self.assertEqual(result["model_usage_totals"]["cacheReadInputTokens"], 7)
        self.assertEqual(result["result_usage"]["inputTokens"], 20)
        self.assertEqual(result["assistant_message_ids"], ["msg-1"])
        self.assertEqual(result["init_models"], ["claude-test"])
        self.assertEqual(result["model_usage_keys"], ["claude-test"])
        self.assertFalse(result["complete_accounting"])
        self.assertFalse(result["product_promotion_pass"])
        self.assertEqual(result["agent_savings"], "unmeasured")
        self.assertEqual(result["live_cap"], "unverified")
        self.assertIn("not verified billing", result["estimated_cost_label"])

    def test_duplicate_keys_and_nonfinite_numbers_are_rejected(self):
        for raw in (b'{"type":"result","type":"assistant"}',
                    b'{"type":"assistant","x":NaN}'):
            with self.subTest(raw=raw):
                result = analyze(raw)
                self.assertFalse(result["diagnostic_pass"])
                self.assertIn("parse_error", result)

    def test_missing_result_or_model_usage_stays_unknown_and_fails(self):
        no_result = analyze(stream(valid_events()[:2]))
        self.assertEqual(no_result["result_count"], 0)
        self.assertIsNone(no_result["model_usage_totals"])
        self.assertFalse(no_result["diagnostic_success"])
        no_usage = valid_events()
        no_usage[-1].pop("modelUsage")
        report = analyze(stream(no_usage))
        self.assertIsNone(report["model_usage_totals"])
        self.assertFalse(report["diagnostic_success"])

    def test_duplicate_result_and_mismatched_totals_fail(self):
        events = valid_events()
        self.assertFalse(analyze(stream(events + [events[-1]]))["diagnostic_success"])
        events[-1]["usage"]["output_tokens"] = 5
        report = analyze(stream(events))
        self.assertFalse(report["totals_match"])
        self.assertFalse(report["diagnostic_success"])

    def test_boolean_negative_and_estimated_cost_values_are_not_accepted_as_accounting(self):
        events = valid_events()
        events[-1]["modelUsage"]["claude-test"]["inputTokens"] = True
        report = analyze(stream(events))
        self.assertIsNone(report["model_usage_totals"])
        self.assertFalse(report["diagnostic_success"])
        events = valid_events()
        events[-1]["modelUsage"]["claude-test"]["outputTokens"] = -1
        self.assertFalse(analyze(stream(events))["diagnostic_success"])
        events = valid_events()
        events[-1]["modelUsage"]["claude-test"]["costUSD"] = "estimated: 0.001"
        report = analyze(stream(events))
        self.assertIsNone(report["model_usage_totals"])
        self.assertFalse(report["diagnostic_success"])

    def test_permission_denials_prevent_success(self):
        events = valid_events()
        events[-1]["permission_denials"] = ["denied"]
        self.assertFalse(analyze(stream(events))["diagnostic_success"])

    def test_nested_usage_observations_are_counted_individually(self):
        events = valid_events()
        events[1]["message"]["usage"] = {"input_tokens": 1}
        events.insert(2, {"type": "stream_event", "event": {
            "type": "content_block_delta", "message": {"usage": {"output_tokens": 2}}}})
        report = analyze(stream(events))
        self.assertEqual(report["usage_bearing_event_count"], 4)
        self.assertEqual(report["result_usage_event_count"], 1)

    def test_repeated_model_usage_observations_are_not_summed(self):
        events = valid_events()
        events.insert(1, {"type": "assistant", "modelUsage": {
            "claude-test": {"inputTokens": 10, "cacheCreationInputTokens": 3,
                            "cacheReadInputTokens": 7, "outputTokens": 4, "costUSD": 0.001}}})
        report = analyze(stream(events))
        self.assertIsNone(report["model_usage_totals"])
        self.assertFalse(report["diagnostic_pass"])

    def test_overflowing_integer_cost_is_invalid_without_raising(self):
        events = valid_events()
        events[-1]["modelUsage"]["claude-test"]["costUSD"] = 10 ** 10000
        report = analyze(stream(events))
        self.assertIsNone(report["model_usage_totals"])
        self.assertFalse(report["diagnostic_pass"])


if __name__ == "__main__":
    unittest.main()
