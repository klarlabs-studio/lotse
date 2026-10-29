"""Trial workspaces archived as deltas against their snapshot come back identical."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import workspace_archive
from workspace_archive import archive_trial, digest, manifest, restore_trial


class WorkspaceArchiveTest(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp())
        for name, value in (("BASE", root), ("CORPORA", root / "corpora"), ("TRIALS", root / "trials"),
                            ("SETTLED_S", -1)):
            patcher = patch.object(workspace_archive, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        base = root / "corpora/repo"
        (base / "src/gone").mkdir(parents=True)
        (base / "src/a.py").write_text("a = 1\n")
        (base / "src/gone/old.py").write_text("x\n")
        (base / "tests").mkdir()
        (base / "tests/test_a.py").write_text("hidden\n")
        (base / "run.sh").write_text("#!/bin/sh\n")
        os.chmod(base / "run.sh", 0o755)
        self.trial = root / "trials/t10-repo-12-builtin-1"
        self.trial.mkdir(parents=True)
        (self.trial / "task.json").write_text(json.dumps({"task": "repo-12"}))
        workspace = self.trial / "workspace"
        os.system(f"cp -R '{base}' '{workspace}'")
        (workspace / "src/a.py").write_text("a = 2\n")
        (workspace / "tests/test_a.py").unlink()
        os.system(f"rm -r '{workspace}/src/gone'")
        (workspace / "src/new.py").write_text("n\n")
        (workspace / "empty").mkdir()
        os.symlink("/elsewhere/node_modules", workspace / "node_modules")
        os.chmod(workspace / "run.sh", 0o644)

    def test_round_trip_keeps_contents_modes_links_and_empty_directories(self):
        expected = manifest(self.trial / "workspace")
        self.assertEqual(archive_trial(self.trial, {}), "archived")
        self.assertFalse((self.trial / "workspace").exists())
        restore_trial(self.trial)
        self.assertEqual(manifest(self.trial / "workspace"), expected)

    def test_restore_refuses_a_changed_snapshot(self):
        archive_trial(self.trial, {})
        (workspace_archive.CORPORA / "repo/src/a.py").write_text("changed\n")
        with self.assertRaises(SystemExit):
            restore_trial(self.trial)

    def test_a_trial_without_a_snapshot_is_left_alone(self):
        (self.trial / "task.json").write_text(json.dumps({"task": "other-1"}))
        self.assertEqual(archive_trial(self.trial, {}), "skipped: no pinned snapshot")
        self.assertTrue((self.trial / "workspace").is_dir())


if __name__ == "__main__":
    unittest.main()
