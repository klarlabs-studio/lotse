import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import probe_openai_provider as probe


def plan_value():
    return {"id": "openai-provider-v1", "scope": "single_request_protocol_diagnostic",
            "request": probe.REQUEST, "request_sha256": probe.digest(probe.encode(probe.REQUEST)),
            "limits": probe.LIMITS, "credential_source": "OPENAI_API_KEY_environment_only",
            "criteria": ["exact_returned_model", "count_matches_observed_input", "output_within_cap",
                         "no_unknown_or_pending_generation_usage",
                         "one_count_and_at_most_one_generation", "no_retries"],
            "sources": {name: probe.digest((probe.ROOT / name).read_bytes())
                        for name in probe.SOURCES}}


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=probe.ROOT / ".research")
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.plan = self.directory / "plan.json"
        self.output = self.directory / "report.json"
        self.plan.write_bytes(probe.encode(plan_value()))

    def test_missing_key_blocks_even_execute_without_constructing_transport(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(probe, "OpenAITransport") as transport:
            result = probe.run(self.plan, self.output, execute=True)
        transport.assert_not_called()
        self.assertEqual(result["network_requests"], 0)
        self.assertEqual(result["blockers"], ["missing_OPENAI_API_KEY"])
        self.assertFalse(result["product_promotion_pass"])
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)

    def test_preflight_with_key_never_constructs_transport_or_leaks_key(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret"}), patch.object(probe, "OpenAITransport") as transport:
            result = probe.run(self.plan, self.output)
        transport.assert_not_called()
        self.assertEqual(result["blockers"], ["execution_not_requested"])
        self.assertNotIn("synthetic-secret", self.output.read_text())

    def test_source_mismatch_rejected_before_transport(self):
        value = plan_value()
        value["sources"][probe.SOURCES[0]] = "0" * 64
        self.plan.write_bytes(probe.encode(value))
        with patch.object(probe, "OpenAITransport") as transport:
            with self.assertRaisesRegex(ValueError, "source_pin_mismatch"):
                probe.run(self.plan, self.output, execute=True)
        transport.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_changed_limits_and_duplicate_keys_rejected(self):
        value = plan_value()
        value["limits"] = dict(probe.LIMITS, attempt_limit=2)
        self.plan.write_bytes(probe.encode(value))
        with self.assertRaisesRegex(ValueError, "unsupported_plan"):
            probe.run(self.plan, self.output)
        self.plan.write_bytes(b'{"id":1,"id":2}')
        with self.assertRaises(ValueError):
            probe.run(self.plan, self.output)

    def test_mock_success_two_calls_and_keeps_promotion_blocked(self):
        count = {"object": "response.input_tokens", "input_tokens": 8}
        generation = {"status": "completed", "model": "gpt-6-luna",
                      "usage": {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10}}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret"}), patch.object(probe, "OpenAITransport") as transport:
            transport.return_value.side_effect = [(200, probe.encode(count)), (200, probe.encode(generation))]
            result = probe.run(self.plan, self.output, execute=True)
        self.assertEqual(transport.return_value.call_count, 2)
        self.assertTrue(result["live_diagnostic_pass"])
        self.assertFalse(result["product_promotion_pass"])
        self.assertFalse(result["complete_accounting"])
        self.assertNotIn("synthetic-secret", self.output.read_text())
        self.assertEqual(result["session"]["generation"]["reserved_tokens"], 24)

    def test_failed_count_never_generates(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret"}), patch.object(probe, "OpenAITransport") as transport:
            transport.return_value.return_value = (503, b'{}')
            result = probe.run(self.plan, self.output, execute=True)
        self.assertEqual(transport.return_value.call_count, 1)
        self.assertFalse(result["live_diagnostic_pass"])
        self.assertEqual(result["session"]["generation"]["accepted_attempts"], 0)

    def test_existing_report_blocks_before_transport(self):
        self.output.write_text("preserved")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret"}), patch.object(probe, "OpenAITransport") as transport:
            with self.assertRaises(FileExistsError):
                probe.run(self.plan, self.output, execute=True)
        transport.assert_not_called()
        self.assertEqual(self.output.read_text(), "preserved")

    def test_missing_generation_usage_stays_unknown(self):
        count = {"object": "response.input_tokens", "input_tokens": 8}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret"}), patch.object(probe, "OpenAITransport") as transport:
            transport.return_value.side_effect = [(200, probe.encode(count)),
                                                  (200, b'{"status":"completed"}')]
            result = probe.run(self.plan, self.output, execute=True)
        self.assertFalse(result["live_diagnostic_pass"])
        self.assertEqual(result["session"]["generation"]["unknown_usage_attempts"], 1)
        self.assertEqual(result["session"]["generation"]["reserved_tokens"], 24)

    def test_count_exceeding_budget_prevents_generation(self):
        count = {"object": "response.input_tokens", "input_tokens": 256}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret"}), patch.object(probe, "OpenAITransport") as transport:
            transport.return_value.return_value = (200, probe.encode(count))
            result = probe.run(self.plan, self.output, execute=True)
        self.assertEqual(transport.return_value.call_count, 1)
        self.assertEqual(result["result"]["outcome"], "admission_denied")
        self.assertFalse(result["live_diagnostic_pass"])

    def test_wrong_returned_model_fails(self):
        count = {"object": "response.input_tokens", "input_tokens": 8}
        generation = {"status": "completed", "model": "different-model",
                      "usage": {"input_tokens": 8, "output_tokens": 2, "total_tokens": 10}}
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret"}), patch.object(probe, "OpenAITransport") as transport:
            transport.return_value.side_effect = [(200, probe.encode(count)), (200, probe.encode(generation))]
            result = probe.run(self.plan, self.output, execute=True)
        self.assertFalse(result["live_diagnostic_pass"])

    def test_external_output_directory_can_preserve_mock_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            external_output = Path(tmp) / "report.json"
            with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret"}), patch.object(probe, "OpenAITransport") as transport:
                transport.return_value.return_value = (503, b'{}')
                result = probe.run(self.plan, external_output, execute=True)
            self.assertTrue(external_output.exists())
            self.assertTrue(result["artifacts"])
            self.assertTrue(all("/" not in name for name in result["artifacts"]))


if __name__ == "__main__":
    unittest.main()
