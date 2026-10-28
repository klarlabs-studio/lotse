"""Injected-transport tests for the experimental unverified provider session."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from provider_ledger import ProviderLedger
from provider_request import ProviderRequestSession


def encoded(value):
    return json.dumps(value, separators=(",", ":")).encode()


def counted(n=3):
    return encoded({"object": "response.input_tokens", "input_tokens": n})


def generated(input_tokens=3, output_tokens=2):
    return encoded({"status": "completed", "usage": {
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens}})


BODY = b'{"model":"x","input":"y","max_output_tokens":16}'


class ProviderRequestTests(unittest.TestCase):
    def test_direct_ledger_rejects_unsupported_request_fields(self):
        with tempfile.TemporaryDirectory() as root:
            with ProviderLedger(Path(root) / "ledger.jsonl", 100, 4) as ledger:
                for field, value in (("stream", True), ("store", False),
                                     ("tool_choice", "auto"), ("input", [])):
                    body = encoded(dict(json.loads(BODY), **{field: value}))
                    bound = {"source": "openai_input_tokens", "request_sha256": hashlib.sha256(body).hexdigest(),
                             "model": "x", "input_tokens": 3, "count_response_sha256": "a" * 64}
                    self.assertIsNone(ledger.admit(body, bound))
                self.assertEqual(ledger.summary()["reserved_tokens"], 0)

    def session(self, root, transport, *, tokens=100, attempts=4, counts=4):
        return ProviderRequestSession(Path(root) / "session", transport,
                                      tokens, attempts, counts)

    def test_provenance_and_count_response_hash_are_preserved_on_admit(self):
        with tempfile.TemporaryDirectory() as root:
            count_body = counted()

            def transport(path, _body):
                return (200, count_body) if path.endswith("input_tokens") else (200, generated())

            with self.session(root, transport) as session:
                self.assertEqual(session.run(BODY)["outcome"], "completed")
                count_events = [json.loads(line) for line in Path(root, "session", "count-journal.jsonl").read_text().splitlines()]
                generation_events = [json.loads(line) for line in Path(root, "session", "generation-ledger.jsonl").read_text().splitlines()]
                self.assertEqual(count_events[0]["scope"], "provider_protocol_unverified")
                admitted = next(event for event in generation_events if event["event"] == "admitted")
                self.assertEqual(admitted["count_response_sha256"], hashlib.sha256(count_body).hexdigest())
                self.assertEqual(session.summary()["scope"], "provider_protocol_unverified")
                self.assertFalse(session.summary()["complete_accounting"])
                self.assertEqual(session.summary()["agent_savings"], "unmeasured")

    def test_provider_ledger_rejects_synthetic_fixture_provenance(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = ProviderLedger(Path(root) / "ledger.jsonl", 100, 1)
            bound = {"source": "synthetic_fixture", "request_sha256": hashlib.sha256(BODY).hexdigest(),
                     "model": "x", "input_tokens": 3, "count_response_sha256": "a" * 64}
            self.assertIsNone(ledger.admit(BODY, bound))
            ledger.close()

    def test_bound_requires_exact_hex64_count_response_hash(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = ProviderLedger(Path(root) / "ledger.jsonl", 100, 1)
            base = {"source": "openai_input_tokens", "request_sha256": hashlib.sha256(BODY).hexdigest(),
                    "model": "x", "input_tokens": 3}
            for value in (None, "f" * 63, "F" * 64, "z" * 64):
                bound = {**base, "count_response_sha256": value}
                self.assertIsNone(ledger.admit(BODY, bound))
            ledger.close()

    def test_missing_usage_is_unknown_and_provider_count_is_upper_bound(self):
        with tempfile.TemporaryDirectory() as root:
            def transport(path, _body):
                return (200, counted(8)) if path.endswith("input_tokens") else (200, b'{"status":"completed"}')
            with self.session(root, transport) as session:
                self.assertEqual(session.run(BODY)["outcome"], "usage_unknown")
                summary = session.summary()["generation"]
                self.assertEqual(summary["unknown_usage_attempts"], 1)
                self.assertEqual(summary["actual_observed_tokens"], 0)
                self.assertEqual(summary["reserved_tokens"], 24)

    def test_cap_and_attempt_reservations_apply(self):
        with tempfile.TemporaryDirectory() as root:
            def transport(path, _body):
                return (200, counted(3)) if path.endswith("input_tokens") else (200, generated())
            with self.session(root, transport, tokens=18, attempts=2, counts=3) as session:
                self.assertEqual(session.run(BODY)["outcome"], "admission_denied")
                self.assertEqual(session.summary()["generation"]["accepted_attempts"], 0)
                self.assertEqual(session.summary()["generation"]["denied_requests"], 1)

    def test_concurrent_runs_are_serialized_and_reservations_stay_bounded(self):
        import threading
        with tempfile.TemporaryDirectory() as root:
            active = 0
            maximum = 0
            guard = threading.Lock()
            def transport(path, _body):
                nonlocal active, maximum
                with guard:
                    active += 1
                    maximum = max(maximum, active)
                try:
                    return (200, counted(1)) if path.endswith("input_tokens") else (200, generated(1, 1))
                finally:
                    with guard:
                        active -= 1
            session = self.session(root, transport, tokens=34, attempts=2, counts=3)
            results = []
            threads = [threading.Thread(target=lambda: results.append(session.run(BODY))) for _ in range(3)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(maximum, 1)
            self.assertEqual(session.summary()["generation"]["accepted_attempts"], 2)
            self.assertEqual(sum(result["outcome"] == "completed" for result in results), 2)
            session.close()

    def test_transport_failure_has_sanitized_type_and_no_generation(self):
        with tempfile.TemporaryDirectory() as root:
            paths = []
            def transport(path, _body):
                paths.append(path)
                raise OSError("secret provider response detail")
            with self.session(root, transport) as session:
                result = session.run(BODY)
                self.assertEqual(result, {"outcome": "count_failed", "reason": "OSError"})
                self.assertEqual(paths, ["/v1/responses/input_tokens"])

    def test_incomplete_with_usage_is_accounted(self):
        with tempfile.TemporaryDirectory() as root:
            def transport(path, _body):
                if path.endswith("input_tokens"):
                    return 200, counted()
                return 200, encoded({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                                     "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}})
            with self.session(root, transport) as session:
                self.assertEqual(session.run(BODY)["outcome"], "incomplete")
                self.assertEqual(session.summary()["generation"]["unknown_usage_attempts"], 0)


if __name__ == "__main__":
    unittest.main()
