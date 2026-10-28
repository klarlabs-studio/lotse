import json
import tempfile
from pathlib import Path
import unittest

import find_replay


def event(operation, arguments=None, content="", ok=True, **result):
    return {"operation": operation, "arguments": arguments or {},
            "response": {"ok": ok, "result": {"content": content, **result}}}


class FindReplayTest(unittest.TestCase):
    def trial(self, calls):
        directory = Path(tempfile.mkdtemp())
        (directory / "tool-events.jsonl").write_text("".join(json.dumps(c) + "\n" for c in calls))
        return directory

    def test_pairs_keep_reads_around_a_hit_until_another_operation(self):
        trial = self.trial([
            event("orient"),
            event("search", {"pattern": "foo", "budget": 900}, "a.py:10:foo()\nb.py:3:foo\n"),
            event("read", {"path": "a.py", "start": 5, "lines": 20}, "L5: x"),
            event("read", {"path": "c.py", "start": 1, "lines": 20}, "L1: y"),
            event("read", {"path": "b.py", "start": 1, "lines": 5}, "L1: z"),
            event("search", {"pattern": "bar"}, "a.py:40:bar\n"),
            event("outline", {"path": "a.py"}),
            event("read", {"path": "a.py", "start": 30, "lines": 20}, "L30: w"),
        ])
        found = find_replay.pairs(trial)
        self.assertEqual(len(found), 1)
        self.assertEqual((found[0]["pattern"], found[0]["budget"]), ("foo", 900))
        self.assertEqual([r["arguments"]["path"] for r in found[0]["reads"]], ["a.py", "b.py"])

    def test_pairs_stop_at_the_first_edit(self):
        trial = self.trial([
            event("edit", {"path": "a.py"}),
            event("search", {"pattern": "foo"}, "a.py:10:foo\n"),
            event("read", {"path": "a.py", "start": 1, "lines": 20}, "L1: x"),
        ])
        self.assertEqual(find_replay.pairs(trial), [])

    def test_shown_lines_reads_blocks_and_compact_matches_per_path(self):
        output = ("a.go:L7-9 Other (match L8)\nL7: func Other() {\nL8: x\nL9: }\n"
                  "b.go:L1-3 (match L2)\nL1: a\n...\nL3: c\n"
                  "a.go:L20: y := 1\n[3 more matches not shown]\n")
        self.assertEqual(find_replay.shown_lines(output, "a.go"), {7, 8, 9, 20})
        self.assertEqual(find_replay.shown_lines(output, "b.go"), {1, 3})


if __name__ == "__main__":
    unittest.main()
