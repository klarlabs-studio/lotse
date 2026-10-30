"""The t12 ranking secondary reads triage's first listed file and pairs the graf arms by task."""
import json
import unittest

from t12_diagnostic import ranking, triage_files


def row(task, arm, first, called=True):
    return {"task": task, "arm": arm, "triage_called": called, "first_file_faulted": first}


class T12DiagnosticTest(unittest.TestCase):
    def test_triage_files_reads_the_likely_files_list_of_an_mcp_text_result(self):
        result = json.dumps([{"type": "text", "text": "Likely files:\n- a.go\n- b.go\n\nLikely code:\na.go:1-2\n"}])
        self.assertEqual(triage_files(result), ["a.go", "b.go"])
        self.assertEqual(triage_files("Likely code:\na.go:1-2"), [])

    def test_ranking_pairs_tasks_where_both_graf_arms_called_triage(self):
        rows = [row(f"t{i}", "graf_installed_codefiles", True) for i in range(6)]
        rows += [row(f"t{i}", "graf_installed", False) for i in range(6)]
        rows += [row("t9", "graf_installed_codefiles", True), row("t9", "graf_installed", False, called=False)]
        result = ranking(rows)
        self.assertEqual(result["tasks"], 6)
        self.assertEqual(result["mean_difference"], 1.0)
        self.assertTrue(result["codefiles_ranks_better"])


if __name__ == "__main__":
    unittest.main()
