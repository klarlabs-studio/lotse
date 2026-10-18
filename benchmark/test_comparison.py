"""Integrity checks for the common Graphify/graf comparison metrics."""
import unittest
import hashlib
import json
import tempfile
from pathlib import Path
from compare_graphify import cited_paths, cited_path_order, code_hint_recall, validate_locate


class ComparisonIntegrity(unittest.TestCase):
    def test_shared_metric_counts_paths_not_graph_nodes_or_source_anchors(self):
        root = Path("/scratch/input")
        files = {"args.go": "one", "guide.md": "two"}
        graf = '"args.go":1-24\nsource\n\n"guide.md":1-12\ndocument'
        graphify = '[src=/scratch/input/args.go loc=L17 community=1]\n[src=external loc=L1 community=2]'
        self.assertEqual(cited_paths("graf", graf, root, files), ["args.go", "guide.md"])
        located = '\"args.go\":1-24\nid=' + 'a' * 64 + '\nL17: \"func ExactArgs()\"'
        self.assertEqual(cited_paths("graf_locate", located, root, files), ["args.go"])
        self.assertEqual(cited_paths("graphify", graphify, root, files), ["args.go"])
        case = {"evidence": [{"path": "args.go", "start": 999}, {"path": "guide.md", "start": 2}]}
        # This is a file discovery score, never a claim that line 999 was retrieved.
        self.assertEqual(code_hint_recall(case, ["args.go"]), 1)
        self.assertEqual(code_hint_recall(case, []), 0)

    def test_first_file_metrics_preserve_delivery_order(self):
        context = '\"z.rs\":1-24\nid=handle\nL1: \"source\"\n\n\"a.rs\":1-24\nid=handle2\nL1: \"source\"'
        order = cited_path_order("graf_locate", context, Path("/scratch"), {"a.rs": "", "z.rs": ""})
        self.assertEqual(order, ["z.rs", "a.rs"])
        self.assertEqual(code_hint_recall({"evidence": [{"path": "z.rs"}]}, order[:1]), 1)
        self.assertEqual(cited_paths("graf_locate", context, Path("/scratch"), {"a.rs": "", "z.rs": ""}), ["a.rs", "z.rs"])

    def test_locate_text_validation_rejects_forged_previews_and_handles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            text = "// cache policy\nfn cache() {}\n"
            (root / "cache.rs").write_text(text)
            hashed = hashlib.sha256(text.encode()).hexdigest()
            # Explicit join keeps source range digits separate from NUL delimiters.
            handle = hashlib.sha256("\0".join(["cache.rs", hashed, "1", "2"]).encode()).hexdigest()
            index = {"files": {"cache.rs": {"hash": hashed, "chunks": [{"id": handle, "start": 1, "end": 2}]}}}
            context = f'"cache.rs":1-2\nid={handle}\nL2: ' + json.dumps("fn cache() {}")
            self.assertEqual(validate_locate(context, root, index), 1)
            self.assertEqual(validate_locate(context.replace(handle, handle[:12]), root, index), 1)
            with self.assertRaises(AssertionError):
                validate_locate(context.replace("fn cache() {}", "invented"), root, index)
            with self.assertRaises(AssertionError):
                validate_locate(context.replace(handle, "a" * 64), root, index)
            collision = handle[:12] + ("0" if handle[12] != "0" else "1") + handle[13:]
            index["files"]["cache.rs"]["chunks"].append({"id": collision, "start": 1, "end": 2})
            with self.assertRaises(AssertionError):
                validate_locate(context.replace(handle, handle[:12]), root, index)

    def test_docs_only_and_negative_requests_are_not_perfect_code_recall(self):
        self.assertIsNone(code_hint_recall({"evidence": []}, []))
        self.assertIsNone(code_hint_recall({"evidence": [{"path": "guide.rst"}]}, ["guide.rst"]))

    def test_graf_quoted_paths_are_decoded_without_inventing_unknown_sources(self):
        files = {'a"b.rs': "one"}
        context = '"a\\"b.rs":1-24\nsource\n\n"missing.rs":1-24\nsource'
        self.assertEqual(cited_paths("graf", context, Path("/scratch"), files), ['a"b.rs'])


if __name__ == "__main__":
    unittest.main()
