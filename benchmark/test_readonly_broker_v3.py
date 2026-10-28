import json
import hashlib
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from agent_backend import QUERY_ARGS
from readonly_broker_v3 import Broker
from readonly_broker_v2 import source_manifest_sha256
import readonly_broker


class BrokerV3Tests(unittest.TestCase):
    def setUp(self):
        self.token_patch = patch.object(readonly_broker, "tokens", lambda _text: [])
        self.token_patch.start()
        self.addCleanup(self.token_patch.stop)
        self.v3_token_patch = patch("readonly_broker_v3.tokens", lambda _text: [])
        self.v3_token_patch.start()
        self.addCleanup(self.v3_token_patch.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "repo"
        self.root.mkdir()
        self.root = self.root.resolve()
        self.source = self.root / "src.py"
        self.source.write_text("hello\n", encoding="utf8")
        self.artifact = self.root / ".graf" / "index.bin"
        self.artifact.parent.mkdir()
        self.artifact.write_bytes(b"index")
        self.binary = self.root / "backend"
        self.binary.write_bytes(b"binary")
        self.graph = self.base / "graph.json"
        self.graph.write_bytes(b"graph")
        self.graph = self.graph.resolve()
        self.calls = []

    @staticmethod
    def sha(data):
        return hashlib.sha256(data).hexdigest()

    def make_plan(self, arm_id="graf_v61"):
        artifact = self.graph if arm_id == "graphify" else self.artifact
        manifest = {"src.py": self.sha(self.source.read_bytes())}
        return {"comparison": {"shared_output_budget": 64},
                "subprocess_environment": {"PYTHONHASHSEED": "0"},
                "arms": [{"id": arm_id, "binary": str(self.binary),
                          "binary_sha256": self.sha(self.binary.read_bytes()), "query_args": QUERY_ARGS[arm_id]}],
                "source_manifest": manifest,
                "artifacts": {arm_id: {"path": str(artifact), "sha256": self.sha(artifact.read_bytes()),
                                        "source_manifest_sha256": source_manifest_sha256(manifest)}}}

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    def make_broker(self, runner=None, arm="graf_v61"):
        plan = self.make_plan(arm)
        journal = self.base / ("journal-" + str(len(list(self.base.glob("journal-*")))))
        return Broker(plan, arm, self.root, self.graph, journal, runner=runner or self.runner)

    @staticmethod
    def call(broker):
        return broker.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "retrieve", "arguments": {"query": "hello"}}})

    def test_successful_retrieve_has_two_full_inventory_scans(self):
        broker = self.make_broker()
        original = broker._verify_inventory
        count = []

        def counted():
            count.append(1)
            return original()

        with patch.object(broker, "_verify_inventory", side_effect=counted):
            reply = self.call(broker)
        self.assertFalse(reply["result"]["isError"])
        self.assertEqual(len(count), 2)

    def test_query_construction_mutations_refused_before_child(self):
        mutations = [
            lambda: self.source.write_text("changed\n", encoding="utf8"),
            lambda: (self.root / "new.py").write_text("new\n", encoding="utf8"),
            lambda: (self.source.unlink(), self.source.symlink_to(self.root / "target.py"),
                     (self.root / "target.py").write_text("hello\n", encoding="utf8")),
            lambda: self.binary.write_bytes(b"changed binary"),
            lambda: self.artifact.write_bytes(b"changed artifact"),
        ]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                # Each mutation gets a fresh fixture so the inventory is valid
                # when broker initialization completes.
                self.source.write_text("hello\n", encoding="utf8")
                self.binary.write_bytes(b"binary")
                self.artifact.write_bytes(b"index")
                for name in ("new.py", "link.py"):
                    path = self.root / name
                    if path.is_symlink() or path.exists():
                        path.unlink()
                if self.source.is_symlink():
                    self.source.unlink()
                    self.source.write_text("hello\n", encoding="utf8")
                target = self.root / "target.py"
                if target.exists():
                    target.unlink()
                self.calls.clear()
                broker = self.make_broker()
                from agent_backend import query_command as real_query_command

                def change_then_build(*args, **kwargs):
                    mutate()
                    return real_query_command(*args, **kwargs)

                with patch("readonly_broker_v3.query_command", side_effect=change_then_build):
                    reply = self.call(broker)
                self.assertTrue(reply["result"]["isError"])
                self.assertEqual(self.calls, [])

    def test_post_child_source_mutation_preserves_payload_and_latches(self):
        def mutate(argv, **kwargs):
            self.calls.append((argv, kwargs))
            self.source.write_text("changed\n", encoding="utf8")
            return SimpleNamespace(returncode=13, stdout="partial", stderr="diagnostic")

        broker = self.make_broker(runner=mutate)
        first = self.call(broker)
        record = json.loads(first["result"]["content"][0]["text"])
        self.assertEqual(record["backend_failure"]["stdout"], "partial")
        self.assertEqual(record["backend_failure"]["stderr"], "diagnostic")
        self.assertEqual(record["backend_failure"]["returncode"], 13)
        self.assertTrue(self.call(broker)["result"]["isError"])
        self.assertEqual(len(self.calls), 1)

    def test_read_still_checks_source_bytes_and_invalidargs_limits_and_no_match(self):
        broker = self.make_broker()
        read = broker.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": "read", "arguments": {"path": "src.py"}}})
        self.assertFalse(read["result"]["isError"])
        self.source.write_text("changed\n", encoding="utf8")
        changed = broker.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                 "params": {"name": "read", "arguments": {"path": "src.py"}}})
        self.assertTrue(changed["result"]["isError"])

        broker = self.make_broker()
        invalid = broker.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": "retrieve", "arguments": {"query": " "}}})
        self.assertTrue(invalid["result"]["isError"])
        limited = self.make_broker()
        limited.max_actions = 1
        self.call(limited)
        rejected = self.call(limited)
        self.assertTrue(rejected["result"]["isError"])

        no_match = self.make_broker(arm="graphify", runner=lambda *a, **k: SimpleNamespace(
            returncode=4, stdout="No matching nodes found.\n", stderr=""))
        result = self.call(no_match)["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(result["outcome"], "no_match")


if __name__ == "__main__":
    unittest.main()
