"""Configuration and receipt binding checks for the private-copy launcher."""
import copy
import json
from unittest.mock import patch
from readonly_broker_stdio_v4 import main
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from readonly_broker_stdio_v4 import artifact_pin, authenticate, staged_plan, sha, BASE
from readonly_broker_v2 import source_manifest_sha256


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.manifest = {'main.py': 'a' * 64}
        self.plan = {'source_manifest': self.manifest, 'artifacts': {'graf_v61': {
            'path': str(self.root / '.graf/index.bin'), 'sha256': 'b' * 64,
            'source_manifest_sha256': source_manifest_sha256(self.manifest)}}}

    def test_pin_is_bound_to_selected_root_and_manifest(self):
        self.assertEqual(artifact_pin(self.plan, 'graf_v61', self.root, self.root / 'graph.json'),
                         self.plan['artifacts']['graf_v61'])
        changed = copy.deepcopy(self.plan)
        changed['source_manifest']['main.py'] = 'c' * 64
        with self.assertRaisesRegex(ValueError, 'binding'):
            artifact_pin(changed, 'graf_v61', self.root, self.root / 'graph.json')
        with self.assertRaisesRegex(ValueError, 'configuration'):
            artifact_pin(self.plan, 'graf_v61', self.root.parent, self.root / 'graph.json')

    def test_staging_changes_only_copied_configuration(self):
        before = copy.deepcopy(self.plan)
        staged = staged_plan(self.plan, 'graf_v61', SimpleNamespace(artifact_path=Path('/private/staged/index.bin'), receipt={'artifact_sha256': 'c' * 64}))
        self.assertEqual(self.plan, before)
        self.assertEqual(staged['artifacts']['graf_v61']['path'], '/private/staged/index.bin')
        self.assertEqual(staged['artifacts']['graf_v61']['sha256'], 'c' * 64)

    def test_launcher_requires_its_transitive_source_pins(self):
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            authenticate({'sources': {}})
        sources = ['readonly_broker_stdio_v4', 'readonly_snapshot', 'readonly_snapshot_v2', 'readonly_broker_v2',
                   'readonly_broker', 'agent_backend', 'provider_ledger', 'retrieval']
        plan = {'sources': {f'benchmark/{name}.py': sha(BASE / f'benchmark/{name}.py') for name in sources}}
        authenticate(plan)
        plan['sources']['benchmark/readonly_snapshot.py'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            authenticate(plan)

    def test_launcher_rejects_traversal_even_when_other_pins_are_complete(self):
        sources = ['readonly_broker_stdio_v4', 'readonly_snapshot', 'readonly_snapshot_v2', 'readonly_broker_v2',
                   'readonly_broker', 'agent_backend', 'provider_ledger', 'retrieval']
        plan = {'sources': {f'benchmark/{name}.py': sha(BASE / f'benchmark/{name}.py') for name in sources}}
        plan['sources']['benchmark/../README.md'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'invalid'):
            authenticate(plan)

    def test_failed_broker_startup_cleans_snapshot_and_keeps_private_ready_receipt(self):
        source = self.root / 'main.py'
        source.write_bytes(b'frozen source\n')
        graph = self.root.parent / (self.root.name + '-graph')
        # Graphify's source root is inside a dedicated corpus container.
        corpus = self.root / 'corpus'
        live = corpus / 'input'
        live.mkdir(parents=True)
        (live / 'main.py').write_bytes(source.read_bytes())
        graph = corpus / 'graph/graphify-out/graph.json'
        graph.parent.mkdir(parents=True)
        graph.write_bytes(b'{}')
        manifest = {'main.py': sha(source)}
        names = ['readonly_broker_stdio_v4', 'readonly_snapshot', 'readonly_snapshot_v2',
                 'readonly_broker_v2', 'readonly_broker', 'agent_backend', 'provider_ledger', 'retrieval']
        plan = {'source_manifest': manifest, 'sources': {f'benchmark/{n}.py': sha(BASE / f'benchmark/{n}.py') for n in names},
                'arms': [], 'artifacts': {'graphify': {'path': str(graph), 'sha256': sha(graph),
                'source_manifest_sha256': source_manifest_sha256(manifest)}}}
        config = self.root / 'plan.json'
        config.write_text(json.dumps(plan))
        receipt = self.root / 'receipt.json'
        with patch('readonly_broker_stdio_v4.Broker', side_effect=ValueError('startup failure')):
            with self.assertRaisesRegex(ValueError, 'startup failure'):
                main(['--plan', str(config), '--arm', 'graphify', '--root', str(live),
                      '--graph', str(graph), '--journal', str(self.root / 'journal.jsonl'),
                      '--snapshot-parent', str(self.root), '--receipt', str(receipt)])
        saved = json.loads(receipt.read_text())
        self.assertEqual(saved['phase'], 'snapshot_ready')
        self.assertEqual(receipt.stat().st_mode & 0o777, 0o600)
        self.assertFalse(Path(saved['snapshot_root']).exists())
        self.assertFalse((self.root / 'journal.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
