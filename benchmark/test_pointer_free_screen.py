import unittest

from pointer_free_screen import code_identifiers, pointer_free

WORDS = {"return", "string", "handler", "config"}


class CodeIdentifiersTest(unittest.TestCase):
    def test_keeps_code_shaped_names_and_drops_words_and_keywords(self):
        text = "func parseHeader(raw_value string) error { return MaxSize2 + handler + zzqfoo + Config + elif }"
        self.assertEqual(code_identifiers(text, "Go", WORDS), {"parseHeader", "raw_value", "MaxSize2", "zzqfoo"})

    def test_short_names_are_ignored(self):
        self.assertEqual(code_identifiers("a_b fooBar x1 abc", "Python", WORDS), {"fooBar"})


class PointerFreeTest(unittest.TestCase):
    task = {"repo": "r", "faults": [{"path": "pkg/server/router.go", "before": "s.parseHeader(x)", "after": "s.parseHeaders(x)"}]}

    def check(self, issue):
        return pointer_free({**self.task, "issue": issue}, "Go", WORDS)

    def test_free_issue(self):
        self.assertTrue(self.check("Requests with a long header hang forever")["pointer_free"])

    def test_path_file_name_and_stem_point(self):
        self.assertFalse(self.check("see pkg/server/router.go line 4")["pointer_free"])
        self.assertFalse(self.check("crash in Router.go")["pointer_free"])
        self.assertTrue(self.check("the router crashes")["stem"])  # Stem of 4+ characters, whole word, any case.
        self.assertFalse(self.check("the routers crash")["stem"])

    def test_identifier_points_case_sensitively(self):
        self.assertEqual(self.check("parseHeader panics")["identifiers"], ["parseHeader"])
        self.assertEqual(self.check("parseheader panics")["identifiers"], [])

    def test_a_file_the_fix_added_counts_by_path_only(self):
        task = {"issue": "add a retry helper", "faults": [{"path": "retry_helper.py", "added": True}]}
        self.assertTrue(pointer_free(task, "Python", WORDS)["pointer_free"])
        self.assertFalse(pointer_free({**task, "issue": "see retry_helper.py"}, "Python", WORDS)["pointer_free"])


if __name__ == "__main__":
    unittest.main()
