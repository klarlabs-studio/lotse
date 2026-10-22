"""Localization scoring must be symmetric across arms and anchored to the real fix."""
import unittest

from localization import (file_propagate, graf_locate_command, catalog_for, write_json_atomic, corpus_gold, accuracy_at, bounded_units, cited_units, rewrite_query, decide, mcnemar_one_sided, code_units, call_edges, gold_lines, gold_units, graphify_locations, propagate,
                          pack_units, score_task)

GO = """package x

func helper(a int) int {
	return a + 1
}

func Run() int {
	return helper(2)
}
"""
PY = """import os


@decorator
def first():
    return second()


class Box:
    size = 1

    def second(self):
        return 2
"""
JS = """'use strict'
function onSend (reply) {
  return wrap(reply)
}
const wrap = (r) => r
module.exports = { onSend }
"""


class GoldTests(unittest.TestCase):
    def test_changed_and_deleted_lines_map_to_pre_fix_lines(self):
        pre = "a\nb\nc\nd\n"
        self.assertEqual(gold_lines(pre, "a\nB\nc\nd\n"), {2})
        self.assertEqual(gold_lines(pre, "a\nc\nd\n"), {2})

    def test_pure_insertions_mark_the_bordering_lines(self):
        self.assertEqual(gold_lines("a\nb\nc\n", "a\nb\nnew\nc\n"), {2, 3})
        self.assertEqual(gold_lines("a\n", "a\nnew\n"), {1})


class UnitTests(unittest.TestCase):
    def test_go_functions_and_module_remainder(self):
        units = code_units("x.go", GO)
        names = {(u["name"], u["start"], u["end"]) for u in units if u["kind"] == "function"}
        self.assertEqual(names, {("helper", 3, 5), ("Run", 7, 9)})
        self.assertTrue(any(u["kind"] == "module" and u["start"] == 1 for u in units))

    def test_python_decorators_and_methods(self):
        units = {u["name"]: u for u in code_units("x.py", PY) if u["kind"] == "function"}
        self.assertEqual((units["first"]["start"], units["first"]["end"]), (4, 6))
        self.assertEqual((units["second"]["start"], units["second"]["end"]), (12, 13))

    def test_javascript_declarations_and_arrow_bindings(self):
        units = {u["name"]: u for u in code_units("x.js", JS) if u["kind"] == "function"}
        self.assertEqual((units["onSend"]["start"], units["onSend"]["end"]), (2, 4))
        self.assertEqual((units["wrap"]["start"], units["wrap"]["end"]), (5, 5))

    def test_every_line_belongs_to_exactly_one_unit(self):
        for path, text in [("x.go", GO), ("x.py", PY), ("x.js", JS), ("README.md", "a\n" * 50)]:
            covered = sorted(n for u in code_units(path, text) for n in range(u["start"], u["end"] + 1))
            self.assertEqual(covered, list(range(1, len(text.splitlines()) + 1)), path)

    def test_gold_units_are_the_enclosing_units(self):
        units = code_units("x.go", GO)
        self.assertEqual({u["name"] for u in gold_units(units, {4})}, {"helper"})


class GraphTests(unittest.TestCase):
    def test_calls_link_units_by_defined_name(self):
        units = code_units("x.go", GO) + code_units("x.js", JS)
        edges = call_edges(units)
        name = {i: u["name"] for i, u in enumerate(units)}
        self.assertIn(("Run", "helper"), {(name[a], name[b]) for a, b in edges})
        self.assertIn(("onSend", "wrap"), {(name[a], name[b]) for a, b in edges})

    def test_propagation_adds_half_the_best_neighbour(self):
        self.assertEqual(propagate([1.0, 4.0, 0.0], [(0, 1), (1, 2)], 0.5), [3.0, 4.5, 2.0])


class PackAndScoreTests(unittest.TestCase):
    def test_packing_stops_at_the_score_floor_and_budget(self):
        units = [{"path": "a.go", "start": 1, "end": 1, "text": "x"},
                 {"path": "b.go", "start": 1, "end": 1, "text": "y"},
                 {"path": "c.go", "start": 1, "end": 1, "text": "z"}]
        picked, _ = pack_units(units, [3.0, 2.0, 1.0], budget=2000, floor=0.5)
        self.assertEqual([u["path"] for u in picked], ["a.go", "b.go"])
        picked, _ = pack_units(units, [3.0, 2.9, 2.8], budget=8, floor=0.5)
        self.assertEqual(len(picked), 1)

    def test_graphify_locations_skip_file_and_external_nodes(self):
        text = ("NODE Completion [src= loc= community=]\n"
                "NODE .Flags() [src=command.go loc=L1688 community=]\n"
                "NODE command_test.go [src=command_test.go loc=L1 community=]\n")
        self.assertEqual(graphify_locations(text), (["command.go", "command_test.go"], [("command.go", 1688)]))

    def test_function_and_file_hits(self):
        units = code_units("x.go", GO)
        gold = {"x.go": {4}}
        snapshot_units = {"x.go": units}
        hit = score_task(gold, snapshot_units, files=["x.go"], spans=[("x.go", 3, 3)], shown="")
        self.assertEqual((hit["file_hit"], hit["unit_hit"]), (True, True))
        miss = score_task(gold, snapshot_units, files=["x.go"], spans=[("x.go", 7, 9)], shown="")
        self.assertEqual((miss["file_hit"], miss["unit_hit"]), (True, False))
        shown = score_task(gold, snapshot_units, files=["x.go"], spans=[("x.go", 3, 5)], shown=GO)
        self.assertTrue(shown["gold_line_shown"])


class DecisionTests(unittest.TestCase):
    def test_exact_mcnemar_counts_only_discordant_tasks(self):
        self.assertAlmostEqual(mcnemar_one_sided([1, 1, 0, 1], [1, 0, 0, 0]), 0.25)
        self.assertEqual(mcnemar_one_sided([1, 0], [1, 0]), 1.0)

    def test_an_arm_must_beat_every_comparator_after_holm(self):
        rows = []
        for i in range(40):
            rows.append({"task": f"t{i}", "repo": "r", "arms": {
                "ast_units": {"unit_rank": 1}, "ast_graph": {"unit_rank": 1 if i < 20 else None},
                "graf_floor": {"unit_rank": 3 if i < 20 else 9}, "graphify": {"unit_rank": 2 if i < 5 else None}}})
        result = decide(rows, ["ast_units", "ast_graph"], ["graf_floor", "graphify"], 0.05)
        self.assertTrue(result["arms"]["ast_units"]["qualifies"])
        self.assertFalse(result["arms"]["ast_graph"]["qualifies"])
        self.assertEqual(result["arms"]["ast_units"]["rate"], 1.0)


class RankAndRewriteTests(unittest.TestCase):
    def test_cited_units_keep_citation_order_without_duplicates(self):
        units = {"x.go": code_units("x.go", GO)}
        order = cited_units([("x.go", 7, 7), ("x.go", 4, 8), ("y.go", 1, 1)], units)
        self.assertEqual([(u["name"]) for u in order][:2], ["Run", "helper"])
        self.assertEqual(len(order), 3)  # Run, helper, and the blank module line 6 between them.

    def test_accuracy_at_k(self):
        ranked = [("a", 1), ("b", 2), ("c", 3)]
        self.assertEqual(accuracy_at(ranked, {("c", 3)}, 2), False)
        self.assertEqual(accuracy_at(ranked, {("c", 3)}, 3), True)

    def test_bounded_units_split_long_functions_at_the_window(self):
        text = "func long() {\n" + "\tx()\n" * 50 + "}\n"
        pieces = [u for u in bounded_units(code_units("x.go", text)) if u["kind"] == "function"]
        self.assertEqual([(u["start"], u["end"]) for u in pieces], [(1, 24), (25, 48), (49, 52)])
        self.assertTrue(all(u["name"] == "long" for u in pieces))

    def test_rewrite_keeps_title_and_code_like_tokens(self):
        issue = ("reply.send fails\n\nWhen I call `reply.code(204)` the onSend hook in lib/reply.js "
                 "throws FST_ERR_REP_ALREADY_SENT and the docs say nothing.\n```js\nfastify.addHook('onSend', fn)\n```")
        query = rewrite_query(issue)
        for token in ["reply.send", "reply.code", "onSend", "lib/reply.js", "FST_ERR_REP_ALREADY_SENT", "addHook"]:
            self.assertIn(token, query)
        self.assertNotIn("docs", query.split("\n", 1)[1])
        self.assertEqual(rewrite_query("plain words only"), "plain words only")


class CorpusGoldTests(unittest.TestCase):
    def test_gold_outside_the_corpus_is_dropped_and_reported(self):
        kept, dropped = corpus_gold({"lib/a.js": [1], ".github/x.js": [2]}, {"lib/a.js": "x"})
        self.assertEqual((kept, dropped), ({"lib/a.js": {1}}, [".github/x.js"]))
        self.assertEqual(corpus_gold({".github/x.js": [2]}, {"lib/a.js": "x"}), ({}, [".github/x.js"]))


class WriteTests(unittest.TestCase):
    def test_a_failed_write_keeps_the_previous_file(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "r.json"
            write_json_atomic(out, {"a": 1})
            with patch("json.dumps", side_effect=OSError("disk full")), self.assertRaises(OSError):
                write_json_atomic(out, {"a": 2})
            self.assertEqual(out.read_text(), '{\n "a": 1\n}\n')
            self.assertEqual(list(Path(tmp).iterdir()), [out])


class ReplicationTests(unittest.TestCase):
    def test_replication_has_its_own_catalog_and_rule(self):
        self.assertTrue(catalog_for("replication").name.startswith("localization-replication"))
        self.assertEqual(catalog_for("heldout"), catalog_for("dev"))
        import json
        from pathlib import Path
        screen = json.loads((Path(__file__).resolve().parents[1] / "benchmark/preregistration.json").read_text())["localization_screen"]
        rule = screen["replication"]["decision"]
        self.assertEqual(rule["candidate_arms"], ["ast_graph", "graf_floor_ids"])
        self.assertEqual(rule["comparators"], ["graf_floor", "graphify"])


class GrafCommandTests(unittest.TestCase):
    def test_a_dash_leading_query_is_not_parsed_as_an_option(self):
        from pathlib import Path
        command = graf_locate_command(Path("root"), "graf_floor", "--verbose flag", 2000)
        self.assertEqual(command[-2:], ["--", "--verbose flag"])
        self.assertIn("--stop", command[:-2])


class ConfirmationTests(unittest.TestCase):
    def test_confirmation_split_and_rule(self):
        import json
        from pathlib import Path
        self.assertTrue(catalog_for("confirmation").name.startswith("localization-confirmation"))
        screen = json.loads((Path(__file__).resolve().parents[1] / "benchmark/preregistration.json").read_text())["localization_screen"]
        rule = screen["confirmation"]["decision"]
        self.assertEqual(rule["candidate_arms"], ["ast_graph_ids"])
        self.assertEqual(set(rule["comparators"]), {"graphify", "ast_graph", "graf_floor_ids"})
        import localization
        self.assertIn("ast_graph_ids", localization.ARMS)


class FileScreenTests(unittest.TestCase):
    def test_file_scores_rise_by_half_the_best_linked_file(self):
        units = code_units("a.go", GO) + code_units("b.js", JS)
        units[1] = {**units[1], "calls": ["onSend"]}  # helper (a.go) calls onSend (b.js)
        ranked = file_propagate({"a.go": 2.0}, units, call_edges(units), 0.5)
        self.assertEqual(ranked, ["a.go", "b.js"])
        self.assertEqual(file_propagate({"a.go": 2.0, "c.py": 3.0}, units, [], 0.5), ["c.py", "a.go"])

    def test_decide_on_file_rank(self):
        rows = [{"task": f"t{i}", "repo": "r", "arms": {
            "x": {"unit_rank": None, "file_rank": 1}, "y": {"unit_rank": 1, "file_rank": None if i < 15 else 1}}}
            for i in range(20)]
        result = decide(rows, ["x"], ["y"], 0.05, 5, metric="file_acc")
        self.assertTrue(result["arms"]["x"]["qualifies"])
        self.assertFalse(decide(rows, ["x"], ["y"], 0.05, 5)["arms"]["x"]["qualifies"])

    def test_file_screen_split_is_registered(self):
        self.assertTrue(catalog_for("file_screen").name.startswith("localization-file-screen"))


if __name__ == "__main__":
    unittest.main()
