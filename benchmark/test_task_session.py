"""Broker integrity checks only: these do not run a repair agent."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

import agent_trials
import task_session


class TaskBrokerIntegrity(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.patches = [patch.object(agent_trials, "BASE", self.base), patch.object(task_session, "BASE", self.base)]
        for p in self.patches:
            p.start()
        self.task = {"id": "fixture", "repo": "fixture", "issue": "Repair the boundary behavior.",
                     "fault": {"path": "app.py", "before": "return 1", "after": "return 2"},
                     "verify": ["true"]}
        root = self.base / ".research/corpora/fixture"
        (root / ".graf").mkdir(parents=True)
        (root / ".graf/index.json").write_text('clean source: return 1')
        (root / "graphify-out").mkdir()
        (root / "graphify-out/graph.json").write_text('clean source: return 1')
        (root / "tests").mkdir()
        (root / "tests/test_app.py").write_text("# protected verification source\n")
        (root / "app.py").write_text("def value():\n    return 1\n")
        self.session = task_session.prepare_session(self.task, "fixture-trial", "graf")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.temp.cleanup()

    def test_prepare_excludes_clean_indexes_and_injects_fault(self):
        self.assertFalse((self.session.root / ".graf").exists())
        self.assertFalse((self.session.root / "graphify-out").exists())
        self.assertIn("return 2", (self.session.root / "app.py").read_text())
        self.assertIn("return 1", (self.base / ".research/corpora/fixture/app.py").read_text())
        with self.assertRaises(ValueError):
            task_session.Session(self.task, "fixture-trial", "graphify")

    def test_read_blocks_metadata_parent_paths_and_symlinks(self):
        for path in ["../task.json", str(self.session.directory / "task.json"), ".graf/index.json"]:
            result = self.session.call("read", {"path": path})
            self.assertFalse(result["ok"])
        outside = self.base / "private.py"
        outside.write_text("private")
        (self.session.root / "link.py").symlink_to(outside)
        self.assertFalse(self.session.call("read", {"path": "link.py"})["ok"])
        result = self.session.call("read", {"path": str(self.session.input / "app.py")})
        self.assertTrue(result["ok"])
        self.assertIn("return 2", result["result"]["content"])
        limited = self.session.call("read", {"path": "app.py", "budget": 1})
        self.assertTrue(limited["result"]["truncated"])

    def test_edits_mark_indexes_dirty_and_protected_changes_fail_verification(self):
        self.session.dirty = False
        result = self.session.call("edit", {"path": "app.py", "old": "return 2", "new": "return 1"})
        self.assertTrue(result["ok"])
        self.assertTrue(self.session.dirty)
        result = self.session.call("edit", {"path": "tests/test_app.py", "old": "protected", "new": "tampered"})
        self.assertFalse(result["ok"])
        (self.session.root / "tests/test_app.py").write_text("external tampering")
        reopened = task_session.Session(self.task, "fixture-trial", "graf")
        finished = reopened.call("finish", {})
        self.assertFalse(finished["result"]["verification_passed"])
        self.assertTrue(reopened.closed)
        self.assertTrue(task_session.Session(self.task, "fixture-trial", "graf").closed)

    def test_missing_retriever_is_an_infrastructure_error_not_an_agent_error(self):
        # The fixture has no graf binary, as when target/ is deleted mid-batch.
        result = self.session.call("retrieve", {"query": "boundary"})
        self.assertFalse(result["ok"])
        self.assertTrue(result["infrastructure"])
        self.assertTrue(self.session.infrastructure_failed)
        agent_error = self.session.call("read", {"path": "missing.py"})
        self.assertNotIn("infrastructure", agent_error)

    def test_retrieve_commands_per_arm(self):
        base = task_session.retrieve_command("graf", Path("in"), Path("out"), "q", 2000)
        floor = task_session.retrieve_command("graf_floor", Path("in"), Path("out"), "q", 2000)
        self.assertEqual(floor[:len(base) - 2], base[:-2])
        self.assertEqual(floor[len(base) - 2:], ["--file-score", "damped", "--stop", "floor", "--", "q"])
        self.assertEqual(task_session.retrieve_command("graf", Path("in"), Path("out"), "--verbose", 2000)[-2:],
                         ["--", "--verbose"])
        self.assertIn("--graphify-query", task_session.retrieve_command("graphify", Path("in"), Path("out"), "q", 2000))
        ast = task_session.retrieve_command("ast_graph", Path("in"), Path("out"), "q", 2000)
        self.assertEqual(ast[1:], [str(task_session.AST_BACKEND / "ast_retrieve.py"), "query", "out", "q", "2000"])
        with self.assertRaises(ValueError):
            task_session.retrieve_command("search_only", Path("in"), Path("out"), "q", 2000)

    def test_adapter_persists_and_cannot_change_on_reopen(self):
        session = task_session.prepare_session(self.task, "native-trial", "graphify_orient", "native-cli-v2")
        reopened = task_session.Session(self.task, "native-trial", "graphify_orient")
        self.assertEqual(reopened.graphify_adapter, "native-cli-v2")
        with self.assertRaises(ValueError):
            task_session.Session(self.task, "native-trial", "graphify_orient", "library-v1")
        metadata = self.session.directory / "session.json"
        old = json.loads(metadata.read_text())
        old.pop('graphify_adapter')
        metadata.write_text(json.dumps(old))
        self.assertEqual(task_session.Session(self.task, "fixture-trial", "graf").graphify_adapter, "library-v1")
        with self.assertRaises(ValueError):
            task_session.Session(self.task, "fixture-trial", "graf", "native-cli-v2")

    def test_native_graphify_routes_both_retrieve_and_orientation(self):
        session = task_session.prepare_session(self.task, "native-commands", "graphify_orient", "native-cli-v2")
        session.dirty = False
        result = subprocess.CompletedProcess([], 0, 'native text', '')
        with patch.object(task_session, 'run_tool', return_value=result) as run:
            self.assertTrue(session.call('orient', {})['ok'])
            orient_cmd = run.call_args.args[0]
            self.assertEqual(orient_cmd[1:4], ['-m', 'graphify', 'query'])
            self.assertEqual(orient_cmd[4], self.task['issue'])
            self.assertTrue(session.call('retrieve', {'query': 'callee'})['ok'])
            self.assertEqual(run.call_args.args[0][4], 'callee')
        receipts = sorted(session.directory.glob('retrieval-command-*.json'))
        self.assertEqual(len(receipts), 2)
        self.assertTrue(all(json.loads(p.read_text())['stdout'] == 'native text' for p in receipts))

    def test_native_no_match_is_normal_but_other_failures_are_retained(self):
        session = task_session.prepare_session(self.task, "native-errors", "graphify", "native-cli-v2")
        session.dirty = False
        for code, text, success in [(4, 'No matching nodes found.\n', True), (4, 'unexpected', False), (1, 'broken', False)]:
            with patch.object(task_session, 'run_tool', return_value=subprocess.CompletedProcess([], code, text, 'details')):
                reply = session.call('retrieve', {'query': 'callee'})
            self.assertEqual(reply['ok'], success)
            if not success:
                self.assertTrue(reply['infrastructure'])
            receipt = json.loads((session.directory / f'retrieval-command-{session.tool_count}.json').read_text())
            self.assertEqual(receipt['returncode'], code)
            self.assertEqual(receipt['stderr'], 'details')

    def test_ast_graph_returns_whole_units_from_the_faulty_workspace(self):
        session = task_session.prepare_session(self.task, "ast-trial", "ast_graph")
        result = session.call("retrieve", {"query": "value"})
        self.assertTrue(result["ok"], result)
        self.assertIn("app.py:1-2\ndef value():\n    return 2", result["result"]["content"])
        build = json.loads((session.directory / "index-build-1.json").read_text())
        self.assertEqual(build["arm"], "ast_graph")

    def test_orient_arms_have_their_tools_and_commands(self):
        names = lambda arm: [d["name"] for d in task_session.tool_definitions(arm)]
        self.assertIn("outline", names("graf_orient"))
        self.assertNotIn("outline", names("graphify_orient"))
        self.assertNotIn("outline", names("graf"))
        self.assertNotIn("orient", names("graf_orient"))
        units = task_session.retrieve_command("graf_orient", Path("in"), Path("out"), "-q", 900)
        self.assertEqual(units[3:], ["units", "--budget", "900", "--text", "--", "-q"])
        self.assertIn("--graphify-query", task_session.retrieve_command("graphify_orient", Path("in"), Path("out"), "q", 9))
        self.assertEqual(units[0], str(task_session.GRAF_T5))

    def test_current_orientation_pins_every_operation_to_the_new_binary(self):
        arm = "graf_current_orient"
        self.assertEqual(task_session.graf_binary("graf_orient"), task_session.GRAF_T5)
        self.assertEqual(task_session.retrieve_command(arm, Path("in"), Path("out"), "q", 90)[0],
                         str(task_session.GRAF_T6))
        self.assertIn("outline", [d["name"] for d in task_session.tool_definitions(arm)])
        with patch.object(task_session, "run_tool") as run:
            run.return_value.stdout = "app.py:1-2\n"
            run.return_value.returncode = 0
            session = task_session.prepare_session(self.task, "current-orient", arm)
            session.dirty = False
            self.assertTrue(session.call("orient", {})["ok"])
            self.assertEqual(run.call_args[0][0][0], str(task_session.GRAF_T6))
            self.assertTrue(session.call("outline", {"path": "app.py"})["ok"])
            self.assertEqual(run.call_args[0][0][0], str(task_session.GRAF_T6))

    def test_lean_orientation_shows_only_triage_files_from_the_latest_committed_binary(self):
        arm = "graf_lean_orient"
        self.assertEqual(task_session.retrieve_command(arm, Path("in"), Path("out"), "q", 90)[0],
                         str(task_session.GRAF_LATEST))
        self.assertIn("outline", [d["name"] for d in task_session.tool_definitions(arm)])
        with patch.object(task_session, "run_tool") as run:
            run.return_value.stdout = json.dumps({"files": ["app.py", "lib/core.py"], "context": "Likely code:\n..."})
            run.return_value.returncode = 0
            session = task_session.prepare_session(self.task, "lean-orient", arm)
            session.dirty = False
            result = session.call("orient", {})["result"]
            self.assertEqual(result["content"], "Likely files:\n- app.py\n- lib/core.py\n")
            command = run.call_args[0][0]
            self.assertEqual(command[0], str(task_session.GRAF_LATEST))
            self.assertEqual(command[3:], ["triage", "--budget", str(task_session.ORIENT_BUDGET), "--",
                                           self.task["issue"]])
            run.return_value.stdout = json.dumps({"files": [], "context": ""})
            empty = task_session.prepare_session(self.task, "lean-orient-empty", arm)
            empty.dirty = False
            self.assertEqual(empty.call("orient", {})["result"]["content"], "")

    def test_orientation_is_only_for_orient_arms_and_only_once(self):
        with patch.object(task_session, "run_tool") as run:
            run.return_value.stdout = "Likely files:\n- app.py\n"
            session = task_session.prepare_session(self.task, "orient-trial", "graf_orient")
            session.dirty = False
            first = session.call("orient", {})
            self.assertTrue(first["ok"], first)
            self.assertEqual(first["result"]["content"], "Likely files:\n- app.py\n")
            command = run.call_args[0][0]
            self.assertEqual(command[3:], ["triage", "--budget", str(task_session.ORIENT_BUDGET), "--text", "--",
                                           self.task["issue"]])
            self.assertFalse(session.call("orient", {})["ok"])
            run.return_value.stdout = "word\n" * 5000
            long = task_session.prepare_session(self.task, "orient-long", "graphify_orient")
            long.dirty = False
            capped = long.call("orient", {})["result"]
            self.assertLessEqual(capped["reference_text_tokens"], task_session.ORIENT_BUDGET)
            self.assertTrue(capped["truncated"])
        self.assertFalse(self.session.call("orient", {})["ok"])

    def test_outline_reads_through_the_index_of_the_workspace(self):
        with patch.object(task_session, "run_tool") as run:
            run.return_value.stdout = "app.py (2 lines, 1 functions)\n"
            run.return_value.returncode = 0
            session = task_session.prepare_session(self.task, "outline-trial", "graf_orient")
            session.dirty = False
            result = session.call("outline", {"path": "app.py"})
            self.assertTrue(result["ok"], result)
            self.assertEqual(run.call_args[0][0][3:], ["outline", "--text", "--", "app.py"])
            self.assertFalse(session.call("outline", {"path": "../x"})["ok"])
        self.assertFalse(self.session.call("outline", {"path": "app.py"})["ok"])

    def test_a_refused_second_finish_records_no_verdict(self):
        self.session.call("finish", {})
        self.assertFalse(self.session.call("finish", {})["ok"])
        events = [json.loads(s) for s in self.session.events.read_text().splitlines()]
        self.assertEqual(sum(e["type"] == "verification" for e in events), 1)

    def test_tool_only_trace_cannot_be_scored_as_agent_usage(self):
        self.assertTrue(self.session.call("finish", {})["result"]["verification_passed"])
        events = [json.loads(s) for s in self.session.events.read_text().splitlines()]
        self.assertEqual(events[-1]["type"], "verification")
        self.assertTrue(events[-1]["passed"])
        with self.assertRaises(ValueError):
            agent_trials.audit_trace(self.session.events)
        self.assertFalse(self.session.call("read", {"path": "app.py"})["ok"])


if __name__ == "__main__":
    unittest.main()
