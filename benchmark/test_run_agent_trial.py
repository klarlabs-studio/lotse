"""Agent-trial accounting must keep every model's usage and never invent it."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_trials import audit_trace
from run_agent_trial import SYSTEM_PROMPT, claude_command, preflight, scored_events, system_prompt, usage_events

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
    def test_a_pinned_solver_copy_replaces_the_self_updating_one(self):
        with patch.dict("os.environ", {"CLAUDE_BINARY": "/pinned/claude"}):
            self.assertEqual(claude_command("i", Path("/x/mcp.json"), "m", 0.5, "graf")[0], "/pinned/claude")
        with patch.dict("os.environ", clear=True):
            self.assertEqual(claude_command("i", Path("/x/mcp.json"), "m", 0.5, "graf")[0], "claude")

    def test_command_disables_builtin_tools_and_foreign_configuration(self):
        command = claude_command("issue text", Path("/x/mcp.json"), "claude-haiku-5-5", 0.5, "graf")
        joined = " ".join(command)
        for flag in ('--tools', '--strict-mcp-config', '--setting-sources', '--disable-slash-commands',
                     '--no-session-persistence', '--max-budget-usd 0.5', '--model claude-haiku-5-5',
                     '--allowedTools mcp__broker'):
            self.assertIn(flag, joined)
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertEqual(command[command.index("--setting-sources") + 1], "")


    def test_orient_arms_prepend_the_orientation_and_describe_it(self):
        from run_agent_trial import ORIENTATION_HEADER, issue_prompt
        self.assertEqual(issue_prompt("bug", None), "bug")
        prompt = issue_prompt("bug", "Likely files:\n- a.py\n")
        self.assertTrue(prompt.startswith("bug\n\n" + ORIENTATION_HEADER))
        self.assertTrue(prompt.endswith("- a.py\n"))
        for arm in ("graf_orient", "graf_current_orient", "graphify_orient"):
            self.assertIn("orientation", system_prompt(arm))
        self.assertIn("outline", system_prompt("graf_orient"))
        self.assertEqual(system_prompt("graf_current_orient"), system_prompt("graf_orient"))
        self.assertNotIn("outline", system_prompt("graphify_orient"))

    def test_prompt_names_only_the_arm_tools(self):
        # Batch t ran with SYSTEM_PROMPT for both arms; retrieval arms keep it unchanged.
        for arm in ("graf", "graf_floor", "graphify", "ast_graph"):
            self.assertEqual(system_prompt(arm), SYSTEM_PROMPT)
        self.assertNotIn("retrieve", system_prompt("search_only"))
        command = claude_command("issue", Path("/x/mcp.json"), "m", 0.5, "search_only")
        self.assertEqual(command[command.index("--system-prompt") + 1], system_prompt("search_only"))


class ScoredEventTests(unittest.TestCase):
    def test_verdicts_written_by_a_refused_finish_are_dropped(self):
        finish = {"type": "tool", "operation": "finish", "response": {"ok": True}}
        refused = {"type": "tool", "operation": "finish", "response": {"ok": False, "error": "Trial is already finished"}}
        events = [finish, {"type": "verification", "passed": True}, refused, {"type": "verification", "passed": False}]
        kept, dropped = scored_events(events)
        self.assertEqual(kept, events[:3])
        self.assertEqual(dropped, 1)
        self.assertEqual(scored_events(events[:2]), (events[:2], 0))


class FrozenBackendTests(unittest.TestCase):
    def test_t4_ast_graph_backend_keeps_the_hash_its_first_runs_recorded(self):
        from run_agent_trial import TOOL_BINARIES
        self.assertTrue(preflight("ast_graph", TOOL_BINARIES["ast_graph"]).startswith("fc85e65972d1"))


class PreflightTests(unittest.TestCase):
    def test_missing_tool_binary_stops_before_the_agent_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "graf"
            with self.assertRaises(RuntimeError):
                preflight("graf", missing)
            missing.write_bytes(b"binary")
            missing.chmod(0o755)
            self.assertEqual(len(preflight("graf", missing)), 64)

    def test_script_backends_hash_every_source_file(self):
        with tempfile.TemporaryDirectory() as temp:
            sources = [Path(temp) / "a.py", Path(temp) / "b.py"]
            with self.assertRaises(RuntimeError):
                preflight("ast_graph", sources)
            for source in sources:
                source.write_text("x = 1\n")
            first = preflight("ast_graph", sources)
            sources[1].write_text("x = 2\n")
            self.assertNotEqual(preflight("ast_graph", sources), first)


if __name__ == "__main__":
    unittest.main()
