import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from probe_codex_otel import ping_server


class SyntheticPingTests(unittest.TestCase):
    def test_tool_has_fixed_payload_and_cannot_dispatch_other_operations(self):
        messages = [
            {'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-03-26'}},
            {'method': 'notifications/initialized'},
            {'id': 2, 'method': 'tools/list'},
            {'id': 3, 'method': 'tools/call', 'params': {'name': 'ping', 'arguments': {}}},
            {'id': 4, 'method': 'tools/call', 'params': {'name': 'read', 'arguments': {'path': '/etc/passwd'}}},
        ]
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'ping.jsonl'
            with patch('sys.stdin', io.StringIO(''.join(json.dumps(m) + '\n' for m in messages))), patch('sys.stdout', output):
                ping_server(log)
            records = [json.loads(line) for line in log.read_text().splitlines()]
        replies = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([r['id'] for r in replies], [1, 2, 3, 4])
        self.assertEqual(replies[0]['result']['protocolVersion'], '2025-03-26')
        self.assertEqual([t['name'] for t in replies[1]['result']['tools']], ['ping'])
        self.assertEqual(replies[2]['result']['content'], [{'type': 'text', 'text': 'pong'}])
        self.assertFalse(replies[2]['result']['isError'])
        self.assertTrue(replies[3]['result']['isError'])
        self.assertEqual([r['reply'] for r in records], replies)


if __name__ == '__main__':
    unittest.main()
