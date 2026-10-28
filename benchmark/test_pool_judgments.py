"""Pooling must be blind, bounded, and complete before judgments count."""
import unittest

from pool_judgments import POOL_DEPTH, build_pool, check_judgments, relevant_files


def case(query_id, *paths):
    return {"id": query_id, "evidence": [{"path": p} for p in paths]}


def row(query_id, **orders):
    return {"id": query_id, "tools": {tool: {"cited_path_order": order} for tool, order in orders.items()}}


class PoolTests(unittest.TestCase):
    def test_pool_unions_top_code_files_and_drops_labels_and_docs(self):
        deep = [f"f{i}.go" for i in range(20)]
        rows = [row("q", graf=["a.go", "README.md", "b.go"], graphify=["c.go", "a.go"]),
                row("q", graf_locate=deep)]
        pool = build_pool(rows, {"q": case("q", "a.go", "guide.md")})
        self.assertEqual(set(pool["q"]), {"b.go", "c.go", *deep[:POOL_DEPTH]} - {"a.go"})
        self.assertNotIn("f10.go", pool["q"])

    def test_pool_is_deterministic_and_skips_queries_without_code_labels(self):
        rows = [row("q", graf=[f"x{i}.py" for i in range(8)]), row("neg", graf=["x.py"])]
        cases = {"q": case("q", "a.py"), "neg": case("neg")}
        self.assertEqual(build_pool(rows, cases), build_pool(rows, cases))
        self.assertNotIn("neg", build_pool(rows, cases))

    def test_judgments_must_cover_the_pool_exactly_with_reasons(self):
        pool = {"q": ["b.go", "c.go"]}
        good = {"q": {"b.go": {"relevant": True, "reason": "defines it"},
                      "c.go": {"relevant": False, "reason": "unrelated"}}}
        self.assertEqual(check_judgments(pool, good), [])
        self.assertTrue(check_judgments(pool, {"q": {"b.go": good["q"]["b.go"]}}))
        self.assertTrue(check_judgments(pool, {"q": {**good["q"], "d.go": good["q"]["c.go"]}}))
        self.assertTrue(check_judgments(pool, {"q": {**good["q"], "c.go": {"relevant": "no", "reason": "x"}}}))

    def test_relevance_adds_judged_files_to_author_labels(self):
        judgments = {"q": {"b.go": {"relevant": True, "reason": "r"}, "c.go": {"relevant": False, "reason": "r"}}}
        self.assertEqual(relevant_files({"id": "q", "evidence": [{"path": "a.go"}, {"path": "x.md"}]}, judgments),
                         {"a.go", "b.go"})


if __name__ == "__main__":
    unittest.main()
