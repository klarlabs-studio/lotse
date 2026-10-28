"""Configuration and receipt binding checks for the private-copy launcher."""
import copy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from readonly_broker_stdio_v3 import artifact_pin, authenticate, staged_plan, sha, BASE
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
        staged = staged_plan(self.plan, 'graf_v61', SimpleNamespace(artifact_path=Path('/private/staged/index.bin')))
        self.assertEqual(self.plan, before)
        self.assertEqual(staged['artifacts']['graf_v61']['path'], '/private/staged/index.bin')
        self.assertEqual(staged['artifacts']['graf_v61']['sha256'], 'b' * 64)

    def test_launcher_requires_its_transitive_source_pins(self):
        with self.assertRaisesRegex(ValueError, 'incomplete'):
            authenticate({'sources': {}})
        sources = ['readonly_broker_stdio_v3', 'readonly_snapshot', 'readonly_broker_v2',
                   'readonly_broker', 'agent_backend', 'provider_ledger', 'retrieval']
        plan = {'sources': {f'benchmark/{name}.py': sha(BASE / f'benchmark/{name}.py') for name in sources}}
        authenticate(plan)
        plan['sources']['benchmark/readonly_snapshot.py'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'mismatch'):
            authenticate(plan)

    def test_launcher_rejects_traversal_even_when_other_pins_are_complete(self):
        sources = ['readonly_broker_stdio_v3', 'readonly_snapshot', 'readonly_broker_v2',
                   'readonly_broker', 'agent_backend', 'provider_ledger', 'retrieval']
        plan = {'sources': {f'benchmark/{name}.py': sha(BASE / f'benchmark/{name}.py') for name in sources}}
        plan['sources']['benchmark/../README.md'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'invalid'):
            authenticate(plan)


if __name__ == '__main__':
    unittest.main()
