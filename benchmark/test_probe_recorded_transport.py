"""Offline integration and independent receipt reconciliation checks."""
from pathlib import Path
import tempfile
import unittest

from probe_recorded_transport import reconcile, run


class ProbeTests(unittest.TestCase):
    def test_failure_matrix_and_shared_cap(self):
        with tempfile.TemporaryDirectory() as root:
            report = run(Path(root) / "diagnostic")
            self.assertEqual(sum(s["verification"]["attempts_verified"] for s in report["scenarios"]), 10)
            self.assertEqual(sum(s["verification"]["denials_verified"] for s in report["scenarios"]), 1)
            self.assertEqual(report["network_requests"], 0)
            self.assertFalse(report["product_promotion_pass"])
            retry = report["scenarios"][-1]
            self.assertEqual(retry["session"]["generation"]["accepted_attempts"], 2)
            self.assertEqual(retry["session"]["generation"]["unknown_usage_attempts"], 1)
            self.assertEqual(retry["transport"]["attempts"], 3)

    def test_reconciliation_refuses_corruption_and_missing_attempt(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root) / "diagnostic"
            report = run(directory)
            case = report["scenarios"][0]
            transport = directory / "completed" / "transport"
            raw = transport / "request-000001.bin"
            original = raw.read_bytes()
            raw.write_bytes(b"tampered")
            with self.assertRaises(AssertionError):
                reconcile(transport, case["forwarded"], case["transport"])
            raw.write_bytes(original)
            (transport / "end-000001.json").unlink()
            with self.assertRaises(AssertionError):
                reconcile(transport, case["forwarded"], case["transport"])


if __name__ == "__main__":
    unittest.main()
