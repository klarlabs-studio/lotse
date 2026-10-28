from pathlib import Path
import tempfile
import unittest

from agent_native_replay import native_read, native_search


class NativeToolsTest(unittest.TestCase):
    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp())
        (self.workspace / "a.py").write_text("".join(f"def f{i}(): pass\n" for i in range(3000)))
        (self.workspace / "b.py").write_text("x = 1\n")

    def test_read_returns_the_first_2000_lines_once_until_the_file_is_held(self):
        held = set()
        size = native_read(self.workspace, {"path": "a.py", "start": 40}, held)
        self.assertGreater(size, 2000 * 4)
        self.assertEqual(native_read(self.workspace, {"path": "a.py", "start": 900}, held), 0)

    def test_read_beyond_line_2000_starts_there_and_does_not_hold_the_file(self):
        held = set()
        self.assertGreater(native_read(self.workspace, {"path": "a.py", "start": 2500}, held), 0)
        self.assertNotIn("a.py", held)

    def test_missing_files_keep_the_broker_size(self):
        self.assertIsNone(native_read(self.workspace, {"path": "nope.py"}, set()))

    def test_files_with_matches_lists_paths_and_content_lists_lines(self):
        paths = native_search(self.workspace, {"pattern": "x = 1|def f1\\("}, "files_with_matches")
        lines = native_search(self.workspace, {"pattern": "def f1"}, "content")
        self.assertLess(paths, 20)
        self.assertGreater(lines, paths)

    def test_a_content_result_over_20000_characters_becomes_a_short_preview(self):
        (self.workspace / "wide.js").write_text("".join(f"var v{i} = '{'abc ' * 60}';\n" for i in range(300)))
        self.assertLess(native_search(self.workspace, {"pattern": "var v"}, "content"), 1500)


if __name__ == "__main__":
    unittest.main()
