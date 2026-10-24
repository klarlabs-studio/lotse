"""A selected workload cannot silently change the baseline, inputs or binaries."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from comparison_plan import load_plan, selected_layout, sha, verify_sources


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / "benchmark").mkdir()
        (self.base / "results").mkdir()
        self.known = {"graf": ((), ("search",)), "graf_other": (("--x",), ("locate",))}
        self.pin = {"id": "graphify", "repository": "baseline/source", "commit": "a" * 40, "role": "baseline"}
        self.repo = {"id": "fresh", "repository": "example/fresh", "commit": "b" * 40, "role": "corpus"}
        self.write("benchmark/corpora.json", {"repositories": [self.pin]})
        self.write("benchmark/corpora-test.json", {"repositories": [self.repo, self.pin]})
        self.write("results/snapshots.json", [self.repo, self.pin])
        self.write("benchmark/queries-test.json", [{"id": "one", "repo": "fresh", "query": "question"}])
        self.write("results/sources.json", {"fresh": {"x.py": "digest"}})
        (self.base / "binary").write_bytes(b"saved-binary")
        self.tools = {}
        for name in ("compare_graphify.py", "check_comparison.py", "comparison_plan.py"):
            path = self.base / "benchmark" / name
            path.write_text("# frozen tool\n")
            self.tools[f"benchmark/{name}"] = sha(path)
        self.plan = self.base / "benchmark/plan.json"
        self.spec = {"id": "test", "arms": ["graf_other", "graphify"], "repeats": 1,
                     "budgets": [2000, 8000], "tokenizer": "cl100k_base",
                     "output": "results/test.json", "graphify_adapter": "native-cli-v2",
                     "queries": self.ref("benchmark/queries-test.json"),
                     "corpora": self.ref("benchmark/corpora-test.json"),
                     "snapshots": self.ref("results/snapshots.json"),
                     "source_manifest": self.ref("results/sources.json"),
                     "index_binary": self.ref("binary"),
                     "binaries": {"graf_other": self.ref("binary")}}

    def write(self, path, value):
        (self.base / path).write_text(json.dumps(value))

    def ref(self, path):
        return {"path": path, "sha256": sha(self.base / path)}

    def freeze(self, spec=None):
        self.plan.write_text(json.dumps(self.spec if spec is None else spec))
        return {"comparison_plans": {"test": {"path": "benchmark/plan.json",
                                              "sha256": sha(self.plan), "tools": self.tools}}}

    def load(self, spec=None):
        return load_plan(self.plan, self.freeze(spec), self.base, self.known)

    def test_selected_layout_keeps_base_index_and_requested_order(self):
        selected, variants = selected_layout(["graf_other", "graphify"], self.known)
        self.assertEqual(list(selected), ["graf_other"])
        self.assertEqual(variants, ((), ("--x",)))

    def test_valid_selected_plan(self):
        spec, inputs = self.load()
        self.assertEqual(spec, self.spec)
        self.assertEqual(inputs["queries"][0]["repo"], "fresh")

    def test_unfrozen_or_modified_plan_rejected(self):
        prereg = self.freeze()
        with self.assertRaises(ValueError):
            load_plan(self.plan, {}, self.base, self.known)
        self.plan.write_text(self.plan.read_text() + "\n")
        with self.assertRaises(ValueError):
            load_plan(self.plan, prereg, self.base, self.known)

    def test_missing_or_changed_tool_rejected(self):
        prereg = self.freeze()
        (self.base / "benchmark/compare_graphify.py").write_text("changed")
        with self.assertRaises(ValueError):
            load_plan(self.plan, prereg, self.base, self.known)
        prereg["comparison_plans"]["test"]["tools"] = {}
        with self.assertRaises(ValueError):
            load_plan(self.plan, prereg, self.base, self.known)

    def test_baseline_required_and_no_duplicate_or_unknown_arm(self):
        for arms in (["graf"], ["graphify"], ["graf", "graf", "graphify"], ["oops", "graphify"]):
            spec = {**self.spec, "arms": arms}
            with self.assertRaises(ValueError):
                self.load(spec)

    def test_invalid_repetitions_or_budget_rejected(self):
        for key, value in (("repeats", 0), ("repeats", True), ("budgets", [4000]),
                           ("tokenizer", "different"), ("output", "../outside.json")):
            with self.assertRaises(ValueError):
                self.load({**self.spec, key: value})

    def test_changed_input_or_binary_rejected(self):
        for path in ("benchmark/queries-test.json", "results/snapshots.json", "binary"):
            source = self.base / path
            old = source.read_bytes()
            source.write_bytes(old + b"\n")
            with self.assertRaises(ValueError):
                self.load()
            source.write_bytes(old)

    def test_replacing_graphify_pin_rejected(self):
        changed = {**self.pin, "commit": "c" * 40}
        self.write("benchmark/corpora-test.json", {"repositories": [self.repo, changed]})
        with self.assertRaisesRegex(ValueError, "replace pinned Graphify"):
            self.load({**self.spec, "corpora": self.ref("benchmark/corpora-test.json")})

    def test_repository_identity_and_snapshot_disagreement_rejected(self):
        for repo in ({**self.repo, "id": "../escape"}, {**self.repo, "commit": "c" * 40}):
            self.write("benchmark/corpora-test.json", {"repositories": [repo, self.pin]})
            with self.assertRaises(ValueError):
                self.load({**self.spec, "corpora": self.ref("benchmark/corpora-test.json")})

    def test_unknown_query_repo_and_duplicate_id_rejected(self):
        for cases in ([{"id": "one", "repo": "other"}],
                      [{"id": "one", "repo": "fresh"}] * 2):
            self.write("benchmark/queries-test.json", cases)
            with self.assertRaises(ValueError):
                self.load({**self.spec, "queries": self.ref("benchmark/queries-test.json")})

    def test_per_arm_binary_accounting_is_complete(self):
        with self.assertRaises(ValueError):
            self.load({**self.spec, "binaries": {}})

    def test_source_snapshot_exactness_includes_deletions(self):
        import hashlib
        files = {"x.py": "source"}
        inputs = {"source_manifest": {"fresh": {"x.py": hashlib.sha256(b"source").hexdigest()}}}
        verify_sources(inputs, "fresh", files)
        for changed in ({}, {"x.py": "changed"}, {**files, "extra.py": "extra"}):
            with self.assertRaises(ValueError):
                verify_sources(inputs, "fresh", changed)


if __name__ == "__main__":
    unittest.main()
