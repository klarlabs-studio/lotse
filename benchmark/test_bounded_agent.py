import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import bounded_agent as agent
from agent_backend import QUERY_ARGS
from provider_request import ProviderRequestSession
from recorded_transport import RecordedTransport
from readonly_broker_v2 import Broker, source_manifest_sha256


TOOLS = [{"name": name, "inputSchema": {"type": "object"}} for name in ("retrieve", "read")]
READ = {"tool": "read", "arguments": {"path": "src.py"}}
FINAL = {"final": "The function returns 42."}


class FixtureTransport:
    """Invented token usage, never a tokenizer/latency/quality measurement."""
    def __init__(self, actions):
        self.actions = iter(actions)
        self.calls = []
        self.model = "fixture-model"
        self.input_tokens = 8

    def __call__(self, path, body):
        self.calls.append((path, body))
        if path.endswith("input_tokens"):
            return 200, agent.wire({"object": "response.input_tokens", "input_tokens": 8})
        action = next(self.actions)
        if isinstance(action, BaseException):
            raise action
        if isinstance(action, tuple):
            return action
        text = action if type(action) is str else agent.wire(action).decode()
        return 200, agent.wire({
            "model": self.model, "status": "completed",
            "usage": {"input_tokens": self.input_tokens, "output_tokens": 16,
                      "total_tokens": self.input_tokens + 16},
            "output": [{"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": text}]}]})


class FixtureBroker:
    def __init__(self, reply=None):
        self.calls = []
        self.reply = reply

    def handle(self, request):
        self.calls.append(request)
        return self.reply if self.reply is not None else {
            "jsonrpc": "2.0", "id": request["id"],
            "result": {"content": [{"type": "text", "text": "def answer(): return 42"}],
                       "isError": False}}


class BoundedAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def setup_pipeline(self, actions, token_limit=10000):
        self.transport = FixtureTransport(actions)
        self.recorder = RecordedTransport(self.root / "transport", self.transport, attempt_limit=16)
        self.addCleanup(self.recorder.close)
        self.provider = ProviderRequestSession(self.root / "provider", self.recorder,
                                              token_limit=token_limit, attempt_limit=8, count_limit=8)
        self.addCleanup(self.provider.close)
        self.broker = FixtureBroker()

    def run_loop(self, **kwargs):
        return agent.run(self.root / "loop", self.provider, self.broker,
                         task="What does answer return?", tools=TOOLS,
                         model="fixture-model", max_output_tokens=64, **kwargs)

    def test_multiturn_preserves_history_and_all_synthetic_usage(self):
        self.setup_pipeline([READ, FINAL])
        report = self.run_loop()
        self.assertEqual((report["status"], report["turns"], report["actions"]), ("answered", 2, 1))
        self.assertEqual(len(self.transport.calls), 4)
        request = json.loads(self.transport.calls[-1][1])
        transcript = json.loads(request["input"])
        self.assertEqual(transcript["tools"], TOOLS)
        self.assertEqual(transcript["history"][0]["action"], READ)
        self.assertEqual(transcript["history"][1]["response"]["result"]["isError"], False)
        self.assertEqual(self.provider.summary()["generation"]["actual_observed_tokens"], 48)
        self.assertFalse(report["complete_accounting"])
        self.assertFalse(report["product_promotion_pass"])
        self.assertEqual(report["task_outcome"], "unverified")
        self.assertEqual(report["agent_savings"], "unmeasured")
        for p in (self.root / "loop").iterdir():
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_cumulative_reservation_blocks_third_generation(self):
        self.setup_pipeline([READ, READ, FINAL], token_limit=144)
        report = self.run_loop()
        self.assertEqual(report["reason"], "provider_admission_denied")
        self.assertEqual(len(self.transport.calls), 5)  # Three counts, two generations.
        self.assertEqual(len(self.broker.calls), 2)
        self.assertEqual(self.provider.summary()["generation"]["reserved_tokens"], 144)

    def test_turn_limit_has_no_extra_model_request(self):
        self.setup_pipeline([READ, FINAL])
        report = self.run_loop(max_turns=1)
        self.assertEqual(report["reason"], "turn_limit")
        self.assertEqual(len(self.transport.calls), 2)

    def test_action_limit_denies_before_dispatch(self):
        self.setup_pipeline([READ, FINAL])
        report = self.run_loop(max_actions=0)
        self.assertEqual(report["reason"], "action_limit")
        self.assertEqual(self.broker.calls, [])

    def test_unknown_usage_stops_without_retry_or_tool(self):
        self.setup_pipeline([RuntimeError("private exception detail")])
        report = self.run_loop()
        self.assertEqual(report["reason"], "provider_transport_error")
        self.assertEqual(len(self.transport.calls), 2)
        self.assertEqual(self.broker.calls, [])
        self.assertNotIn("private exception detail", (self.root / "loop/report.json").read_text())

    def test_duplicate_action_key_is_rejected(self):
        self.setup_pipeline(['{"final":"one","final":"two"}'])
        report = self.run_loop()
        self.assertEqual(report["status"], "failed")
        self.assertEqual(self.broker.calls, [])

    def test_non_readonly_action_is_rejected(self):
        self.setup_pipeline([{"tool": "edit", "arguments": {}}])
        self.assertEqual(self.run_loop()["status"], "failed")
        self.assertEqual(self.broker.calls, [])

    def test_wrong_model_is_rejected_before_tool(self):
        self.setup_pipeline([READ])
        self.transport.model = "other"
        self.assertEqual(self.run_loop()["status"], "failed")
        self.assertEqual(self.broker.calls, [])

    def test_count_usage_mismatch_stops(self):
        self.setup_pipeline([READ])
        self.transport.input_tokens = 7
        self.assertEqual(self.run_loop()["reason"], "count_usage_mismatch")
        self.assertEqual(self.broker.calls, [])

    def test_tool_failure_is_preserved_and_included_in_next_count(self):
        self.setup_pipeline([READ, FINAL])
        self.broker.reply = {"jsonrpc": "2.0", "id": 1, "result": {"isError": True, "content": []}}
        self.assertEqual(self.run_loop()["status"], "answered")
        next_count = json.loads(self.transport.calls[2][1])
        history = json.loads(next_count["input"])["history"]
        self.assertTrue(history[-1]["response"]["result"]["isError"])

    def test_invalid_broker_reply_stops(self):
        self.setup_pipeline([READ, FINAL])
        self.broker.reply = {"jsonrpc": "2.0", "id": 42, "result": {}}
        self.assertEqual(self.run_loop()["reason"], "invalid_broker_reply")
        self.assertEqual(len(self.transport.calls), 2)

    def test_oversized_tool_reply_saved_in_full_without_next_call(self):
        self.setup_pipeline([READ, FINAL])
        self.broker.reply = {"jsonrpc": "2.0", "id": 1, "result": {"text": "x" * 10000}}
        report = self.run_loop(max_payload_bytes=4096)
        self.assertEqual(report["reason"], "tool_response_byte_limit")
        self.assertEqual(len(self.transport.calls), 2)
        saved = json.loads((self.root / "loop/tool-response-000001.json").read_bytes())
        self.assertEqual(saved, self.broker.reply)

    def test_initial_request_byte_limit_prevents_forward(self):
        self.setup_pipeline([FINAL])
        self.assertEqual(self.run_loop(max_payload_bytes=128)["reason"], "request_byte_limit")
        self.assertEqual(self.transport.calls, [])

    def test_existing_loop_evidence_never_overwritten(self):
        self.setup_pipeline([FINAL])
        (self.root / "loop").mkdir()
        with self.assertRaises(FileExistsError):
            self.run_loop()
        self.assertEqual(self.transport.calls, [])

    def test_event_persistence_failure_prevents_dispatch(self):
        self.setup_pipeline([READ])
        original = agent._save

        def failing(path, raw):
            if json.loads(raw).get("event") == "tool_begin":
                raise OSError("disk full")
            original(path, raw)

        with patch.object(agent, "_save", side_effect=failing):
            self.assertEqual(self.run_loop()["status"], "failed")
        self.assertEqual(self.broker.calls, [])

    def test_broker_mutation_does_not_change_recorded_action_history(self):
        self.setup_pipeline([READ, FINAL])

        def mutate(request):
            request["params"]["arguments"]["path"] = "changed.py"
            return {"jsonrpc": "2.0", "id": 1, "result": {}}

        self.broker.handle = mutate
        self.assertEqual(self.run_loop()["status"], "answered")
        transcript = json.loads(json.loads(self.transport.calls[-1][1])["input"])
        self.assertEqual(transcript["history"][0]["action"]["arguments"]["path"], "src.py")

    def test_nonfresh_provider_is_rejected_without_more_calls(self):
        self.setup_pipeline([FINAL])
        request = agent.wire({"model": "fixture-model", "input": "used", "max_output_tokens": 64})
        self.provider.run(request)
        with self.assertRaisesRegex(ValueError, "fresh_provider_session_required"):
            self.run_loop()
        self.assertEqual(len(self.transport.calls), 2)
        self.assertFalse((self.root / "loop").exists())

    def test_saved_offline_probe_reconciles_all_three_scenarios(self):
        from probe_bounded_agent import run
        report = run(self.root / "probe")
        self.assertTrue(report["diagnostic_pass"])
        self.assertEqual(report["fixture_forwarded_calls"], 13)
        self.assertEqual(report["network_requests"], 0)
        self.assertFalse(report["complete_accounting"])

    def test_cancellation_is_recorded_and_propagated(self):
        self.setup_pipeline([KeyboardInterrupt()])
        with self.assertRaises(KeyboardInterrupt):
            self.run_loop()
        rows = [json.loads(p.read_bytes()) for p in sorted((self.root / "loop").glob("event-*.json"))]
        self.assertEqual(rows[-1]["event"], "cancelled")
        self.assertEqual(self.broker.calls, [])

    def test_integration_with_pinned_broker_and_actual_source_read(self):
        self.setup_pipeline([{"tool": "retrieve", "arguments": {"query": "answer"}}, READ, FINAL])
        source_root = self.root / "repo"
        source_root.mkdir()
        (source_root / "src.py").write_text("def answer(): return 42\n")
        artifact = source_root / ".graf/index.bin"
        artifact.parent.mkdir()
        artifact.write_bytes(b"fixture index")
        binary = source_root / "backend"
        binary.write_bytes(b"fixture backend")
        manifest = {"src.py": agent.sha((source_root / "src.py").read_bytes())}
        plan = {"comparison": {"shared_output_budget": 64},
                "subprocess_environment": {"PYTHONHASHSEED": "0"},
                "source_manifest": manifest,
                "arms": [{"id": "graf_v61", "binary": str(binary),
                          "binary_sha256": agent.sha(binary.read_bytes()),
                          "query_args": QUERY_ARGS["graf_v61"]}],
                "artifacts": {"graf_v61": {"path": str(artifact),
                    "sha256": agent.sha(artifact.read_bytes()),
                    "source_manifest_sha256": source_manifest_sha256(manifest)}}}
        native_calls = []

        def runner(argv, **kwargs):
            native_calls.append(argv)
            return SimpleNamespace(returncode=0, stdout="src.py:1 fixture hint\n", stderr="")

        self.broker = Broker(plan, "graf_v61", source_root, self.root / "graph.json",
                             self.root / "broker.jsonl", max_actions=2, runner=runner)
        self.addCleanup(self.broker.close)
        report = self.run_loop()
        self.assertEqual(report["status"], "answered")
        self.assertEqual(len(native_calls), 1)
        self.assertEqual(native_calls[0][3], "evidence-selective")
        transcript = json.loads(json.loads(self.transport.calls[-1][1])["input"])
        self.assertIn("return 42", json.dumps(transcript["history"][-1]))
        self.assertEqual(len(self.transport.calls), 6)


if __name__ == "__main__":
    unittest.main()
