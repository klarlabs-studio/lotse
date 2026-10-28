import hashlib
import os
from pathlib import Path
import tempfile
import unittest

from readonly_broker import BrokerError
from readonly_broker_v2 import source_manifest_sha256
from readonly_snapshot import Snapshot


def sha(data):
    return hashlib.sha256(data).hexdigest()


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.repo = self.base / "repo"
        self.root = self.repo / "input"
        self.root.mkdir(parents=True)
        (self.root / "src").mkdir()
        self.source = self.root / "src" / "main.py"
        self.source.write_bytes(b"frozen source\n")
        self.artifact = self.root / ".graf" / "index.bin"
        self.artifact.parent.mkdir()
        self.artifact.write_bytes(b"frozen index")
        self.manifest = {"src/main.py": sha(self.source.read_bytes())}

    def snapshot(self, **overrides):
        args = dict(source_root=self.root, manifest=self.manifest,
                    artifact_path=self.artifact, artifact_sha256=sha(b"frozen index"),
                    arm_id="graf_v61", parent=self.base)
        args.update(overrides)
        return Snapshot(**args)

    def test_copy_is_private_read_only_and_insulated_from_live_changes(self):
        snap = self.snapshot()
        with snap:
            staged_source = snap.root / "src/main.py"
            staged_artifact = snap.artifact_path
            self.assertEqual(staged_source.read_bytes(), b"frozen source\n")
            self.assertEqual(staged_artifact.read_bytes(), b"frozen index")
            self.assertNotEqual(staged_source.stat().st_ino, self.source.stat().st_ino)
            self.assertNotEqual(staged_artifact.stat().st_ino, self.artifact.stat().st_ino)
            self.assertEqual(snap.graph_path, snap.artifact_path)
            self.assertEqual(snap.receipt["source_manifest_sha256"],
                             source_manifest_sha256(self.manifest))
            self.assertEqual(snap.receipt["artifact_sha256"], sha(b"frozen index"))
            self.assertEqual(staged_source.stat().st_mode & 0o777, 0o444)
            self.assertEqual(snap.root.stat().st_mode & 0o777, 0o555)
            self.assertEqual(snap._container.stat().st_mode & 0o777, 0o500)
            self.source.write_bytes(b"changed live source\n")
            self.artifact.write_bytes(b"changed live index")
            self.assertEqual(staged_source.read_bytes(), b"frozen source\n")
            self.assertEqual(staged_artifact.read_bytes(), b"frozen index")
        self.assertFalse(snap._container)

    def test_graphify_external_artifact_uses_fixed_sibling_layout(self):
        graph = self.repo / "graph" / "graphify-out" / "graph.json"
        graph.parent.mkdir(parents=True)
        graph.write_bytes(b"graph bytes")
        with Snapshot(self.root, self.manifest, graph, sha(b"graph bytes"),
                       "graphify", self.base) as snap:
            self.assertEqual(snap.artifact_path, snap.root.parent / "graph/graphify-out/graph.json")
            self.assertEqual(snap.artifact_path.read_bytes(), b"graph bytes")

    def test_bad_source_and_artifact_digests_are_rejected(self):
        with self.assertRaisesRegex(BrokerError, "Source bytes"):
            with self.snapshot(manifest={"src/main.py": "0" * 64}):
                pass
        with self.assertRaisesRegex(BrokerError, "artifact hash"):
            with self.snapshot(artifact_sha256="0" * 64):
                pass

    def test_symlink_source_and_artifact_paths_are_rejected(self):
        real = self.root / "actual.py"
        real.write_bytes(b"frozen source\n")
        self.source.unlink()
        self.source.symlink_to(real)
        with self.assertRaisesRegex(BrokerError, "Symlink"):
            with self.snapshot():
                pass

        self.source.unlink()
        self.source.write_bytes(b"frozen source\n")
        graf_dir = self.root / ".graf"
        real_graf_dir = self.root / ".graf-real"
        graf_dir.rename(real_graf_dir)
        graf_dir.symlink_to(real_graf_dir, target_is_directory=True)
        with self.assertRaisesRegex(BrokerError, "Symlink"):
            with self.snapshot():
                pass
        graf_dir.unlink()
        real_graf_dir.rename(graf_dir)

        root_link = self.base / "root-link"
        root_link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaisesRegex(BrokerError, "source root"):
            Snapshot(root_link, self.manifest, self.artifact, sha(b"frozen index"),
                     "graf_v61", self.base)

        self.source.unlink()
        self.source.write_bytes(b"frozen source\n")
        real_artifact = self.root / "index-real.bin"
        real_artifact.write_bytes(b"frozen index")
        self.artifact.unlink()
        self.artifact.symlink_to(real_artifact)
        with self.assertRaisesRegex(BrokerError, "Symlink"):
            with self.snapshot():
                pass

    def test_traversal_offroot_artifact_and_extra_eligible_source_are_rejected(self):
        with self.assertRaisesRegex(BrokerError, "traverses"):
            self.snapshot(manifest={"../escape.py": sha(b"x")})
        outside = self.base / "outside.bin"
        outside.write_bytes(b"frozen index")
        with self.assertRaisesRegex(BrokerError, "supported layout"):
            self.snapshot(artifact_path=outside)
        (self.root / "extra.ts").write_text("extra\n")
        with self.assertRaisesRegex(BrokerError, "inventory"):
            with self.snapshot():
                pass

    def test_failed_copy_cleans_only_its_private_directory(self):
        sentinel = self.base / "keep.txt"
        sentinel.write_text("keep")
        snap = self.snapshot()
        original_write = snap._write
        calls = 0

        def fail_second(path, data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected write failure")
            original_write(path, data)

        snap._write = fail_second
        with self.assertRaisesRegex(OSError, "injected"):
            with snap:
                pass
        self.assertEqual(list(self.base.glob("graf-snapshot-*")), [])
        self.assertEqual(sentinel.read_text(), "keep")

    def test_live_mutation_during_copy_is_rejected_and_partial_copy_removed(self):
        snap = self.snapshot()
        original_write = snap._write
        changed = False

        def mutate_after_source_write(path, data):
            nonlocal changed
            original_write(path, data)
            if path.name == "main.py" and not changed:
                changed = True
                self.source.write_bytes(b"changed during copy\n")

        snap._write = mutate_after_source_write
        with self.assertRaisesRegex(BrokerError, "inventory differs|changed while snapshot"):
            with snap:
                pass
        self.assertEqual(list(self.base.glob("graf-snapshot-*")), [])


if __name__ == "__main__":
    unittest.main()
