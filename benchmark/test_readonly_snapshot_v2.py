import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from readonly_broker import BrokerError
from readonly_snapshot_v2 import Snapshot, _canonical_semantic_digest


def sha(data):
    return hashlib.sha256(data).hexdigest()


class SnapshotV2Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "repo" / "input"
        self.root.mkdir(parents=True)
        self.source = self.root / "src.py"
        self.source.write_bytes(b"frozen source\n")
        self.artifact = self.root / ".graf" / "index.bin"
        self.artifact.parent.mkdir()
        self.artifact.write_bytes(b"root-bound old index")
        self.binary = self.base / "graf"
        self.binary.write_bytes(b"pinned binary")
        self.manifest = {"src.py": sha(b"frozen source\n")}
        self.export_body = {"schema": 1, "root": "ROOT", "files": {"src.py": {"hash": "abc"}},
                            "postings": {"source": [{"chunk": 0, "count": 1}]}, "lengths": [2]}
        self.semantics = _canonical_semantic_digest(json.dumps(self.export_body))

    def make_runner(self, mode="success"):
        calls = []

        def runner(argv, **kwargs):
            calls.append((list(argv), kwargs))
            if argv[-1] == "--rebuild":
                if mode == "build-fail":
                    return SimpleNamespace(returncode=9, stdout="partial build", stderr="build failed")
                staged_index = Path(argv[2], ".graf/index.bin")
                staged_index.unlink()
                staged_index.write_bytes(b"rebuilt index for snapshot root")
                if mode == "binary-changed":
                    self.binary.write_bytes(b"changed binary")
                if mode == "source-changed":
                    staged_source = Path(argv[2], "src.py")
                    staged_source.chmod(0o600)
                    staged_source.write_bytes(b"changed during index build")
                return SimpleNamespace(returncode=0, stdout="built\n", stderr="")
            body = dict(self.export_body)
            body["root"] = str(Path(argv[2]).resolve())
            if mode == "semantic-mismatch":
                body["lengths"] = [999]
            if mode == "wrong-root":
                body["root"] = "/another/root"
            return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")

        return runner, calls

    def snapshot(self, runner, **overrides):
        args = dict(source_root=self.root, manifest=self.manifest,
                    artifact_path=self.artifact, artifact_sha256=sha(b"root-bound old index"),
                    arm_id="graf_v61", parent=self.base, index_binary=self.binary,
                    index_binary_sha256=sha(b"pinned binary"),
                    index_semantics_sha256=self.semantics, runner=runner)
        args.update(overrides)
        return Snapshot(**args)

    def test_rebuilds_with_pinned_binary_and_checks_root_independent_semantics(self):
        runner, calls = self.make_runner()
        with self.snapshot(runner) as snap:
            self.assertEqual(len(calls), 2)
            self.assertTrue(all(kwargs["env"]["PYTHONHASHSEED"] == "0" for _, kwargs in calls))
            self.assertTrue(all(kwargs["timeout"] == 60 for _, kwargs in calls))
            self.assertEqual(calls[0][0][-2:], ["index", "--rebuild"])
            self.assertEqual(calls[1][0][-1], "export-index")
            self.assertEqual(snap.artifact_path.read_bytes(), b"rebuilt index for snapshot root")
            self.assertEqual(snap.receipt["original_artifact_sha256"], sha(b"root-bound old index"))
            self.assertEqual(snap.receipt["artifact_sha256"], sha(b"rebuilt index for snapshot root"))
            self.assertEqual(snap.receipt["index_semantics_sha256"], self.semantics)
            self.assertEqual([op["status"] for op in snap.receipt["index_operations"]], [0, 0])
            self.assertEqual(len(snap.receipt["index_elapsed_samples_seconds"]), 2)
            self.assertEqual(snap.artifact_path.stat().st_mode & 0o777, 0o444)
            self.source.write_bytes(b"live mutation\n")
            self.artifact.write_bytes(b"live artifact mutation")
            self.assertEqual((snap.root / "src.py").read_bytes(), b"frozen source\n")
            self.assertEqual(snap.artifact_path.read_bytes(), b"rebuilt index for snapshot root")
        self.assertFalse(snap._container)

    def test_semantic_mismatch_cleans_snapshot_and_records_rebuild(self):
        runner, _ = self.make_runner("semantic-mismatch")
        snap = self.snapshot(runner)
        with self.assertRaisesRegex(BrokerError, "semantic digest mismatch"):
            with snap:
                pass
        self.assertEqual([item["operation"] for item in snap.receipt["index_operations"]],
                         ["rebuild", "export-index"])
        self.assertEqual(list(self.base.glob("graf-snapshot-*")), [])

    def test_wrong_exported_root_is_rejected_and_cleans_snapshot(self):
        runner, _ = self.make_runner("wrong-root")
        snap = self.snapshot(runner)
        with self.assertRaisesRegex(BrokerError, "root does not match"):
            with snap:
                pass
        self.assertEqual(len(snap.receipt["index_operations"]), 2)
        self.assertEqual(list(self.base.glob("graf-snapshot-*")), [])

    def test_build_failure_cleans_snapshot_and_preserves_failure_receipt(self):
        runner, _ = self.make_runner("build-fail")
        snap = self.snapshot(runner)
        with self.assertRaisesRegex(BrokerError, "exited with status 9"):
            with snap:
                pass
        self.assertEqual(snap.receipt["index_operations"][0]["status"], 9)
        self.assertEqual(snap.receipt["index_operations"][0]["stdout"], "partial build")
        self.assertEqual(list(self.base.glob("graf-snapshot-*")), [])

    def test_binary_hash_mismatch_during_rebuild_cleans_snapshot(self):
        runner, _ = self.make_runner("binary-changed")
        snap = self.snapshot(runner)
        with self.assertRaisesRegex(BrokerError, "binary hash mismatch"):
            with snap:
                pass
        self.assertEqual(len(snap.receipt["index_operations"]), 1)
        self.assertEqual(snap.receipt["index_operations"][0]["stdout"], "built\n")
        self.assertEqual(snap.receipt["index_operations"][0]["status"], 0)
        self.assertEqual(list(self.base.glob("graf-snapshot-*")), [])

    def test_source_mutation_during_rebuild_retains_operation_and_cleans_snapshot(self):
        runner, _ = self.make_runner("source-changed")
        snap = self.snapshot(runner)
        with self.assertRaisesRegex(BrokerError, "Staged source bytes"):
            with snap:
                pass
        self.assertEqual(len(snap.receipt["index_operations"]), 1)
        self.assertEqual(snap.receipt["index_operations"][0]["stdout"], "built\n")
        self.assertEqual(snap.receipt["index_operations"][0]["status"], 0)
        self.assertEqual(list(self.base.glob("graf-snapshot-*")), [])

    def test_initial_binary_mismatch_is_rejected_before_snapshot_creation(self):
        with self.assertRaisesRegex(BrokerError, "binary hash mismatch"):
            self.snapshot(lambda *a, **k: None, index_binary_sha256="0" * 64)
        self.assertEqual(list(self.base.glob("graf-snapshot-*")), [])

    def test_graphify_remains_a_byte_copy_without_rebuild(self):
        graph = self.root.parent / "graph" / "graphify-out" / "graph.json"
        graph.parent.mkdir(parents=True)
        graph.write_bytes(b"graph")
        calls = []
        with Snapshot(self.root, self.manifest, graph, sha(b"graph"), "graphify", self.base,
                      runner=lambda *args, **kwargs: calls.append(args)) as snap:
            self.assertEqual(snap.artifact_path.read_bytes(), b"graph")
            self.assertEqual(snap.receipt["original_artifact_sha256"], sha(b"graph"))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
