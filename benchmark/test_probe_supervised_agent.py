from pathlib import Path
import tempfile
import unittest

from probe_supervised_agent import probe


class SupervisedAgentTests(unittest.TestCase):
    def test_completed_and_interrupted_loop_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = probe(Path(tmp) / "cases")
            self.assertTrue(report["diagnostic_pass"])
            self.assertFalse(report["complete_accounting"])
            self.assertFalse(report["product_promotion_pass"])
            self.assertEqual(report["agent_savings"], "unmeasured")
            self.assertEqual([s["supervisor"]["outcome"] for s in report["scenarios"]],
                             ["completed", "timeout", "timeout"])


if __name__ == "__main__":
    unittest.main()
