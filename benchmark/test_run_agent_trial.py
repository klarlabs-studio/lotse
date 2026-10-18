"""Agent-trial accounting must keep every model's usage and never invent it."""
import json
from pathlib import Path
import tempfile
import unittest

from agent_trials import audit_trace
from run_agent_trial import claude_command, usage_events

RESULT = {"type": "result", "subtype": "success", "is_error": False, "num_turns": 3,
          "total_cost_usd": 0.0123,
          "modelUsage": {"claude-haiku-5-5": {"inputTokens": 10, "outputTokens": 400,
                                              "cacheReadInputTokens": 3000,
                                              "cacheCreationInputTokens": 900,
                                              "thinkingTokens": 150, "costUSD": 0.0123}}}


class UsageTests(unittest.TestCase):
    def test_input_includes_cache_reads_and_writes(self):
        [event] = usage_events(RESULT)
        self.assertEqual(event["input_tokens"], 3910)
        self.assertEqual(event["cached_input_tokens"], 3000)
        self.assertEqual(event["output_tokens"], 400)
        self.assertEqual(event["reasoning_tokens"], 150)
        self.assertEqual(event["cost_basis"], "list-price estimate under a subscription")

    def test_every_model_is_reported_and_missing_thinking_stays_unknown(self):
        result = json.loads(json.dumps(RESULT))
        result["modelUsage"]["other-model"] = {"inputTokens": 1, "outputTokens": 2,
                                               "cacheReadInputTokens": 0,
                                               "cacheCreationInputTokens": 0, "costUSD": 0.0}
        events = usage_events(result)
        self.assertEqual({e["model"] for e in events}, {"claude-haiku-5-5", "other-model"})
        self.assertIsNone(next(e for e in events if e["model"] == "other-model")["reasoning_tokens"])

    def test_missing_result_is_an_error_not_zero_usage(self):
        with self.assertRaises(ValueError):
            usage_events(None)

    def test_events_pass_the_trace_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / "trace.jsonl"
            events = [*usage_events(RESULT), {"type": "verification", "passed": False}]
            trace.write_text("\n".join(json.dumps(e) for e in events))
            audit_trace(trace)


class CommandTests(unittest.TestCase):
    def test_command_disables_builtin_tools_and_foreign_configuration(self):
        command = claude_command("issue text", Path("/x/mcp.json"), "claude-haiku-5-5", 0.5)
        joined = " ".join(command)
        for flag in ('--tools', '--strict-mcp-config', '--setting-sources', '--disable-slash-commands',
                     '--no-session-persistence', '--max-budget-usd 0.5', '--model claude-haiku-5-5',
                     '--allowedTools mcp__broker'):
            self.assertIn(flag, joined)
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertEqual(command[command.index("--setting-sources") + 1], "")


if __name__ == "__main__":
    unittest.main()
