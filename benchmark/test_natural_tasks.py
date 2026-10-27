"""Natural tasks revert a real fix in several places and hide the fix's tests until grading."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import agent_trials
import check_tasks
import task_session


class NaturalTaskTests(unittest.TestCase):
    def test_a_timed_out_verifier_leaves_no_process_behind(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "child.pid"
            command = ["sh", "-c", f"sleep 30 & echo $! > {marker}; sleep 30"]
            with self.assertRaises(agent_trials.subprocess.TimeoutExpired):
                agent_trials.run_group(command, temp, None, timeout=1)
            child = int(marker.read_text())
            with self.assertRaises(ProcessLookupError):
                agent_trials.os.kill(child, 0)

    def test_a_file_the_fix_added_and_upstream_later_removed_is_already_faulty(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "kept.md").write_text("added by the fix\n")
            agent_trials._apply(root, [{"path": "kept.md", "added": True}, {"path": ".changeset/gone.md", "added": True}])
            self.assertFalse((root / "kept.md").exists())

    def test_a_fix_added_file_absent_from_the_corpus_reads_as_absent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "kept.md").write_text("added by the fix\n")
            self.assertEqual(check_tasks.texts(root, ["kept.md", ".changeset/gone.md"]),
                             {"kept.md": "added by the fix\n", ".changeset/gone.md": None})

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.patches = [patch.object(agent_trials, "BASE", self.base), patch.object(task_session, "BASE", self.base)]
        for p in self.patches:
            p.start()
        root = self.base / ".research/corpora/fixture"
        (root / "tests").mkdir(parents=True)
        (root / "app.py").write_text("def value():\n    return fixed()\n")
        (root / "util.py").write_text("def fixed():\n    return 1\n")
        (root / "CHANGES.md").write_text("- Fixed value (#12)\n- Older entry\n")
        (root / "tests/test_app.py").write_text("def test_old():\n    pass\n\ndef test_fix():\n    assert post\n")
        (root / "tests/test_new.py").write_text("def test_regression():\n    pass\n")
        self.task = {
            "id": "natural", "repo": "fixture", "issue": "value() is wrong",
            "faults": [{"path": "app.py", "before": "return fixed()", "after": "return 2"},
                       {"path": "util.py", "before": "def fixed():\n    return 1\n", "after": ""},
                       {"path": "CHANGES.md", "before": "- Fixed value (#12)\n", "after": ""}],
            "hidden_tests": [{"path": "tests/test_app.py", "before": "\ndef test_fix():\n    assert post\n", "after": ""},
                             {"path": "tests/test_new.py", "added": True}],
            "verify": ["sh", "-c", "test -f tests/test_new.py && grep -q 'assert post' tests/test_app.py "
                                   "&& grep -q 'return fixed()' app.py"]}

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def prepare(self, trial="natural-trial"):
        with contextlib.redirect_stdout(io.StringIO()):
            agent_trials.prepare(self.task, trial)
        return self.base / ".research/trials" / trial / "workspace"

    def verify(self, workspace):
        with contextlib.redirect_stdout(io.StringIO()):
            return agent_trials.verify(self.task, workspace)

    def test_prepare_reverts_every_place_and_hides_the_fix_tests(self):
        workspace = self.prepare()
        self.assertEqual((workspace / "app.py").read_text(), "def value():\n    return 2\n")
        self.assertEqual((workspace / "util.py").read_text(), "")
        self.assertNotIn("#12", (workspace / "CHANGES.md").read_text())
        self.assertNotIn("test_fix", (workspace / "tests/test_app.py").read_text())
        self.assertFalse((workspace / "tests/test_new.py").exists())

    def test_grading_installs_hidden_tests_then_restores_the_agent_view(self):
        workspace = self.prepare()
        self.assertNotEqual(self.verify(workspace), 0)
        (workspace / "app.py").write_text("def value():\n    return fixed()\n")
        self.assertEqual(self.verify(workspace), 0)
        self.assertFalse((workspace / "tests/test_new.py").exists())
        self.assertNotIn("test_fix", (workspace / "tests/test_app.py").read_text())

    def test_session_tools_never_see_hidden_tests(self):
        workspace = self.prepare()
        session = task_session.Session(self.task, "natural-trial", "search_only")
        found = session.call("search", {"pattern": "assert post|test_regression"})
        self.assertTrue(found["ok"])
        self.assertEqual(found["result"]["content"], "")
        self.assertFalse(session.call("read", {"path": "tests/test_new.py"})["ok"])
        self.assertTrue((workspace / "tests/test_app.py").is_file())

    def test_every_fault_and_hidden_test_must_match_once(self):
        self.task["faults"][0]["before"] = "e"
        with self.assertRaises(ValueError):
            self.prepare("ambiguous")
        self.task["faults"][0]["before"] = "return fixed()"
        self.task["hidden_tests"][0]["before"] = "missing"
        with self.assertRaises(ValueError):
            self.prepare("unmatched")

    def test_tasks_link_their_declared_node_runtime(self):
        (self.base / ".research/task-runtime/fixture-full/node_modules").mkdir(parents=True)
        self.task["runtime"] = "fixture-full"
        workspace = self.prepare("with-runtime")
        self.assertEqual((workspace / "node_modules").resolve(),
                         (self.base / ".research/task-runtime/fixture-full/node_modules").resolve())
        del self.task["runtime"]
        self.assertFalse((self.prepare("without-runtime") / "node_modules").exists())

    def test_single_fault_tasks_still_work(self):
        legacy = {"id": "natural", "repo": "fixture", "issue": "x", "verify": ["true"],
                  "fault": {"path": "app.py", "before": "return fixed()", "after": "return 2"}}
        self.assertEqual(agent_trials.task_faults(legacy), [legacy["fault"]])
        self.assertEqual(agent_trials.task_faults(self.task), self.task["faults"])


if __name__ == "__main__":
    unittest.main()
