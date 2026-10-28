import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from io import StringIO

from readonly_broker import Broker
from agent_backend import QUERY_ARGS


class ReadonlyBrokerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        self.source = self.root / "src.py"
        self.source.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
        self.binary = self.root / "backend"
        self.binary.write_bytes(b"pinned executable placeholder")
        self.plan = {"comparison": {"shared_output_budget": 64},
                     "arms": [{"id": "graf_v59", "binary": str(self.binary),
                               "binary_sha256": self.sha(self.binary.read_bytes()),
                               "query_args": QUERY_ARGS["graf_v59"]}],
                     "source_manifest": {"src.py": self.sha(self.source.read_bytes())}}
        self.calls = []

    @staticmethod
    def sha(data):
        return hashlib.sha256(data).hexdigest()

    def broker(self, runner=None, max_actions=4):
        return Broker(self.plan, "graf_v59", self.root, self.root / "graph.json",
                      self.base / ("j" + str(len(list(self.base.glob("j*"))))),
                      max_actions=max_actions, runner=runner or self.run_backend)

    def run_backend(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="backend output " * 20, stderr="", args=argv)

    def call(self, broker, args, ident=1, name="read", meta=None):
        params = {"name": name, "arguments": args}
        if meta is not None:
            params["_meta"] = meta
        return broker.handle({"jsonrpc": "2.0", "id": ident, "method": "tools/call", "params": params})

    def journal(self, b):
        return [json.loads(line) for line in b.journal.read_text().splitlines()]

    def test_mcp_inventory_and_meta(self):
        b = self.broker()
        init = b.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        self.assertEqual(init["result"]["serverInfo"]["version"], "1")
        names = [x["name"] for x in b.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]]
        self.assertEqual(names, ["retrieve", "read"])
        reply = self.call(b, {"path": "src.py"}, meta={"progressToken": "opaque"})
        self.assertFalse(reply["result"]["isError"])

    def test_bad_types_and_extra_fields_consume_action(self):
        b = self.broker(max_actions=3)
        for arg in ({"path": "src.py", "start": True}, {"path": "src.py", "wat": 1}, {"path": "src.py", "budget": True}):
            self.assertTrue(self.call(b, arg)["result"]["isError"])
        self.assertTrue(self.call(b, {"path": "src.py"})["result"]["isError"])
        self.assertEqual(len(self.journal(b)), 8)

    def test_action_cap_counts_refusal(self):
        b = self.broker(max_actions=1)
        self.assertFalse(self.call(b, {"path": "src.py"})["result"]["isError"])
        self.assertTrue(self.call(b, {"path": "src.py"})["result"]["isError"])
        self.assertEqual([r["action"] for r in self.journal(b) if r["kind"] == "begin"], [1, 2])

    def test_traversal_hidden_and_symlink_refused(self):
        outside = self.base / "secret.py"
        outside.write_text("secret")
        self.plan["source_manifest"].update({"../secret.py": self.sha(outside.read_bytes()), ".hidden.py": self.sha(b"x")})
        with self.assertRaises(ValueError):
            self.broker()

    def test_symlink_and_unmanifested_source_refused(self):
        (self.root / "link.py").symlink_to(self.source)
        b = self.broker()
        self.assertTrue(self.call(b, {"path": "link.py"})["result"]["isError"])
        self.assertTrue(self.call(b, {"path": "../src.py"})["result"]["isError"])

    def test_manifest_byte_change_refused(self):
        b = self.broker()
        self.source.write_text("changed")
        self.assertIn("manifest", self.call(b, {"path": "src.py"})["result"]["content"][0]["text"])

    def test_retrieve_checks_changed_and_extra_inventory_before_backend(self):
        b = self.broker()
        self.source.write_text("changed")
        changed = self.call(b, {"query": "q"}, name="retrieve")
        self.assertTrue(changed["result"]["isError"])
        self.assertEqual(self.calls, [])
        self.source.write_text("alpha\nbeta\ngamma\n")
        (self.root / "extra.txt").write_text("unfrozen")
        extra = self.call(b, {"query": "q"}, name="retrieve", ident=2)
        self.assertTrue(extra["result"]["isError"])
        self.assertEqual(self.calls, [])

    def test_source_clip_uses_whole_lines(self):
        self.source.write_text("one\ntwo\nthree\n")
        self.plan["source_manifest"]["src.py"] = self.sha(self.source.read_bytes())
        b = self.broker()
        result = self.call(b, {"path": "src.py", "budget": 2})["result"]["source"]
        self.assertTrue(result["clipped"])
        self.assertNotIn("two", result["text"])

    def test_single_overlong_line_is_not_partially_returned(self):
        self.source.write_text("x" * 1000 + "\nshort\n")
        self.plan["source_manifest"]["src.py"] = self.sha(self.source.read_bytes())
        b = self.broker()
        result = self.call(b, {"path": "src.py", "budget": 20})["result"]["source"]
        self.assertEqual(result["text"], "")
        self.assertTrue(result["clipped"])

    def test_exact_argv_and_backend_overrun_preserved(self):
        b = self.broker()
        reply = self.call(b, {"query": "exact literal query", "budget": 3}, name="retrieve")
        backend = json.loads(reply["result"]["content"][0]["text"])
        self.assertEqual(self.calls[0][0], [str(b.binary), "--root", str(b.root), "evidence-rescue",
                                             "exact literal query", "--budget", "3", "--limit", "20", "--text"])
        self.assertEqual(backend["stdout"], "backend output " * 20)
        rec = self.journal(b)[-1]
        self.assertEqual(json.loads(rec["wire_line"]), reply)
        self.assertEqual(rec["wire_bytes"], len(rec["wire_line"].encode()))

    def test_backend_error_fails_closed_for_retrieval(self):
        attempts = []
        def fail(*a, **kw):
            attempts.append(a[0])
            return SimpleNamespace(returncode=7, stdout="partial", stderr="failure")
        b = self.broker(runner=fail)
        first = self.call(b, {"query": "q"}, name="retrieve")
        self.assertEqual(json.loads(first["result"]["content"][0]["text"])["stderr"], "failure")
        second = self.call(b, {"query": "q"}, name="retrieve", ident=2)
        self.assertTrue(second["result"]["isError"])
        self.assertEqual(len(attempts), 1)

    def test_journal_is_exclusive_and_permissions_private(self):
        b = self.broker()
        self.assertEqual(os.stat(b.journal).st_mode & 0o777, 0o600)
        with self.assertRaises(ValueError):
            Broker(self.plan, "graf_v59", self.root, self.root / "graph.json", b.journal, runner=self.run_backend)

    def test_journal_failure_prevents_backend_dispatch(self):
        b = self.broker()
        os.close(b._journal_fd)
        with self.assertRaises(OSError):
            self.call(b, {"query": "q"}, name="retrieve")
        self.assertTrue(b._journal_poisoned)
        with self.assertRaises(OSError):
            self.call(b, {"query": "q"}, name="retrieve", ident=2)
        self.assertEqual(self.calls, [])

    def test_serve_records_raw_and_canonical_wire(self):
        b = self.broker()
        source_call = '{ "jsonrpc" : "2.0", "id":1, "method":"tools/call", "params":{"name":"read","arguments":{"path":"src.py"}} }\n'
        malformed = '{"jsonrpc":"2.0","jsonrpc":"2.0","id":2,"method":"ping"}\n'
        output = StringIO()
        b.serve(StringIO(source_call + malformed), output)
        emitted = output.getvalue().splitlines()
        self.assertEqual(len(emitted), 2)
        records = self.journal(b)
        raw = [record for record in records if record["kind"] == "raw_input"]
        self.assertEqual(raw[0]["raw_line"], source_call)
        replies = [record for record in records if record["kind"] in {"reply", "control_reply"}]
        self.assertEqual(json.loads(replies[-1]["wire_line"]), json.loads(emitted[-1]))
        self.assertIn('"id":1', [r["wire_line"] for r in replies if r["kind"] == "reply"][0])


if __name__ == "__main__":
    unittest.main()
