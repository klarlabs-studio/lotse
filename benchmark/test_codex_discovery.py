import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from broker_mcp import handle
from codex_discovery import discovery


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'control.jsonl'

    def row(self, arm='graf_current_orient'):
        request = dict(id=7, method='tools/list')
        return dict(request=request, reply=handle(request, SimpleNamespace(arm=arm)))

    def write(self, rows):
        self.path.write_text(''.join(json.dumps(row) + '\n' for row in rows))

    def test_all_arms_validate_only_their_exact_schema(self):
        for arm in ('graf_current_orient', 'graphify_orient', 'search_only'):
            row = self.row(arm)
            row['reply']['result']['tools'].reverse()
            self.write([row, row])
            self.assertTrue(discovery(self.path, arm)['valid'])
            for other in ('graf_current_orient', 'graphify_orient', 'search_only'):
                if other != arm:
                    self.assertFalse(discovery(self.path, other)['valid'])

    def test_missing_empty_malformed_and_failed_captures_fail(self):
        self.assertFalse(discovery(self.path, 'search_only')['valid'])
        for text in ('', 'bad json', 'null', '[]', '{}',
                     json.dumps(dict(request=dict(id=7, method='tools/list'), reply=dict(id=7, error={})))):
            self.path.write_text(text)
            self.assertFalse(discovery(self.path, 'search_only')['valid'])

    def test_every_response_must_match_and_have_valid_envelope(self):
        original = self.row()
        for change in ('id', 'missing', 'duplicate', 'schema', 'pagination', 'malformed_tool'):
            row = copy.deepcopy(original)
            tools = row['reply']['result']['tools']
            if change == 'id':
                row['reply']['id'] = 99
            elif change == 'missing':
                tools.pop()
            elif change == 'duplicate':
                tools.append(tools[0])
            elif change == 'schema':
                tools[0]['inputSchema']['additionalProperties'] = True
            elif change == 'pagination':
                row['reply']['result']['nextCursor'] = 'next'
            else:
                tools[0] = 'invalid'
            self.write([original, row])
            self.assertFalse(discovery(self.path, 'graf_current_orient')['valid'], change)


if __name__ == '__main__':
    unittest.main()
