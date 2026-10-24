"""Each Graphify revision is a separate pinned baseline; the installed one must match exactly one."""
import json
from pathlib import Path
import tempfile
import unittest

from retrieval import graphify_baseline


def pinned(corpora, ident, version, source):
    root = corpora / ident
    (root / "graphify").mkdir(parents=True)
    (root / "pyproject.toml").write_text(f'[project]\nname = "graphifyy"\nversion = "{version}"\n')
    (root / "graphify" / "serve.py").write_text(source)


class GraphifyBaseline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.corpora = self.tmp / "corpora"
        pinned(self.corpora, "graphify", "0.9.92", "old\n")
        pinned(self.corpora, "graphify-0.9.95", "0.9.95", "new\n")
        self.manifest = self.tmp / "corpora.json"
        self.manifest.write_text(json.dumps({"repositories": [
            {"id": "graphify", "commit": "86c57be", "role": "baseline"},
            {"id": "graphify-0.9.95", "commit": "92741d1", "role": "baseline", "revision_of": "graphify"}]}))
        self.installed = self.tmp / "site" / "graphify"
        self.installed.mkdir(parents=True)

    def baseline(self, version):
        return graphify_baseline(version, self.installed, self.manifest, self.corpora)

    def test_the_installed_version_selects_its_own_snapshot(self):
        (self.installed / "serve.py").write_text("new\n")
        self.assertEqual(self.baseline("0.9.95")["commit"], "92741d1")
        (self.installed / "serve.py").write_text("old\n")
        self.assertEqual(self.baseline("0.9.92")["commit"], "86c57be")

    def test_changed_source_or_unpinned_version_is_refused(self):
        (self.installed / "serve.py").write_text("patched\n")
        with self.assertRaisesRegex(ValueError, "differs from the pinned snapshot"):
            self.baseline("0.9.95")
        with self.assertRaisesRegex(ValueError, "no pinned baseline"):
            self.baseline("0.9.93")


if __name__ == "__main__":
    unittest.main()
