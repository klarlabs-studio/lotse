"""Agent-trial accounting must keep every model's usage and never invent it."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from agent_trials import audit_trace
from run_agent_trial import SYSTEM_PROMPT, attempt_budget, claude_command, gateway_refusal, lost_reply, solver_budget, solver_model, preflight, scored_events, system_prompt, timeout_usage, usage_events

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

    def test_a_frozen_price_table_replaces_the_solver_estimate(self):
        prices = '{"input": 0.03, "cache_read": 0.006, "cache_write": 0.03, "output": 0.13}'
        with patch.dict("os.environ", {"SOLVER_PRICES": prices}):
            [event] = usage_events(RESULT)
        self.assertAlmostEqual(event["cost_usd"], (10 * 0.03 + 3000 * 0.006 + 900 * 0.03 + 400 * 0.13) / 1e6)
        self.assertEqual(event["cost_basis"], "frozen gateway price table (preregistered)")
        with patch.dict("os.environ", clear=True):
            self.assertEqual(usage_events(RESULT)[0]["cost_usd"], 0.0123)

    def test_a_turn_cap_ends_loops_with_usage_reported(self):
        with patch.dict("os.environ", {"SOLVER_MAX_TURNS": "80"}):
            command = claude_command("i", Path("/x/mcp.json"), "m", 0.5, "graf")
        self.assertEqual(command[command.index("--max-turns") + 1], "80")
        with patch.dict("os.environ", clear=True):
            self.assertNotIn("--max-turns", claude_command("i", Path("/x/mcp.json"), "m", 0.5, "graf"))

    def test_model_and_spend_cap_follow_the_batch(self):
        with patch.dict("os.environ", {"SOLVER_MODEL": "qwen/x", "SOLVER_BUDGET_USD": "25"}):
            command = claude_command("i", Path("/x/mcp.json"), solver_model("claude-haiku-5-5"), solver_budget(0.5), "graf")
        self.assertIn("--model qwen/x", " ".join(command))
        self.assertIn("--max-budget-usd 25.0", " ".join(command))
        with patch.dict("os.environ", clear=True):
            self.assertEqual((solver_model("m"), solver_budget(0.5)), ("m", 0.5))

    def test_missing_result_is_an_error_not_zero_usage(self):
        with self.assertRaises(ValueError):
            usage_events(None)

    def test_events_pass_the_trace_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            trace = Path(tmp) / "trace.jsonl"
            events = [*usage_events(RESULT), {"type": "verification", "passed": False}]
            trace.write_text("\n".join(json.dumps(e) for e in events))
            audit_trace(trace)


class LostReplyTests(unittest.TestCase):
    def stream(self, *events):
        path = Path(tempfile.mkdtemp()) / "agent-stream.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in events))
        return path

    def test_a_timeout_waiting_on_an_unanswered_tool_call_is_an_infrastructure_abort(self):
        use = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "a"}, {"type": "tool_use", "id": "b"}]}}
        answered = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "a"}]}}
        self.assertTrue(lost_reply(self.stream(use, answered), "timeout"))
        both = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "b"}]}}
        self.assertFalse(lost_reply(self.stream(use, answered, both), "timeout"))
        self.assertFalse(lost_reply(self.stream(use, answered), 0))

    def test_a_call_seen_only_as_heartbeats_is_a_lost_reply(self):
        beat = {"type": "tool_progress", "tool_use_id": "c-heartbeat-3", "tool_name": "mcp__broker__search"}
        self.assertTrue(lost_reply(self.stream(beat), "timeout"))
        done = {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "c"}]}}
        self.assertFalse(lost_reply(self.stream(beat, done), "timeout"))

    def test_a_timeout_during_a_verifier_call_within_its_limit_is_the_solvers(self):
        for tool in ("mcp__broker__test", "mcp__broker__finish"):
            use = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t", "name": tool}]}}
            beat = {"type": "tool_progress", "tool_use_id": "t-heartbeat-4", "tool_name": tool, "elapsed_time_seconds": 150}
            self.assertFalse(lost_reply(self.stream(use, beat), "timeout"))
            hung = {**beat, "elapsed_time_seconds": 300}  # Past the verifier's own limit: the reply was lost.
            self.assertTrue(lost_reply(self.stream(use, hung), "timeout"))
        beat = {"type": "tool_progress", "tool_use_id": "v-heartbeat-0", "tool_name": "mcp__broker__test", "elapsed_time_seconds": 30}
        self.assertFalse(lost_reply(self.stream(beat), "timeout"))

    def test_a_solver_timeout_bounds_its_cost_from_the_streamed_calls(self):
        def call(ident, output, read, write_1h, cached_input=0):
            return {"type": "assistant", "message": {"id": ident, "model": "claude-haiku-5-5", "content": [], "usage": {
                "input_tokens": 2, "output_tokens": output, "cache_read_input_tokens": read + cached_input,
                "cache_creation_input_tokens": write_1h,
                "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": write_1h}}}}
        stream = self.stream(call("m1", 5, 1000, 4000), call("m1", 5, 1000, 4000), call("m2", 7, 120_000, 0))
        events, (lower, upper) = timeout_usage(stream, 0.5)
        short = (2 * 0.1 + 5 * 0.5 + 4000 * 0.2 + 1000 * 0.01) / 1e6  # m1 once: repeated message ids are one call
        long = (2 * 0.5 + 7 * 2.5 + 120_000 * 0.05) / 1e6  # Above 100K prompt tokens: the long-prompt rates
        self.assertAlmostEqual(lower, short + long)
        self.assertEqual(upper, 0.5)
        self.assertEqual(events[0]["input_tokens"], 5002 + 120_002)
        self.assertIsNone(events[0]["reasoning_tokens"])
        with self.assertRaises(ValueError):
            timeout_usage(self.stream({"type": "user", "message": {"content": []}}), 0.5)
        self.assertEqual(attempt_budget(["claude", "--max-budget-usd", "0.5", "--model", "m"]), 0.5)

    def test_a_gateway_refusal_is_an_infrastructure_abort(self):
        refused = {"type": "assistant", "message": {"model": "<synthetic>", "content": [
            {"type": "text", "text": "API Error: 402 This request would exceed your available credits"}]}}
        self.assertTrue(gateway_refusal(self.stream(refused)))
        answer = {"type": "assistant", "message": {"model": "m", "content": [{"type": "text", "text": "API Error: in a reply"}]}}
        self.assertFalse(gateway_refusal(self.stream(answer)))


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

    def test_a_first_finish_whose_verifier_failed_keeps_its_verdict(self):
        failed = {"type": "tool", "operation": "finish", "response": {"ok": False, "error": "timed out after 180 seconds"}}
        events = [failed, {"type": "verification", "passed": False}]
        self.assertEqual(scored_events(events), (events, 0))


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
