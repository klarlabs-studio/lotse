import json
import unittest

from claude_failure_accounting import analyze_failure


def stream(events):
    return ("\n".join(json.dumps(e, separators=(",", ":")) for e in events) + "\n").encode()


def usage_result(subtype="success", is_error=False):
    return {"type": "result", "subtype": subtype, "is_error": is_error,
            "usage": {"input_tokens": 10, "cache_creation_input_tokens": 3,
                      "cache_read_input_tokens": 7, "output_tokens": 4},
            "modelUsage": {"claude-test": {"inputTokens": 10,
                "cacheCreationInputTokens": 3, "cacheReadInputTokens": 7,
                "outputTokens": 4, "costUSD": 0.001}}}


class ClaudeFailureAccountingTests(unittest.TestCase):
    def base(self):
        return [{"type": "assistant", "message": {"id": "m1"}},
                usage_result()]

    def test_success_and_tool_retry_report_observations_separately(self):
        events = [{"type": "assistant", "message": {"id": "m1"}},
                  {"type": "assistant", "message": {"id": "m2", "content": [
                      {"type": "tool_use", "id": "t1"}]}}, usage_result()]
        report = analyze_failure(stream(events))
        self.assertEqual(report["task_outcome"], "success")
        self.assertEqual(report["accounting_status"], "reported_terminal_consistent")
        self.assertEqual(report["assistant_count"], 2)
        self.assertEqual(report["assistant_message_ids"], ["m1", "m2"])
        self.assertEqual(report["usage_bearing_event_count"], 2)
        self.assertEqual(report["result_usage"]["outputTokens"], 4)
        self.assertEqual(report["exposed_estimated_cost"], 0.001)
        self.assertEqual(report["internal_provider_attempts"], "unmeasured")
        self.assertFalse(report["complete_accounting"])
        self.assertFalse(report["hardcap_verified"])
        self.assertFalse(report["promotion"])
        self.assertEqual(report["savings"], "unmeasured")

    def test_error_max_turns_can_have_consistent_usage_without_being_success(self):
        events = self.base()
        events[-1] = usage_result("error_max_turns", True)
        report = analyze_failure(stream(events))
        self.assertEqual(report["task_outcome"], "error_max_turns")
        self.assertTrue(report["result_usage_valid"])
        self.assertEqual(report["accounting_status"], "reported_terminal_consistent")
        self.assertFalse(report["diagnostic_success"])

    def test_no_result_and_interrupted_are_distinguished(self):
        self.assertEqual(analyze_failure(b"")["task_outcome"], "no_result")
        report = analyze_failure(stream(self.base()[:1]))
        self.assertEqual(report["task_outcome"], "interrupted")
        self.assertEqual(report["accounting_status"], "unknown")

    def test_truncated_tail_preserves_complete_prefix_observations_only(self):
        prefix = stream(self.base()[:1])
        report = analyze_failure(prefix + b'{"type":"result","usage":')
        self.assertEqual(report["task_outcome"], "malformed_stream")
        self.assertEqual(report["assistant_message_ids"], ["m1"])
        self.assertEqual(report["assistant_count"], 1)
        self.assertEqual(report["result_count"], 0)
        self.assertIsNone(report["result_usage"])
        self.assertIsNone(report["model_usage_totals"])
        self.assertEqual(report["accounting_status"], "unknown")
        self.assertEqual(report["parse_error"], "invalid_stream_json")
        self.assertNotIn("result\",\"usage", report["parse_error"])

    def test_malformed_suffix_after_terminal_result_cannot_keep_diagnostic_pass(self):
        report = analyze_failure(stream(self.base()) + b'{"broken":')
        self.assertEqual(report["task_outcome"], "malformed_stream")
        self.assertEqual(report["accounting_status"], "unknown")
        self.assertFalse(report["diagnostic_pass"])
        self.assertFalse(report["diagnostic_success"])
        self.assertIsNone(report["model_usage_totals"])

    def test_duplicate_and_negative_usage_remain_unknown(self):
        duplicate = stream(self.base()) + b'{"type":"result","type":"assistant"}\n'
        report = analyze_failure(duplicate)
        self.assertEqual(report["accounting_status"], "unknown")
        events = self.base()
        events[-1]["modelUsage"]["claude-test"]["outputTokens"] = -1
        report = analyze_failure(stream(events))
        self.assertIsNone(report["model_usage_totals"])
        self.assertEqual(report["accounting_status"], "unknown")


if __name__ == "__main__":
    unittest.main()
