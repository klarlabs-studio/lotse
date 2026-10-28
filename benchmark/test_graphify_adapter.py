"""Mechanical CLI parity diagnostics, not retrieval/agent efficiency evidence."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

from compare_graphify import BASE, graphify_command, sha, validate_adapter_freeze
from audit_graphify_adapter import probe




class AdapterTests(unittest.TestCase):
    # CI installs no Graphify (it runs no comparison); the pinned virtualenvs do.
    @unittest.skipUnless(importlib.util.find_spec("graphify"), "Graphify is not installed")
    def test_native_cli_fixtures(self):
        with tempfile.TemporaryDirectory() as directory:
            for row in probe(directory):
                with self.subTest(row=row['id']):
                    self.assertTrue(row['parity_pass'], row)
                    self.assertTrue(row['semantic_pass'], row)

    def test_legacy_default_and_invalid_adapter(self):
        self.assertIn('--graphify-query', graphify_command(Path('graph.json'), 'callee', 2000))
        with self.assertRaises(ValueError):
            graphify_command(Path('graph.json'), 'callee', 2000, 'unknown')

    def test_new_comparison_requires_new_freeze(self):
        validate_adapter_freeze({}, 'library-v1')
        with self.assertRaises(ValueError):
            validate_adapter_freeze({'version': 37}, 'native-cli-v2')
        frozen = {'id': 'native-cli-v2', 'driver_sha256': sha(BASE / 'benchmark/compare_graphify.py'),
                  'manifest_sha256': sha(BASE / 'benchmark/graphify-adapters.json')}
        validate_adapter_freeze({'graphify_adapter': frozen}, 'native-cli-v2')
        frozen['driver_sha256'] = 'stale'
        with self.assertRaises(ValueError):
            validate_adapter_freeze({'graphify_adapter': frozen}, 'native-cli-v2')


if __name__ == '__main__':
    unittest.main()
