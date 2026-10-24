"""Batches must find the natural-task specification that froze their catalog."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import run_agent_batch

from run_agent_batch import natural_spec

BASE = Path(__file__).resolve().parents[1]
AGENT = json.loads((BASE / "benchmark/preregistration.json").read_text())["agent_trials"]


class NaturalSpecTests(unittest.TestCase):
    def test_changed_solver_stops_before_any_trial_is_run(self):
        with patch("sys.argv", ["run_agent_batch", "--batch", "t6"]), \
             patch.object(run_agent_batch.subprocess, "check_output", return_value="changed"), \
             patch.object(run_agent_batch, "run") as run:
            with self.assertRaisesRegex(SystemExit, "Solver version differs"):
                run_agent_batch.main()
            run.assert_not_called()

    def test_changed_backend_stops_before_any_trial_is_run(self):
        frozen = AGENT["batches"]["t6"]["solver_version"]
        with patch("sys.argv", ["run_agent_batch", "--batch", "t6"]), \
             patch.object(run_agent_batch.subprocess, "check_output", return_value=frozen), \
             patch.object(run_agent_batch, "graphify_baseline",
                          return_value={"commit": AGENT["batches"]["t6"]["graphify_commit"]}), \
             patch.object(run_agent_batch, "preflight", return_value="changed"), \
             patch.object(run_agent_batch, "run") as run:
            with self.assertRaisesRegex(SystemExit, "frozen backend"):
                run_agent_batch.main()
            run.assert_not_called()

    def test_changed_graphify_baseline_stops_before_any_trial_is_run(self):
        frozen = AGENT["batches"]["t4r"]["solver_version"]
        with patch("sys.argv", ["run_agent_batch", "--batch", "t4r"]), \
             patch.object(run_agent_batch.subprocess, "check_output", return_value=frozen), \
             patch.object(run_agent_batch, "graphify_baseline", return_value={"commit": "86c57be"}), \
             patch.object(run_agent_batch, "run") as run:
            with self.assertRaisesRegex(SystemExit, "Graphify baseline differs"):
                run_agent_batch.main()
            run.assert_not_called()

    def test_a_changed_solver_copy_stops_before_any_trial_is_run(self):
        batch = {**AGENT["batches"]["t4r"], "solver_binary": "benchmark/run_agent_batch.py", "solver_sha256": "changed"}
        agent = {**AGENT, "batches": {**AGENT["batches"], "pinned": batch}}
        with patch("sys.argv", ["run_agent_batch", "--batch", "pinned"]), \
             patch.object(run_agent_batch, "agent_preregistration", return_value=agent), \
             patch.object(run_agent_batch, "run") as run:
            with self.assertRaisesRegex(SystemExit, "Solver binary differs"):
                run_agent_batch.main()
            run.assert_not_called()

    def test_each_natural_batch_resolves_its_frozen_catalog(self):
        self.assertEqual(natural_spec(AGENT, AGENT["batches"]["t3"]["catalog"])["minimum_tasks"], 18)
        t4 = natural_spec(AGENT, AGENT["batches"]["t4"]["catalog"])
        self.assertEqual(t4["catalog"], "benchmark/natural-tasks-t4.json")
        self.assertEqual(len(t4["frozen_catalog_sha256"]), 64)
        self.assertIsNone(natural_spec(AGENT, "benchmark/agent-tasks.json"))


if __name__ == "__main__":
    unittest.main()
