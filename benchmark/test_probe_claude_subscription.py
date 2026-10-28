import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import probe_claude_subscription as probe


class SubscriptionProbeTests(unittest.TestCase):
    def test_command_disables_builtin_tools_and_limits_turns(self):
        for case, turns in (("plain", "1"), ("ping", "3")):
            cmd = probe.command("/fake/claude", case, Path("/tmp/mcp.json"))
            for flag, value in (("--tools", ""), ("--model", "haiku"),
                                ("--max-budget-usd", "0.10"), ("--max-turns", turns),
                                ("--setting-sources", ""), ("--permission-mode", "dontAsk")):
                self.assertEqual(cmd[cmd.index(flag)+1], value)
            self.assertIn("--strict-mcp-config", cmd)
            self.assertNotIn("--bare", cmd)
            self.assertNotIn("--dangerously-skip-permissions", cmd)

    def test_environment_scrubs_api_overrides_without_mutating_parent(self):
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "synthetic-secret",
                                     "ANTHROPIC_BASE_URL": "https://example.invalid",
                                     "CLAUDE_CODE_OAUTH_TOKEN": "synthetic-token"}):
            env = probe.environment()
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            self.assertNotIn("ANTHROPIC_BASE_URL", env)
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)
            self.assertEqual(os.environ["ANTHROPIC_API_KEY"], "synthetic-secret")

    def test_ping_records_complete_payload_and_refuses_repeat(self):
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                   "params": {"name": "ping", "arguments": {}}}
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "ping.jsonl"
            stdin = io.StringIO(json.dumps(request) + "\n" + json.dumps(dict(request, id=2)) + "\n")
            with patch.object(probe.sys, "stdin", stdin), patch.object(probe.sys, "stdout", io.StringIO()):
                probe.ping_server(log)
            records = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertEqual(records[0]["request"], request)
            self.assertFalse(records[0]["response"]["isError"])
            self.assertTrue(records[1]["response"]["isError"])
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    def test_save_exclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            probe.save(path, {"first": True})
            with self.assertRaises(FileExistsError):
                probe.save(path, {"replacement": True})
            self.assertEqual(json.loads(path.read_text()), {"first": True})


if __name__ == "__main__":
    unittest.main()
