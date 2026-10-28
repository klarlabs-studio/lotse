"""Confirmation packets reject contamination, tampering and invalid labels."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from confirmation import STATUS, historical_exposure, prepare, sha, validate, write_json


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "benchmark").mkdir()
        (self.root / "results").mkdir()
        source = self.root / ".research/corpora/fresh"
        source.mkdir(parents=True)
        (source / "impl.py").write_text("def fresh():\n    return 42\n")
        (source / "README.md").write_text("Fresh API.\n")
        self.spec = self.root / "benchmark/confirmation-v62.json"
        write_json(self.spec, {"id": "test", "repositories": [
            {"id": "fresh", "repository": "example/fresh", "commit": "a" * 40}],
            "category_quota": {"conceptual": 1, "no_evidence": 1},
            "minimum_scored_queries": 1})
        self.directory = self.root / "packet"
        self.receipt = prepare(self.spec, self.directory, self.root)
        self.packet = self.directory / "packet.json"
        self.queries = self.root / "questions.json"
        self.review = self.root / "review.json"
        evidence = {"path": "impl.py", "start": 1, "end": 2, "anchor": "def fresh():",
                    "file_sha256": sha(self.directory / "sources/fresh/impl.py")}
        self.cases = [
            {"id": "h-fresh-01", "repo": "fresh", "category": "conceptual",
             "query": "Where is the constant answer returned?", "evidence": [evidence],
             "expected": "retrieve_evidence", "annotation_status": STATUS},
            {"id": "h-fresh-02", "repo": "fresh", "category": "no_evidence",
             "query": "Where is distributed tracing implemented?", "evidence": [],
             "expected": "insufficient_repository_evidence", "annotation_status": STATUS}]
        self.declaration = {"author_id": "independent-test-author", "source_only": True,
                            "independent_of_tuning": True, "packet_sha256": sha(self.packet),
                            "no_evidence_rationales": {"h-fresh-02":
                                "Reviewed all source files; only a constant-returning function is present."}}

    def errors(self, cases=None, declaration=None, historical=()):
        write_json(self.queries, self.cases if cases is None else cases)
        value = copy.deepcopy(self.declaration if declaration is None else declaration)
        value.setdefault("queries_sha256", sha(self.queries))
        write_json(self.review, value)
        return validate(self.packet, self.queries, self.review,
                        self.receipt["packet_sha256"], set(historical))

    def test_valid_packet_and_questions_only_establish_structural_validity(self):
        self.assertEqual(self.errors(), [])
        packet = json.loads(self.packet.read_text())
        self.assertFalse(packet["product_promotion_allowed"])
        self.assertFalse(packet["retrieval_has_run"])
        self.assertEqual(packet["status"], "awaiting_independent_authoring")

    def test_packet_contains_only_sources_and_author_instructions(self):
        self.assertEqual({p.relative_to(self.directory).as_posix() for p in
                          self.directory.rglob("*") if p.is_file()}, {
            "packet.json", "source-manifest.json", "AUTHORING.md",
            "sources/fresh/impl.py", "sources/fresh/README.md"})

    def test_source_or_instruction_tampering_rejects(self):
        for name in ("sources/fresh/impl.py", "AUTHORING.md", "source-manifest.json"):
            path = self.directory / name
            original = path.read_bytes()
            path.write_bytes(original + b"\n")
            self.assertTrue(self.errors(), name)
            path.write_bytes(original)

    def test_packet_quota_tampering_cannot_relax_frozen_requirements(self):
        packet = json.loads(self.packet.read_text())
        packet["minimum_scored_queries"] = 0
        packet["category_quota"] = {}
        write_json(self.packet, packet)
        self.assertIn("Packet differs from the frozen preparation receipt", self.errors([]))

    def test_symlink_substitution_is_rejected(self):
        source = self.directory / "sources/fresh/impl.py"
        other = self.root / "same-content.py"
        other.write_bytes(source.read_bytes())
        source.unlink()
        source.symlink_to(other)
        self.assertTrue(self.errors())

    def test_wrong_hash_anchor_range_or_path_rejects(self):
        for field, value in (("file_sha256", "0" * 64), ("anchor", "missing"),
                             ("start", 0), ("end", 999), ("start", True),
                             ("path", "../outside.py")):
            cases = copy.deepcopy(self.cases)
            cases[0]["evidence"][0][field] = value
            self.assertTrue(self.errors(cases), field)

    def test_duplicate_ids_questions_and_historical_queries_reject(self):
        for field in ("id", "query"):
            cases = copy.deepcopy(self.cases)
            cases[1][field] = cases[0][field]
            self.assertTrue(self.errors(cases), field)
        self.assertTrue(self.errors(historical=[self.cases[0]["query"].casefold()]))

    def test_missing_independence_and_negative_rationale_reject(self):
        for field in ("author_id", "independent_of_tuning", "source_only", "no_evidence_rationales"):
            declaration = copy.deepcopy(self.declaration)
            del declaration[field]
            self.assertTrue(self.errors(declaration=declaration), field)

    def test_declaration_is_bound_to_packet_and_questions(self):
        for field in ("packet_sha256", "queries_sha256"):
            declaration = {**self.declaration, field: "0" * 64}
            self.assertTrue(self.errors(declaration=declaration), field)

    def test_quotas_sample_size_and_code_evidence_are_required(self):
        self.assertTrue(self.errors([]))
        cases = copy.deepcopy(self.cases)
        item = cases[0]["evidence"][0]
        item.update(path="README.md", start=1, end=1, anchor="Fresh API.",
                    file_sha256=sha(self.directory / "sources/fresh/README.md"))
        self.assertTrue(self.errors(cases))

    def test_repeated_prepare_never_overwrites(self):
        with self.assertRaisesRegex(ValueError, "Refusing to overwrite"):
            prepare(self.spec, self.directory, self.root)

    def test_exposed_repository_rejected_before_creating_packet(self):
        for value in ([{"repo": "fresh"}], [{"id": "different", "repository": "EXAMPLE/FRESH"}]):
            write_json(self.root / "results/snapshots-old.json", value)
            output = self.root / "another-packet"
            with self.assertRaisesRegex(ValueError, "Previously exposed"):
                prepare(self.spec, output, self.root)
            self.assertFalse(output.exists())

    def test_exposure_tracks_input_hashes_and_normalizes_questions(self):
        write_json(self.root / "benchmark/prior.json", [{"repo": "old", "query": " Where  IS it? "}])
        result = historical_exposure(self.root)
        self.assertIn("old", result["repositories"])
        self.assertIn("where is it?", result["queries"])
        self.assertIn("benchmark/prior.json", result["input_sha256"])
        self.assertNotIn("fresh", result["repositories"])

    def test_malformed_cases_return_validation_errors(self):
        for cases in ({}, [None], [{"id": "oops"}]):
            self.assertTrue(self.errors(cases))
        cases = copy.deepcopy(self.cases)
        cases[0]["evidence"] = [None]
        self.assertTrue(self.errors(cases))


if __name__ == "__main__":
    unittest.main()
