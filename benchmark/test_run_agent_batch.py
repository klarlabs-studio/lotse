"""Batches must find the natural-task specification that froze their catalog."""
import json
from pathlib import Path
import unittest

from run_agent_batch import natural_spec

BASE = Path(__file__).resolve().parents[1]
AGENT = json.loads((BASE / "benchmark/preregistration.json").read_text())["agent_trials"]


class NaturalSpecTests(unittest.TestCase):
    def test_each_natural_batch_resolves_its_frozen_catalog(self):
        self.assertEqual(natural_spec(AGENT, AGENT["batches"]["t3"]["catalog"])["minimum_tasks"], 18)
        t4 = natural_spec(AGENT, AGENT["batches"]["t4"]["catalog"])
        self.assertEqual(t4["catalog"], "benchmark/natural-tasks-t4.json")
        self.assertEqual(len(t4["frozen_catalog_sha256"]), 64)
        self.assertIsNone(natural_spec(AGENT, "benchmark/agent-tasks.json"))


if __name__ == "__main__":
    unittest.main()
