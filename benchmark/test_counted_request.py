"""Focused safety tests for counted_request's deterministic fixture pipeline."""
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from counted_request import CountedFixtureSession


def response_usage(input_tokens=3, output_tokens=2, reasoning=0):
    return json.dumps({"status": "completed", "usage": {
        "input_tokens": input_tokens, "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "output_tokens_details": {"reasoning_tokens": reasoning}}}).encode()


def count_response(n=3):
    return json.dumps({"object": "response.input_tokens", "input_tokens": n}).encode()


class CountedRequestTests(unittest.TestCase):
    def session(self, root, transport, *, tokens=100, attempts=10, counts=10):
        return CountedFixtureSession(Path(root) / "session", transport, tokens, attempts, counts)

    def test_count_projection_and_generation_bytes_are_exact(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            body = b'{"model":"fixture","input":"hello","instructions":"be brief","max_output_tokens":16}'
            def transport(path, data):
                calls.append((path, data))
                if path.endswith("input_tokens"):
                    self.assertEqual(data, b'{"input":"hello","instructions":"be brief","model":"fixture"}\n')
                    return 200, count_response()
                return 200, response_usage()
            with self.session(d, transport) as s:
                self.assertEqual(s.run(body)["outcome"], "completed")
                self.assertEqual(calls[1], ("/v1/responses", body))
                self.assertEqual(Path(d, "session", "generation-request-000001.json").read_bytes(), body)
                self.assertEqual(s.summary()["generation"]["actual_observed_tokens"], 5)

    def test_count_failure_does_not_generate_and_retry_counts_again(self):
        with tempfile.TemporaryDirectory() as d:
            paths = []
            def transport(path, body):
                paths.append(path)
                if len(paths) == 1:
                    return 500, b'{"error":"fixture"}'
                return (200, count_response()) if path.endswith("input_tokens") else (200, response_usage())
            with self.session(d, transport, counts=2) as s:
                body = b'{"model":"x","input":"y","max_output_tokens":16}'
                self.assertEqual(s.run(body)["outcome"], "count_failed")
                self.assertEqual(paths, ["/v1/responses/input_tokens"])
                self.assertEqual(s.run(body)["outcome"], "completed")
                self.assertEqual(s.summary()["count_attempts"], 2)
                self.assertEqual(s.summary()["count_failures"], 1)

    def test_bad_count_shapes_fail_closed_before_generation(self):
        for payload in (b'{"object":"response.input_tokens","input_tokens":true}',
                        b'{"object":"response.input_tokens","input_tokens":-1}',
                        b'{"object":"wrong","input_tokens":2}',
                        b'{"object":"response.input_tokens","input_tokens":NaN}'):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as d:
                paths = []
                def transport(path, _body):
                    paths.append(path)
                    return 200, payload
                with self.session(d, transport) as s:
                    out = s.run(b'{"model":"x","input":"y","max_output_tokens":16}')
                    self.assertEqual(out["outcome"], "count_failed")
                    self.assertEqual(paths, ["/v1/responses/input_tokens"])

    def test_generation_failure_is_accounted_and_later_run_can_reserve(self):
        with tempfile.TemporaryDirectory() as d:
            generation_n = 0
            def transport(path, _body):
                nonlocal generation_n
                if path.endswith("input_tokens"):
                    return 200, count_response()
                generation_n += 1
                if generation_n == 1:
                    raise OSError("fixture failure")
                return 200, response_usage()
            with self.session(d, transport, tokens=40, attempts=2) as s:
                body = b'{"model":"x","input":"y","max_output_tokens":16}'
                self.assertEqual(s.run(body)["outcome"], "transport_error")
                self.assertEqual(s.run(body)["outcome"], "completed")
                summary = s.summary()["generation"]
                self.assertEqual(summary["accepted_attempts"], 2)
                self.assertEqual(summary["unknown_usage_attempts"], 1)
                self.assertEqual(summary["pending_attempts"], 0)

    def test_disk_failure_before_count_prevents_transport(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            def transport(path, body):
                calls.append(path)
                return 200, count_response()
            s = self.session(d, transport)
            def fail_journal(_event):
                s._poisoned = True
                raise OSError("fixture disk failure")
            with patch.object(s, "_append_count", fail_journal):
                with self.assertRaises(Exception):
                    s.run(b'{"model":"x","input":"y","max_output_tokens":16}')
            self.assertEqual(calls, [])
            self.assertTrue(s.summary()["poisoned"])
            s.close()

    def test_raw_preservation_failure_prevents_count_transport(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            def transport(path, body):
                calls.append(path)
                return 200, count_response()
            s = self.session(d, transport)
            with patch("counted_request.os.open", side_effect=OSError("disk full")):
                with self.assertRaises(Exception):
                    s.run(b'{"model":"x","input":"y","max_output_tokens":16}')
            self.assertEqual(calls, [])
            self.assertTrue(s.summary()["poisoned"])
            s.close()

    def test_generation_journal_failure_blocks_later_count_attempt(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            def transport(path, body):
                calls.append(path)
                return 200, count_response()
            s = self.session(d, transport)
            def fail_generation_journal(_event):
                s._generation._poisoned = True
                raise RuntimeError("fsync failed")
            with patch.object(s._generation, "_append", fail_generation_journal):
                with self.assertRaises(Exception):
                    s.run(b'{"model":"x","input":"y","max_output_tokens":16}')
            self.assertEqual(calls, ["/v1/responses/input_tokens"])
            self.assertTrue(s.summary()["poisoned"])
            with self.assertRaises(Exception):
                s.run(b'{"model":"x","input":"y","max_output_tokens":16}')
            self.assertEqual(calls, ["/v1/responses/input_tokens"])
            s.close()

    def test_concurrent_runs_obey_limits_and_serialize(self):
        with tempfile.TemporaryDirectory() as d:
            active = 0
            maximum = 0
            guard = threading.Lock()
            def transport(path, _body):
                nonlocal active, maximum
                with guard:
                    active += 1
                    maximum = max(maximum, active)
                try:
                    if path.endswith("input_tokens"):
                        return 200, count_response(1)
                    return 200, response_usage(1, 1)
                finally:
                    with guard:
                        active -= 1
            s = self.session(d, transport, tokens=34, attempts=2, counts=3)
            body = b'{"model":"x","input":"y","max_output_tokens":16}'
            results = []
            threads = [threading.Thread(target=lambda: results.append(s.run(body))) for _ in range(3)]
            for t in threads: t.start()
            for t in threads: t.join()
            self.assertLessEqual(maximum, 1)
            self.assertEqual(s.summary()["count_attempts"], 3)
            self.assertEqual(s.summary()["generation"]["accepted_attempts"], 2)
            self.assertEqual(sum(r["outcome"] == "completed" for r in results), 2)
            s.close()

    def test_incomplete_usage_includes_reasoning_tokens(self):
        with tempfile.TemporaryDirectory() as d:
            def transport(path, _body):
                if path.endswith("input_tokens"): return 200, count_response(3)
                return 200, json.dumps({"status": "incomplete",
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5,
                              "output_tokens_details": {"reasoning_tokens": 2}}}).encode()
            with self.session(d, transport) as s:
                self.assertEqual(s.run(b'{"model":"x","input":"y","max_output_tokens":16}')
                                 ["outcome"], "incomplete")
                summary = s.summary()["generation"]
                self.assertEqual(summary["actual_observed_tokens"], 5)
                self.assertEqual(summary["unknown_usage_attempts"], 0)

    def test_overshoot_poisons_and_unknown_usage_is_not_zero(self):
        with tempfile.TemporaryDirectory() as d:
            def transport(path, _body):
                if path.endswith("input_tokens"): return 200, count_response(2)
                return 200, response_usage(3, 1)
            s = self.session(d, transport)
            with self.assertRaises(Exception):
                s.run(b'{"model":"x","input":"y","max_output_tokens":16}')
            self.assertTrue(s.summary()["poisoned"])
            self.assertEqual(s.summary()["generation"]["actual_observed_tokens"], 0)
            self.assertEqual(s.summary()["generation"]["unknown_usage_attempts"], 1)
            with self.assertRaises(Exception):
                s.run(b'{"model":"x","input":"y","max_output_tokens":16}')
            s.close()

    def test_unsupported_duplicate_requests_rejected_before_count(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            def transport(path, body):
                calls.append(path)
                return 200, count_response()
            with self.session(d, transport) as s:
                bodies = [b'{"model":"x","input":"y","max_output_tokens":16,"tools":[]}',
                          b'{"model":"x","model":"z","input":"y","max_output_tokens":16}']
                for body in bodies:
                    self.assertEqual(s.run(body)["outcome"], "rejected")
                self.assertEqual(calls, [])
                self.assertEqual(s.summary()["count_attempts"], 0)

    def test_existing_journals_are_never_overwritten(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "session").mkdir()
            Path(d, "session", "count-journal.jsonl").write_bytes(b"keep")
            with self.assertRaises(FileExistsError):
                self.session(d, lambda *_: (200, count_response()))
            self.assertEqual(Path(d, "session", "count-journal.jsonl").read_bytes(), b"keep")

    def test_missing_usage_remains_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            def transport(path, _body):
                return (200, count_response()) if path.endswith("input_tokens") else (200, b'{"status":"completed"}')
            with self.session(d, transport) as s:
                self.assertEqual(s.run(b'{"model":"x","input":"y","max_output_tokens":16}')
                                 ["outcome"], "usage_unknown")
                self.assertEqual(s.summary()["generation"]["actual_observed_tokens"], 0)
                self.assertEqual(s.summary()["generation"]["unknown_usage_attempts"], 1)


if __name__ == "__main__":
    unittest.main()
