import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import raw_archive


class RawArchiveTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        (self.base / "results").mkdir()
        self.receipt = self.base / "results/raw-archive.json"
        self.receipt.write_text(json.dumps({"files": []}))
        patches = [patch.object(raw_archive, "BASE", self.base), patch.object(raw_archive, "RECEIPT", self.receipt)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_archive_then_restore_is_byte_identical(self):
        result = self.base / "results/big.json"
        result.write_text('{"rows": [1, 2, 3]}\n' * 1000)
        original = result.read_bytes()
        raw_archive.archive(["results/big.json"])
        self.assertFalse(result.exists())
        row = json.loads(self.receipt.read_text())["files"][0]
        self.assertEqual((row["result"], row["bytes"]), ("results/big.json", len(original)))
        raw_archive.restore([])
        self.assertEqual(result.read_bytes(), original)

    def test_restore_refuses_a_changed_archive(self):
        result = self.base / "results/big.json"
        result.write_text("original\n")
        raw_archive.archive(["results/big.json"])
        archive = self.base / json.loads(self.receipt.read_text())["files"][0]["archive"]
        import gzip
        archive.write_bytes(gzip.compress(b"tampered\n"))
        with self.assertRaisesRegex(SystemExit, "does not match"):
            raw_archive.restore(["results/big.json"])

    def test_restore_names_unrecorded_paths(self):
        with self.assertRaisesRegex(SystemExit, "not in the receipt"):
            raw_archive.restore(["results/unknown.json"])


if __name__ == "__main__":
    unittest.main()
