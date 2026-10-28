import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import probe_openai_provider_v2 as probe
from openai_transport import OpenAITransport


class ProbeV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=probe.ROOT / ".research")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.plan = self.root / "plan.json"
        self.output = self.root / "report.json"
        self.write_plan()

    def write_plan(self, value=None):
        self.plan.write_bytes(probe.encode(probe.make_plan() if value is None else value))

    def invoke(self, replies, **kwargs):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret"}), \
                patch.object(probe, "OpenAITransport") as transport:
            transport.return_value.side_effect = replies
            report = probe.run(self.plan, self.output, execute=True, **kwargs)
        self.assertNotIn("synthetic-secret", self.output.read_text())
        return report, transport

    def success_replies(self, *, model="gpt-6-luna", input_tokens=8, output_tokens=2):
        count = {"object": "response.input_tokens", "input_tokens": 8}
        generation = {"status": "completed", "model": model,
                      "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                                "total_tokens": input_tokens + output_tokens}}
        return [(200, probe.encode(count)), (200, probe.encode(generation))]

    def test_mock_success_reconciles_durable_receipts_but_is_not_live_evidence(self):
        report, transport = self.invoke(self.success_replies())
        self.assertEqual(transport.return_value.call_count, 2)
        self.assertTrue(report["live_diagnostic_pass"])
        self.assertTrue(report["transport_reconciled"])
        self.assertEqual([r["path"] for r in report["transport_receipts"]],
                         ["/v1/responses/input_tokens", "/v1/responses"])
        self.assertEqual(report["execution_mode"], "execution_requested")
        self.assertFalse(report["live_provider_verified"])
        self.assertFalse(report["product_promotion_pass"])
        self.assertFalse(report["complete_accounting"])
        self.assertEqual(report["forwarded_attempts"], 2)
        self.assertEqual(report["denied_attempts"], 0)

    def test_openai_transport_with_mock_https_records_exact_posts_without_key(self):
        class Response:
            def __init__(self, body):
                self.status = 200
                self.body = body

            def getheader(self, name):
                return str(len(self.body)) if name == "Content-Length" else None

            def read(self, _limit):
                return self.body

        count = probe.encode({"object": "response.input_tokens", "input_tokens": 8})
        generation = probe.encode({"status": "completed", "model": "gpt-6-luna",
                                   "usage": {"input_tokens": 8, "output_tokens": 2,
                                             "total_tokens": 10}})
        responses = [Response(count), Response(generation)]
        calls = []

        class Connection:
            def __init__(self, *args, **kwargs):
                self.auth = None

            def request(self, method, path, body, headers):
                self.auth = headers["Authorization"]
                calls.append((method, path, body, headers))

            def getresponse(self):
                return responses.pop(0)

            def close(self):
                pass

        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret"}), \
                patch("openai_transport.http.client.HTTPSConnection", Connection), \
                patch.object(probe, "OpenAITransport",
                             return_value=OpenAITransport("synthetic-secret")):
            report = probe.run(self.plan, self.output, execute=True)
        self.assertTrue(report["live_diagnostic_pass"])
        self.assertEqual(report["execution_mode"], "execution_requested")
        self.assertFalse(report["live_provider_verified"])
        self.assertEqual([call[0:2] for call in calls], [
            ("POST", "/v1/responses/input_tokens"), ("POST", "/v1/responses")])
        self.assertTrue(all(call[3]["Authorization"] == "Bearer synthetic-secret" for call in calls))
        for path in (Path(report["artifact_directory"]), self.output):
            for file in path.rglob("*") if path.is_dir() else [path]:
                if file.is_file():
                    self.assertNotIn(b"synthetic-secret", file.read_bytes())

    def test_count_http_error_never_generates(self):
        report, transport = self.invoke([(503, b"{}")])
        self.assertEqual(transport.return_value.call_count, 1)
        self.assertFalse(report["live_diagnostic_pass"])
        self.assertEqual(report["forwarded_attempts"], 1)

    def test_detailed_usage_reconciles(self):
        replies = self.success_replies()
        generation = json.loads(replies[1][1])
        generation["usage"].update(input_tokens_details={"cached_tokens": 4},
                                   output_tokens_details={"reasoning_tokens": 1})
        replies[1] = (200, probe.encode(generation))
        report, _ = self.invoke(replies)
        self.assertTrue(report["live_diagnostic_pass"])

    def test_output_limit_interruption_with_usage_reconciles(self):
        replies = self.success_replies(output_tokens=16)
        generation = json.loads(replies[1][1])
        generation.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
        replies[1] = (200, probe.encode(generation))
        report, _ = self.invoke(replies)
        self.assertTrue(report["live_diagnostic_pass"])
        self.assertEqual(report["result"]["outcome"], "incomplete")

    def test_altered_journal_invalidates_reconciliation(self):
        report, _ = self.invoke(self.success_replies())
        directory = Path(report["artifact_directory"])
        journal = directory / "session" / "generation-ledger.jsonl"
        rows = [json.loads(row) for row in journal.read_text().splitlines()]
        rows[-1]["response_sha256"] = "0" * 64
        journal.write_bytes(b"".join(probe.encode(row) for row in rows))

        class Summary:
            def summary(self):
                return report["transport_summary"]

        self.assertFalse(probe._reconcile(directory, report["session"], Summary())[0])

    def test_generation_transport_error_fails_closed(self):
        report, transport = self.invoke([(200, probe.encode({"object": "response.input_tokens",
                                                               "input_tokens": 8})),
                                         RuntimeError("private detail")])
        self.assertEqual(transport.return_value.call_count, 2)
        self.assertFalse(report["live_diagnostic_pass"])
        self.assertEqual(report["session"]["generation"]["unknown_usage_attempts"], 1)
        self.assertNotIn("private detail", self.output.read_text())

    def test_missing_usage_fails_unknown(self):
        report, _ = self.invoke([(200, probe.encode({"object": "response.input_tokens", "input_tokens": 8})),
                                 (200, b'{"status":"completed","model":"gpt-6-luna"}')])
        self.assertFalse(report["live_diagnostic_pass"])
        self.assertEqual(report["session"]["generation"]["unknown_usage_attempts"], 1)

    def test_count_mismatch_fails(self):
        report, _ = self.invoke([(200, probe.encode({"object": "response.input_tokens", "input_tokens": 8})),
                                 (200, probe.encode({"status": "completed", "model": "gpt-6-luna",
                                                    "usage": {"input_tokens": 9, "output_tokens": 2,
                                                              "total_tokens": 11}}))])
        self.assertFalse(report["live_diagnostic_pass"])

    def test_wrong_model_fails(self):
        report, _ = self.invoke(self.success_replies(model="other"))
        self.assertFalse(report["live_diagnostic_pass"])

    def test_output_cap_blocks_generation(self):
        report, transport = self.invoke([(200, probe.encode({"object": "response.input_tokens",
                                                               "input_tokens": 256}))])
        self.assertEqual(transport.return_value.call_count, 1)
        self.assertEqual(report["result"]["outcome"], "admission_denied")
        self.assertFalse(report["live_diagnostic_pass"])

    def test_preflight_missing_key_never_constructs_transport(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(probe, "OpenAITransport") as transport:
            report = probe.run(self.plan, self.output, execute=True)
        transport.assert_not_called()
        self.assertEqual(report["blockers"], ["missing_OPENAI_API_KEY"])
        self.assertEqual(report["network_requests"], 0)
        self.assertFalse((self.root / "report-private").exists())
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)

    def test_preflight_does_not_construct_transport(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-secret"}), \
                patch.object(probe, "OpenAITransport") as transport:
            report = probe.run(self.plan, self.output)
        transport.assert_not_called()
        self.assertEqual(report["blockers"], ["execution_not_requested"])
        self.assertFalse((self.root / "report-private").exists())

    def test_source_pin_mismatch_rejected(self):
        plan = probe.make_plan()
        plan["sources"][probe.SOURCES[0]] = "0" * 64
        self.write_plan(plan)
        with patch.object(probe, "OpenAITransport") as transport:
            with self.assertRaisesRegex(ValueError, "source_pin_mismatch"):
                probe.run(self.plan, self.output, execute=True)
        transport.assert_not_called()
        self.assertFalse(self.output.exists())

    def test_plan_duplicate_key_and_boolean_limit_rejected(self):
        self.plan.write_bytes(b'{"id":"openai-provider-v2","id":"openai-provider-v2"}')
        with self.assertRaises(ValueError):
            probe.validate_plan(self.plan)
        plan = probe.make_plan()
        plan["limits"]["attempt_limit"] = True
        self.write_plan(plan)
        with self.assertRaisesRegex(ValueError, "unsupported_plan"):
            probe.validate_plan(self.plan)

    def test_float_request_value_rejected_even_when_numerically_equal(self):
        plan = probe.make_plan()
        plan["request"]["max_output_tokens"] = 16.0
        self.write_plan(plan)
        with self.assertRaisesRegex(ValueError, "unsupported_plan"):
            probe.validate_plan(self.plan)

    def test_extra_transport_receipt_invalidates_reconciliation(self):
        report, _ = self.invoke(self.success_replies())
        directory = Path(report["artifact_directory"])
        (directory / "transport" / "begin-000003.json").write_text("{}")

        class Summary:
            def summary(self):
                return {"attempts": 2, "denials": 0, "unknown_usage_attempts": 2,
                        "pending_attempts": 0, "poisoned": False}

        valid, _ = probe._reconcile(directory, report["session"], Summary())
        self.assertFalse(valid)

    def test_existing_output_is_preserved(self):
        self.output.write_text("keep")
        with patch.object(probe, "OpenAITransport") as transport:
            with self.assertRaises(FileExistsError):
                probe.run(self.plan, self.output, execute=True)
        transport.assert_not_called()
        self.assertEqual(self.output.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
