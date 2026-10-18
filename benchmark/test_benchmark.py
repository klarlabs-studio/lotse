"""Tests for measurement integrity, provenance, and trace accounting."""
import json
from pathlib import Path
import tempfile
import unittest

from agent_trials import audit_trace, tasks
from retrieval import BASE, chunks_from, pack, score_evidence, tokens


class MeasurementIntegrity(unittest.TestCase):
    def test_budget_includes_headers_and_unicode(self):
        chunks = chunks_from("目录/example.py", "\n".join("# λ Unicode source evidence" for _ in range(96)))
        budget = len(tokens("目录/example.py:1-24\n" + chunks[0]["text"] + "\n"))
        selected, context = pack(chunks, list(range(len(chunks))), budget)
        self.assertEqual(selected, [0])
        self.assertLessEqual(len(tokens(context)), budget)
        self.assertEqual(pack(chunks, [0], 0), ([], ""))

    def test_path_hit_cannot_substitute_for_line_evidence(self):
        chunks = chunks_from("api.py", "\n".join(str(i) for i in range(72)))
        evidence = [dict(path="api.py", start=60, end=60)]
        metrics = score_evidence(chunks, [0], evidence)
        self.assertEqual(metrics["file_recall"], 1)
        self.assertEqual(metrics["anchor_recall"], 0)
        self.assertFalse(metrics["all_anchors"])

    def test_negative_controls_do_not_receive_vacuous_perfect_recall(self):
        metrics = score_evidence([], [], [])
        self.assertIsNone(metrics["anchor_recall"])
        self.assertIsNone(metrics["all_anchors"])

    def test_snapshot_evidence_has_not_drifted(self):
        import hashlib
        queries = json.loads((BASE / "benchmark/queries.json").read_text())
        self.assertEqual(len(queries), 30)
        self.assertEqual(len({q["id"] for q in queries}), 30)
        for case in queries:
            for e in case["evidence"]:
                text = (BASE / ".research/corpora" / case["repo"] / e["path"]).read_text()
                self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), e["file_sha256"])
                self.assertIn(e["anchor"], text.splitlines()[e["start"]-1])

    def test_faults_are_unique_and_validation_files_exist(self):
        for task in tasks():
            root = BASE / ".research/corpora" / task["repo"]
            f = task["fault"]
            self.assertEqual((root / f["path"]).read_text().count(f["before"]), 1)
            if task["repo"] == "fastify":
                self.assertTrue((root / task["verify"][-1]).is_file())
            if task["repo"] == "flask":
                self.assertTrue((root / task["verify"][-1].split("::")[0]).is_file())

    def trace(self, events):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.jsonl"
            path.write_text("\n".join(json.dumps(e) for e in events))
            return audit_trace(path)

    def test_usage_does_not_double_count_reasoning_or_cache(self):
        usage = dict(type="model_usage", component="solver", model="fixed-model",
                     input_tokens=100, cached_input_tokens=60, output_tokens=50,
                     reasoning_tokens=20, cost_usd=.01)
        result = self.trace([usage, dict(type="tool"), dict(type="verification", passed=False)])
        self.assertEqual(result["total_tokens"], 150)
        self.assertFalse(result["verification_passed"])
        with self.assertRaises(ValueError):
            self.trace([usage])
        with self.assertRaises(ValueError):
            self.trace([dict(usage, cached_input_tokens=101), dict(type="verification", passed=True)])
        with self.assertRaises(ValueError):
            self.trace([dict(usage, cost_usd=float("nan")), dict(type="verification", passed=True)])
        with self.assertRaises(ValueError):
            self.trace([dict(usage, input_tokens=100.5), dict(type="verification", passed=True)])


if __name__ == "__main__":
    unittest.main()
