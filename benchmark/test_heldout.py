"""Held-out drafts must cite real, hashed, code-bearing evidence."""
import copy
import hashlib
import json
import unittest

from check_heldout import STATUS, check_case
from retrieval import BASE, load_documents


class HeldoutCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.documents = {"cobra": load_documents(BASE / ".research/corpora/cobra")}
        cls.development = {q["query"].strip().lower() for q in
                           json.loads((BASE / "benchmark/queries.json").read_text())}

    def evidence(self, path, anchor):
        text = self.documents["cobra"][path]
        line = next(i for i, l in enumerate(text.splitlines(), 1) if anchor in l)
        return {"path": path, "start": line, "end": line, "anchor": anchor,
                "file_sha256": hashlib.sha256(text.encode()).hexdigest()}

    def setUp(self):
        self.case = {"id": "h-cobra-01", "repo": "cobra", "category": "exact",
                     "query": "Where is the validator that requires an exact argument count?",
                     "evidence": [self.evidence("args.go", "func ExactArgs(")],
                     "expected": "retrieve_evidence", "annotation_status": STATUS}

    def errors(self, case):
        return check_case(case, self.documents, self.development)

    def test_valid_case_passes(self):
        self.assertEqual(self.errors(self.case), [])

    def test_wrong_hash_line_or_path_fails(self):
        for field, value in (("file_sha256", "0" * 64), ("start", 1), ("path", "missing.go")):
            case = copy.deepcopy(self.case)
            case["evidence"][0][field] = value
            self.assertTrue(self.errors(case), field)

    def test_answerable_question_needs_code_evidence(self):
        case = copy.deepcopy(self.case)
        case["evidence"] = [self.evidence("README.md", "Cobra")]
        self.assertIn("answerable question without code evidence", self.errors(case))

    def test_negative_controls_have_no_evidence_and_dev_queries_are_rejected(self):
        case = copy.deepcopy(self.case)
        case.update(category="no_evidence", expected="insufficient_repository_evidence")
        self.assertTrue(self.errors(case))
        case["evidence"] = []
        self.assertEqual(self.errors(case), [])
        case["query"] = json.loads((BASE / "benchmark/queries.json").read_text())[0]["query"]
        self.assertIn("repeats a development query", self.errors(case))


if __name__ == "__main__":
    unittest.main()
