"""Focused tests for offline transport-level durability and bounds."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from recorded_transport import RecordedTransport, RecordedTransportError


class RecordedTransportTests(unittest.TestCase):
    def make(self, root, transport, **kwargs):
        return RecordedTransport(Path(root) / "record", transport, **kwargs)

    def test_exact_raw_bytes_and_receipts_for_success_and_http_error(self):
        with tempfile.TemporaryDirectory() as d:
            requests = [b"\x00payload\xff", b"same payload"]
            out = [(200, b"ok\x00"), (429, b"busy")]
            r = self.make(d, lambda *_: out.pop(0), attempt_limit=2)
            self.assertEqual(r("/v1/responses", requests[0]), (200, b"ok\x00"))
            self.assertEqual(r("/v1/responses", requests[1]), (429, b"busy"))
            root = Path(d, "record")
            self.assertEqual((root / "request-000001.bin").read_bytes(), requests[0])
            self.assertEqual((root / "response-000001.bin").read_bytes(), b"ok\x00")
            begin = json.loads((root / "begin-000001.json").read_text())
            end = json.loads((root / "end-000002.json").read_text())
            self.assertEqual(begin["request_sha256"], hashlib.sha256(requests[0]).hexdigest())
            self.assertEqual(begin["seq"], 1)
            self.assertEqual(end["outcome"], "http_error")
            self.assertEqual(end["status"], 429)
            self.assertEqual(end["response_sha256"], hashlib.sha256(b"busy").hexdigest())
            self.assertEqual(r.summary()["unknown_usage_attempts"], 2)
            self.assertFalse(r.summary()["complete_agent_accounting"])
            r.close()

    def test_repeated_hashes_are_distinct_attempts_and_cap_denial_is_durable(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            r = self.make(d, lambda p, b: (calls.append((p, b)) or (200, b"x")), attempt_limit=2)
            for _ in range(2):
                r("/v1/responses", b"repeat")
            with self.assertRaises(RecordedTransportError):
                r("/v1/responses", b"repeat")
            self.assertEqual(len(calls), 2)
            self.assertEqual(r.summary()["attempts"], 2)
            self.assertEqual(r.summary()["denials"], 1)
            self.assertEqual(json.loads(Path(d, "record", "denied-000003.json").read_text())["reason"], "attempt_limit")
            r.close()

    def test_pre_forward_persistence_failure_prevents_call_and_poisons(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            r = self.make(d, lambda *a: calls.append(a), attempt_limit=1)
            with patch("recorded_transport.os.fsync", side_effect=OSError("disk unavailable")):
                with self.assertRaises(RecordedTransportError):
                    r("/v1/responses", b"x")
            self.assertEqual(calls, [])
            self.assertTrue(r.summary()["poisoned"])

    def test_response_persistence_failure_after_forward_poisons_and_hides_response(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            r = self.make(d, lambda *a: (calls.append(a) or (200, b"secret response")), attempt_limit=1)
            real_fsync = __import__("os").fsync
            count = 0
            def fail_response_fsync(fd):
                nonlocal count
                count += 1
                # request, begin receipt file, begin directory, response file
                if count == 4:
                    raise OSError("disk full")
                return real_fsync(fd)
            with patch("recorded_transport.os.fsync", side_effect=fail_response_fsync):
                with self.assertRaises(RecordedTransportError):
                    r("/v1/responses", b"x")
            self.assertEqual(len(calls), 1)
            self.assertTrue(r.summary()["poisoned"])
            self.assertEqual(r.summary()["pending_attempts"], 1)
            with self.assertRaises(RecordedTransportError):
                r("/v1/responses", b"x")

    def test_transport_exception_is_sanitized_and_usage_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            r = self.make(d, lambda *_: (_ for _ in ()).throw(RuntimeError("api_key=secret")), attempt_limit=1)
            with self.assertRaisesRegex(RecordedTransportError, "RuntimeError") as caught:
                r("/v1/responses", b"x")
            self.assertNotIn("secret", str(caught.exception))
            end = json.loads(Path(d, "record", "end-000001.json").read_text())
            self.assertEqual(end["exception_type"], "RuntimeError")
            self.assertEqual(end["usage"], "unknown")
            self.assertEqual(r.summary()["pending_attempts"], 0)
            r.close()

    def test_keyboard_interrupt_is_recorded_and_poisons_before_reraising(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            def interrupted(*_):
                calls.append(1)
                raise KeyboardInterrupt("private detail")
            r = self.make(d, interrupted, attempt_limit=2)
            with self.assertRaises(KeyboardInterrupt):
                r("/v1/responses", b"x")
            self.assertEqual(len(calls), 1)
            self.assertTrue(r.summary()["poisoned"])
            receipt = json.loads(Path(d, "record", "end-000001.json").read_text())
            self.assertEqual(receipt["exception_type"], "KeyboardInterrupt")
            self.assertNotIn("private detail", (Path(d, "record", "end-000001.json").read_text()))
            with self.assertRaises(RecordedTransportError):
                r("/v1/responses", b"x")

    def test_bounds_denials_and_oversized_response(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            r = self.make(d, lambda *a: (calls.append(a) or (200, b"12345")), attempt_limit=2,
                          max_request_bytes=2, max_response_bytes=4)
            with self.assertRaises(RecordedTransportError):
                r("/v1/responses", b"big")
            self.assertEqual(calls, [])
            with self.assertRaises(RecordedTransportError):
                r("/v1/responses", b"ok")
            self.assertEqual(len(calls), 1)
            self.assertTrue(r.summary()["poisoned"])
            receipt = json.loads(Path(d, "record", "end-000001.json").read_text())
            self.assertFalse(receipt["raw_saved"])
            self.assertEqual(receipt["response_bytes"], 5)

    def test_concurrent_calls_serialize_and_closed_invalid_inputs_refuse(self):
        with tempfile.TemporaryDirectory() as d:
            active = maximum = 0
            guard = threading.Lock()
            def transport(*_):
                nonlocal active, maximum
                with guard:
                    active += 1
                    maximum = max(maximum, active)
                try:
                    return 200, b"ok"
                finally:
                    with guard: active -= 1
            r = self.make(d, transport, attempt_limit=4)
            errors = []
            ts = [threading.Thread(target=lambda: r("/v1/responses", b"x")) for _ in range(4)]
            for t in ts: t.start()
            for t in ts: t.join()
            self.assertEqual(maximum, 1)
            for args in (("https://example.test", b"x"), ("/v1/responses", "x")):
                with self.assertRaises((ValueError, TypeError)):
                    r(*args)
            r.close()
            with self.assertRaises(RecordedTransportError):
                r("/v1/responses", b"x")

    def test_strict_limits_and_existing_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            for limit in (True, -1, 1.0):
                with self.assertRaises(ValueError):
                    self.make(d, lambda *_: None, attempt_limit=limit)
            Path(d, "target").mkdir()
            Path(d, "link").symlink_to(Path(d, "target"), target_is_directory=True)
            with self.assertRaises(FileExistsError):
                RecordedTransport(Path(d, "link"), lambda *_: None, 1)

    def test_malformed_responses_get_terminal_receipt_and_latch(self):
        for response in (None, (True, b"x"), (99, b"x"), (600, b"x"),
                         (200, "x"), [200, b"x"]):
            with self.subTest(response=response), tempfile.TemporaryDirectory() as d:
                calls = []
                r = self.make(d, lambda *_: (calls.append(1) or response), attempt_limit=2)
                with self.assertRaises(RecordedTransportError):
                    r("/v1/responses", b"x")
                end = json.loads(Path(d, "record", "end-000001.json").read_text())
                self.assertEqual(end["outcome"], "malformed_response")
                self.assertTrue(r.summary()["poisoned"])
                self.assertEqual(r.summary()["pending_attempts"], 0)
                with self.assertRaises(RecordedTransportError):
                    r("/v1/responses", b"x")
                self.assertEqual(calls, [1])

    def test_directory_fsync_and_short_write_fail_before_forward(self):
        for failure in ("directory", "write", "cancel"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as d:
                calls = []
                r = self.make(d, lambda *a: calls.append(a), attempt_limit=1)
                target, kwargs = {
                    "directory": ("_sync_dir", {"side_effect": OSError("private detail")}),
                    "write": ("_write", {"side_effect": OSError("short write")}),
                    "cancel": ("_write", {"side_effect": KeyboardInterrupt()}),
                }[failure]
                with patch.object(r, target, **kwargs):
                    with self.assertRaises(RecordedTransportError):
                        r("/v1/responses", b"x")
                self.assertEqual(calls, [])
                self.assertTrue(r.summary()["poisoned"])

    def test_invalid_input_denials_do_not_expose_arbitrary_paths(self):
        with tempfile.TemporaryDirectory() as d:
            calls = []
            r = self.make(d, lambda *a: calls.append(a), attempt_limit=2)
            for path in ([], "https://secret.example/key"):
                with self.assertRaises(ValueError):
                    r(path, b"x")
            self.assertEqual(r.summary()["denials"], 2)
            self.assertEqual(calls, [])
            for receipt in Path(d, "record").glob("denied-*.json"):
                value = json.loads(receipt.read_text())
                self.assertIsNone(value["path"])
                self.assertNotIn("secret", receipt.read_text())


if __name__ == "__main__":
    unittest.main()
