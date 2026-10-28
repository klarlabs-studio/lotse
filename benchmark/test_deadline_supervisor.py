import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import deadline_supervisor as supervisor


@unittest.skipUnless(os.name == "posix", "requires POSIX process groups")
class DeadlineSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def invoke(self, name, code, timeout=3):
        return supervisor.run(self.root / name, [sys.executable, "-c", code], timeout_seconds=timeout)

    def test_exact_stdout_stderr_and_success_outcome(self):
        result = self.invoke("success", "import os; os.write(1,b'a\\x00b'); os.write(2,b'err')")
        self.assertEqual(result["outcome"], "completed")
        self.assertEqual((self.root / "success/stdout.bin").read_bytes(), b"a\0b")
        self.assertEqual((self.root / "success/stderr.bin").read_bytes(), b"err")
        self.assertEqual(result["stdout_bytes"], 3)
        self.assertEqual(result["stderr_bytes"], 3)
        self.assertFalse(result["usage_accounting_complete"])
        self.assertEqual(result["savings"], "unmeasured")

    def test_nonzero_exit(self):
        result = self.invoke("nonzero", "import sys; print('partial'); sys.exit(7)")
        self.assertEqual(result["outcome"], "nonzero_exit")
        self.assertEqual(result["returncode"], 7)

    def _descendant_worker(self, marker, delay="1.0", leader_delay="0.2"):
        return ("import subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c',\"import time,pathlib; time.sleep(" + delay + "); pathlib.Path(" +
                repr(str(marker)) + ").write_text('alive')\"]); "
                "time.sleep(" + leader_delay + ")")

    def test_normal_leader_exit_kills_descendant(self):
        marker = self.root / "normal-descendant-survived"
        result = self.invoke("normal-descendant", self._descendant_worker(marker))
        self.assertEqual(result["outcome"], "completed")
        time.sleep(1.2)
        self.assertFalse(marker.exists())

    def test_timeout_kills_leader_and_descendant(self):
        marker = self.root / "timeout-descendant-survived"
        result = self.invoke("timed-descendant", self._descendant_worker(marker, "1.5", "5"), timeout=0.4)
        self.assertEqual(result["outcome"], "timeout")
        self.assertTrue(result["deadline_exceeded"])
        time.sleep(1.7)
        self.assertFalse(marker.exists())

    def test_validation_and_fresh_directory(self):
        for args, timeout in (([], 2), ([""], 2), (["true"], 0), (["true"], 1200.1),
                              (["true"], float("inf")), (["true"], True), (["true"], "1")):
            with self.subTest(args=args, timeout=timeout), self.assertRaises(ValueError):
                supervisor.run(self.root / "bad" / (str(len(args)) + str(timeout)), args, timeout_seconds=timeout)
        target = self.root / "already-there"
        target.mkdir()
        with self.assertRaises(FileExistsError):
            supervisor.run(target, ["true"], timeout_seconds=2)

    def test_spawn_error_is_recorded_without_exception_text(self):
        result = supervisor.run(self.root / "missing", [str(self.root / "does-not-exist")], timeout_seconds=2)
        self.assertEqual(result["outcome"], "spawn_error")
        journal = (self.root / "missing/events.jsonl").read_text()
        self.assertIn('"event":"spawn_error"', journal)
        self.assertNotIn("No such file", journal)

    def test_private_modes_and_journal_sequence(self):
        directory = self.root / "modes"
        result = supervisor.run(directory, [sys.executable, "-c", "pass"], timeout_seconds=2,
                               env={**os.environ, "SUPERVISOR_TEST_SECRET": "never-persist-this"})
        self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
        for name in ("stdout.bin", "stderr.bin", "events.jsonl"):
            self.assertEqual((directory / name).stat().st_mode & 0o777, 0o600)
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        self.assertEqual([e["seq"] for e in events], list(range(1, len(events) + 1)))
        self.assertEqual(json.loads((directory / "report.json").read_text()), result)
        self.assertNotIn("never-persist-this", (directory / "events.jsonl").read_text())
        self.assertNotIn("never-persist-this", (directory / "report.json").read_text())

    def test_persistence_failure_after_spawn_kills_process_group(self):
        marker = self.root / "persistence-descendant-survived"
        original = supervisor._event
        def failing(fd, seq, name, **fields):
            if name == "spawned":
                raise OSError("private simulated failure")
            original(fd, seq, name, **fields)
        with patch.object(supervisor, "_event", side_effect=failing):
            with self.assertRaises(OSError):
                self.invoke("persist-failure", self._descendant_worker(marker, "1.0"))
        time.sleep(1.2)
        self.assertFalse(marker.exists())

    def test_cancellation_propagates_after_group_cleanup(self):
        marker = self.root / "cancel-descendant-survived"
        original = supervisor._event
        def cancelling(fd, seq, name, **fields):
            if name == "spawned":
                raise KeyboardInterrupt()
            original(fd, seq, name, **fields)
        with patch.object(supervisor, "_event", side_effect=cancelling):
            with self.assertRaises(KeyboardInterrupt):
                self.invoke("cancel", self._descendant_worker(marker, "1.0", "5"))
        time.sleep(1.2)
        self.assertFalse(marker.exists())

    def test_unverified_group_exit_fails_closed(self):
        with patch.object(supervisor, "_group_gone", return_value=False):
            result = self.invoke("unknown-group", "pass")
        self.assertEqual(result["outcome"], "reap_unknown")
        self.assertFalse(result["group_exit_verified"])
        self.assertFalse(result["output_capture_complete"])
        self.assertFalse(result["agent_efficiency_acceptance_pass"])

    def test_deadline_consumed_by_journaling_prevents_spawn(self):
        with patch.object(supervisor.time, "monotonic", side_effect=[0.0, 1.0]), \
                patch.object(supervisor.subprocess, "Popen") as popen:
            result = supervisor.run(self.root / "expired-before-spawn", [sys.executable, "-c", "pass"],
                                    timeout_seconds=0.5)
        popen.assert_not_called()
        self.assertEqual(result["outcome"], "timeout")
        self.assertTrue(result["deadline_exceeded"])
        events = (self.root / "expired-before-spawn/events.jsonl").read_text()
        self.assertIn('"event":"timeout_before_spawn"', events)

    def test_timeout_records_partial_bytes_and_monotonic_scope(self):
        started = time.monotonic()
        result = self.invoke("partial", "import os,time; os.write(1,b'partial'); os.write(2,b'err'); time.sleep(5)", timeout=0.3)
        elapsed = time.monotonic() - started
        self.assertEqual(result["outcome"], "timeout")
        self.assertEqual((self.root / "partial/stdout.bin").read_bytes(), b"partial")
        self.assertEqual((self.root / "partial/stderr.bin").read_bytes(), b"err")
        self.assertGreaterEqual(elapsed, 0.2)
        self.assertLess(elapsed, 8.0)


if __name__ == "__main__":
    unittest.main()
