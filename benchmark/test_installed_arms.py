"""Batch t10's installed arms: Claude Code's own tools in a workspace outside the repository."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import run_agent_trial
import task_session
from run_agent_trial import installed_command, outside_workspace
from task_session import configuration_hashes, relocate_workspace, restore_workspace, tool_definitions


class InstalledCommandTest(unittest.TestCase):
    def flags(self, arm):
        with patch.dict("os.environ", {}, clear=False):
            return installed_command("Bug", Path("/x/mcp.json"), "claude-haiku-5-5", 0.5, arm)

    def test_every_arm_gets_the_same_built_in_tools_without_prompts_or_personal_settings(self):
        for arm in ("builtin", "graphify_installed", "graf_installed"):
            command = self.flags(arm)
            self.assertEqual(command[command.index("--setting-sources") + 1], "project")
            self.assertEqual(command[command.index("--permission-prompts") + 1], "none")
            self.assertIn("--strict-mcp-config", command)
            self.assertIn("--disable-slash-commands", command)
            self.assertNotIn("--system-prompt", command)
            self.assertTrue(command[command.index("--tools") + 1].startswith("Read,Grep,Glob,Edit"))

    def test_only_graphify_gets_a_shell_and_only_for_graphify(self):
        builtin, graphify, graf = (self.flags(a) for a in ("builtin", "graphify_installed", "graf_installed"))
        self.assertEqual(builtin[builtin.index("--tools") + 1], "Read,Grep,Glob,Edit")
        self.assertEqual(graphify[graphify.index("--tools") + 1], "Read,Grep,Glob,Edit,Bash")
        self.assertIn("Bash(graphify:*)", graphify)
        self.assertIn("mcp__graf", graf)
        self.assertNotIn("mcp__graf", builtin)

    def test_the_broker_offers_only_test_and_finish(self):
        self.assertEqual([d["name"] for d in tool_definitions("graf_installed")], ["test", "finish"])


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.external = Path(tempfile.mkdtemp())
        patcher = patch.object(task_session, "EXTERNAL_TRIALS", self.external)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.trial = Path(tempfile.mkdtemp()) / "t10-x-builtin-1"
        (self.trial / "workspace/src").mkdir(parents=True)
        (self.trial / "workspace/src/a.py").write_text("x = 1\n")
        (self.trial / "task.json").write_text('{"task": "x"}\n')

    def test_relocate_then_restore_round_trips_the_workspace(self):
        relocate_workspace(self.trial)
        self.assertTrue((self.trial / "workspace").is_symlink())
        self.assertEqual((self.trial / "workspace").resolve(), (self.external / self.trial.name / "workspace").resolve())
        self.assertTrue((self.external / self.trial.name / "task.json").is_file())
        restore_workspace(self.trial)
        self.assertFalse((self.trial / "workspace").is_symlink())
        self.assertEqual((self.trial / "workspace/src/a.py").read_text(), "x = 1\n")
        self.assertFalse((self.external / self.trial.name).exists())

    def test_configuration_files_are_hashed_outside_dependency_folders(self):
        root = self.trial / "workspace"
        (root / "package.json").write_text("{}")
        (root / "node_modules/dep").mkdir(parents=True)
        (root / "node_modules/dep/package.json").write_text("{}")
        self.assertEqual(set(configuration_hashes(root)), {"package.json"})


class AutoMemoryTest(unittest.TestCase):
    def test_removes_only_an_empty_memory_folder(self):
        projects = Path(tempfile.mkdtemp())
        empty = projects / "-private-var-tmp-graf-trials-a-workspace/memory"
        empty.mkdir(parents=True)
        self.assertTrue(run_agent_trial.remove_empty_auto_memory(Path("/private/var/tmp/graf-trials/a/workspace"), projects))
        self.assertFalse(empty.parent.exists())
        kept = projects / "-private-var-tmp-graf-trials-b-workspace/memory"
        kept.mkdir(parents=True)
        (kept / "MEMORY.md").write_text("x")
        self.assertFalse(run_agent_trial.remove_empty_auto_memory(Path("/private/var/tmp/graf-trials/b/workspace"), projects))
        self.assertTrue((kept / "MEMORY.md").is_file())


class OutsideWorkspaceTest(unittest.TestCase):
    def stream(self, *events):
        path = Path(tempfile.mkdtemp()) / "agent-stream.jsonl"
        path.write_text("".join(json.dumps(e) + "\n" for e in events))
        return path

    def test_counts_requests_and_answers_outside_the_workspace(self):
        workspace = Path(tempfile.mkdtemp())
        use = lambda i, name, arg: {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": i, "name": name, "input": arg}]}}
        result = lambda i, error: {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": i, "is_error": error, "content": "x"}]}}
        stream = self.stream(use("a", "Read", {"file_path": str(workspace / "src/a.py")}), result("a", False),
                             use("b", "Read", {"file_path": "/etc/hosts"}), result("b", True),
                             use("c", "Grep", {"pattern": "x", "path": "../.."}), result("c", False),
                             use("d", "Glob", {"pattern": "*.py"}), result("d", False))
        self.assertEqual(outside_workspace(stream, workspace), (2, 1))

    def test_links_inside_the_workspace_and_the_var_alias_count_as_inside(self):
        workspace = Path("/private/var/tmp/graf-trials/x/workspace")
        use = lambda i, arg: {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": i, "name": "Read", "input": {"file_path": arg}}]}}
        stream = self.stream(use("a", "/private/var/tmp/graf-trials/x/workspace/node_modules/dep/index.js"),
                             use("b", "/var/tmp/graf-trials/x/workspace/src/a.py"),
                             use("c", "/private/var/tmp/graf-trials/x/workspace/../task.json"))
        self.assertEqual(outside_workspace(stream, workspace), (1, 0))


if __name__ == "__main__":
    unittest.main()
