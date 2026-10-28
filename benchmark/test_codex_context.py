import unittest
import hashlib
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

import audit_codex_context
from broker_mcp import handle
from types import SimpleNamespace

from audit_codex_context import source_inventory


class ContextInventoryTests(unittest.TestCase):
    def event(self, operation, content, source_hash='same', ok=True):
        return dict(operation=operation, response=dict(ok=ok,
            result=dict(content=content, path='app.py', source_hash=source_hash)))

    def test_counts_delivered_lines_not_requested_ranges_and_tracks_versions(self):
        result = source_inventory([
            self.event('read', 'L1: a\nL2: b\n'), self.event('read', 'L2: b\nL3: c\n'),
            self.event('read', 'L2: b\n', source_hash='changed'),
            self.event('read', 'L2: b\n', ok=False)])
        self.assertEqual(result['delivered_read_lines'], 5)
        self.assertEqual(result['repeated_delivered_read_lines'], 1)

    def test_duplicate_retrieval_requires_exact_delivered_content(self):
        result = source_inventory([
            self.event('retrieve', 'target code'), self.event('retrieve', 'target code'),
            self.event('retrieve', 'different code'), self.event('search', 'target code')])
        self.assertEqual(result['exact_duplicate_retrieval_replies'], 1)

    def test_repeated_unit_header_is_distinct_from_duplicate_reply(self):
        result = source_inventory([
            self.event('retrieve', 'src/app.py:1-3\ncode\n'),
            self.event('retrieve', 'src/app.py:1-3\nchanged or differently bounded code\n')])
        self.assertEqual(result['exact_duplicate_retrieval_replies'], 0)
        self.assertEqual(result['repeated_retrieved_unit_headers'], 1)


class ContextAuditSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(audit_codex_context, 'BASE', self.root).start()
        self.directory = self.root / '.research/trials/example'
        call = self.directory / 'call-001'
        call.mkdir(parents=True)
        for name in ('prompt.txt', 'instructions.txt', 'stream.jsonl'):
            (call / name).write_text('' if name.endswith('jsonl') else 'instructions')
        for name in ('tool-events.jsonl', 'mcp-requests.jsonl'):
            (self.directory / name).write_text('')
        self.report = self.root / 'report.json'
        self.report.write_text(json.dumps(dict(cases=[dict(trial='example', arm='search_only',
            task='task', attempt=1, passed=False, agent_called_finish=False, audit=dict(total_tokens=10))])))
        self.spec = self.root / 'spec.json'
        self.spec.write_text(json.dumps(dict(id='example', implementation_sha256={})))
        self.control = self.directory / 'mcp-requests.jsonl.control.jsonl'

    def test_actual_capture_needs_no_current_schema_files_and_keeps_unknown_null(self):
        request = dict(id=7, method='tools/list')
        reply = handle(request, SimpleNamespace(arm='search_only'))
        reply['result']['tools'][0]['description'] = 'historically different description'
        self.control.write_text(json.dumps(dict(request=request, reply=reply)) + '\n')
        result = audit_codex_context.audit(self.report, self.spec)
        case = result['cases'][0]
        self.assertEqual(case['schema_source'], 'captured_broker_reply')
        self.assertIsNone(case['tool_names_reconstructed'])
        self.assertEqual(case['captured_tool_lists'], [reply['result']['tools']])
        inventory = result['totals']['visible_cl100k_inventory']
        self.assertIsNone(inventory['tool_definitions_reconstructed_canonical_json'])
        self.assertGreater(inventory['tool_definitions_captured_canonical_json'], 0)
        self.assertIn('.research/trials/example/mcp-requests.jsonl.control.jsonl', result['source_artifact_sha256'])

    def test_invalid_capture_never_falls_back_to_reconstruction(self):
        self.control.write_text('malformed')
        with self.assertRaisesRegex(ValueError, 'Invalid captured discovery'):
            audit_codex_context.audit(self.report, self.spec)

    def test_missing_capture_reconstructs_only_when_dependencies_match_freeze(self):
        hashes = {}
        for name in ('benchmark/broker_mcp.py', 'benchmark/task_session.py'):
            path = self.root / name
            path.parent.mkdir(exist_ok=True)
            path.write_text('frozen')
            hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.spec.write_text(json.dumps(dict(id='example', implementation_sha256=hashes)))
        case = audit_codex_context.audit(self.report, self.spec)['cases'][0]
        self.assertEqual(case['schema_source'], 'frozen_reconstruction')
        self.assertFalse(case['actual_tools_list_captured'])
        self.assertIsNone(case['visible_cl100k_inventory']['tool_definitions_captured_canonical_json'])
        (self.root / 'benchmark/task_session.py').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'differs from freeze'):
            audit_codex_context.audit(self.report, self.spec)


if __name__ == '__main__':
    unittest.main()
