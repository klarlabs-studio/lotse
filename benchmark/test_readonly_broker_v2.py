import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent_backend import QUERY_ARGS
from readonly_broker_v2 import Broker, source_manifest_sha256
import readonly_broker

BASE = Path(__file__).resolve().parents[1]


def sha(data):
    return hashlib.sha256(data).hexdigest()


class BrokerV2Tests(unittest.TestCase):
    def setUp(self):
        self.token_patch = patch.object(readonly_broker, 'tokens', lambda _text: [])
        self.token_patch.start()
        self.addCleanup(self.token_patch.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / 'repo'
        self.root.mkdir()
        self.root = self.root.resolve()
        self.source = self.root / 'src.py'
        self.source.write_text('hello\n', encoding='utf8')
        self.artifact = self.root / '.graf' / 'index.bin'
        self.artifact.parent.mkdir()
        self.artifact.write_bytes(b'index')
        self.binary = self.root / 'backend'
        self.binary.write_bytes(b'binary')
        self.graph = self.base / 'graph.json'
        self.graph.write_bytes(b'graph')
        self.graph = self.graph.resolve()
        self.plan = self.make_plan('graf_v61')
        self.calls = []

    def make_plan(self, arm_id):
        artifact = self.graph if arm_id == 'graphify' else self.artifact
        return {
            'comparison': {'shared_output_budget': 64},
            'subprocess_environment': {'PYTHONHASHSEED': '0'},
            'arms': [{'id': arm_id, 'binary': str(self.binary),
                      'binary_sha256': sha(b'binary'), 'query_args': QUERY_ARGS[arm_id]}],
            'source_manifest': {'src.py': sha(self.source.read_bytes())},
            'artifacts': {arm_id: {'path': str(artifact), 'sha256': sha(artifact.read_bytes()),
                                   'source_manifest_sha256': source_manifest_sha256({'src.py': sha(self.source.read_bytes())})}}
        }

    def make_broker(self, runner=None, plan=None, arm='graf_v61'):
        p = plan or (self.plan if arm == 'graf_v61' else self.make_plan(arm))
        journal = self.base / ('journal-' + str(len(list(self.base.glob('journal-*')))))
        return Broker(p, arm, self.root, self.graph, journal, runner=runner or self.runner)

    def runner(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout='ok\n', stderr='')

    @staticmethod
    def call(broker):
        return broker.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
                              'params': {'name': 'retrieve', 'arguments': {'query': 'hello'}}})

    def test_stale_artifact_before_init_does_not_open_journal(self):
        self.artifact.write_bytes(b'stale')
        journal = self.base / 'never-created'
        with self.assertRaisesRegex(ValueError, 'artifact'):
            Broker(self.plan, 'graf_v61', self.root, self.graph, journal)
        self.assertFalse(journal.exists())

    def test_stale_artifact_after_init_refuses_subprocess(self):
        broker = self.make_broker()
        self.artifact.unlink()
        reply = self.call(broker)
        self.assertTrue(reply['result']['isError'])
        self.assertEqual(self.calls, [])

    def test_missing_symlink_and_wrong_artifact_path_refused(self):
        broken = self.make_plan('graf_v61')
        broken['artifacts']['graf_v61']['path'] = str(self.base / 'missing')
        with self.assertRaisesRegex(ValueError, 'path'):
            self.make_broker(plan=broken)
        outside = self.base / 'outside'
        outside.write_bytes(b'index')
        self.artifact.unlink()
        self.artifact.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.make_broker()

    def test_caller_mutation_is_isolated(self):
        plan = self.plan
        broker = self.make_broker(plan=plan)
        plan['arms'][0]['binary'] = '/does/not/exist'
        plan['source_manifest']['src.py'] = '0' * 64
        reply = self.call(broker)
        self.assertFalse(reply['result']['isError'])
        self.assertEqual(self.calls[0][0][0], str(self.binary))

    def test_source_binding_mismatch_and_unknown_environment_refused(self):
        p = self.make_plan('graf_v61')
        p['artifacts']['graf_v61']['source_manifest_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'binding'):
            self.make_broker(plan=p)
        p = self.make_plan('graf_v61')
        p['subprocess_environment']['PATH'] = '/tmp'
        with self.assertRaisesRegex(ValueError, 'subprocess_environment'):
            self.make_broker(plan=p)

    def test_env_zero_overrides_inherited_seed_without_parent_mutation(self):
        broker = self.make_broker()
        old = os.environ.get('PYTHONHASHSEED')
        os.environ['PYTHONHASHSEED'] = '731'
        try:
            self.call(broker)
            self.assertEqual(self.calls[0][1]['env']['PYTHONHASHSEED'], '0')
            self.assertEqual(os.environ['PYTHONHASHSEED'], '731')
        finally:
            if old is None:
                os.environ.pop('PYTHONHASHSEED', None)
            else:
                os.environ['PYTHONHASHSEED'] = old

    def test_binary_mutation_refused_before_dispatch(self):
        broker = self.make_broker()
        self.binary.write_bytes(b'changed')
        self.assertTrue(self.call(broker)['result']['isError'])
        self.assertEqual(self.calls, [])

    def test_post_run_mutations_preserve_native_output_and_latch(self):
        def mutate(argv, **kwargs):
            self.calls.append((argv, kwargs))
            self.source.write_text('changed\n')
            return SimpleNamespace(returncode=17, stdout='partial out', stderr='partial err')
        broker = self.make_broker(runner=mutate)
        first = self.call(broker)
        data = json.loads(first['result']['content'][0]['text'])
        self.assertEqual(data['backend_failure']['stdout'], 'partial out')
        self.assertEqual(data['backend_failure']['stderr'], 'partial err')
        self.assertEqual(data['backend_failure']['returncode'], 17)
        self.assertTrue(self.call(broker)['result']['isError'])
        self.assertEqual(len(self.calls), 1)

    def test_post_run_artifact_mutation_preserves_output_and_latches(self):
        def mutate(argv, **kwargs):
            self.artifact.write_bytes(b'changed index')
            return SimpleNamespace(returncode=0, stdout='native output', stderr='')
        broker = self.make_broker(runner=mutate)
        first = self.call(broker)
        data = json.loads(first['result']['content'][0]['text'])
        self.assertEqual(data['backend_failure']['stdout'], 'native output')
        self.assertEqual(data['backend_failure']['returncode'], 0)
        self.assertTrue(self.call(broker)['result']['isError'])

    def test_graphify_native_no_match_then_success_and_malformed_exit_four(self):
        outcomes = [SimpleNamespace(returncode=4, stdout='No matching nodes found.\n', stderr=''),
                    SimpleNamespace(returncode=0, stdout='match\n', stderr='')]
        def run(*args, **kwargs):
            self.calls.append(args)
            return outcomes.pop(0)
        broker = self.make_broker(runner=run, arm='graphify')
        first = self.call(broker)['result']
        self.assertFalse(first['isError'])
        self.assertEqual(first['outcome'], 'no_match')
        self.assertFalse(self.call(broker)['result']['isError'])
        malformed = self.make_broker(runner=lambda *a, **k: SimpleNamespace(returncode=4, stdout='oops', stderr=''), arm='graphify')
        self.assertTrue(self.call(malformed)['result']['isError'])
        self.assertTrue(self.call(malformed)['result']['isError'])
        warning = self.make_broker(runner=lambda *a, **k: SimpleNamespace(
            returncode=4, stdout='No matching nodes found.\n', stderr='warning\n'), arm='graphify')
        self.assertTrue(self.call(warning)['result']['isError'])
        self.assertTrue(self.call(warning)['result']['isError'])

    def test_stdio_launcher_accepts_pins_and_serves_over_subprocess(self):
        plan = self.make_plan('graf_v61')
        plan['sources'] = {}
        for name in ('benchmark/readonly_broker_stdio_v2.py', 'benchmark/readonly_broker_v2.py',
                     'benchmark/readonly_broker.py', 'benchmark/agent_backend.py',
                     'benchmark/provider_ledger.py', 'benchmark/retrieval.py'):
            plan['sources'][name] = sha((BASE / name).read_bytes())
        path = self.base / 'plan.json'
        path.write_text(json.dumps(plan), encoding='utf8')
        proc = subprocess.run([sys.executable, str(BASE / 'benchmark/readonly_broker_stdio_v2.py'),
                               '--plan', str(path), '--arm', 'graf_v61', '--root', str(self.root),
                               '--graph', str(self.graph), '--journal', str(self.base / 'stdio-journal')],
                              input=b'', capture_output=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr.decode())

    def test_stdio_launcher_refuses_missing_or_changed_source_pin_before_journal(self):
        plan = self.make_plan('graf_v61')
        plan['sources'] = {}
        required = ('benchmark/readonly_broker_stdio_v2.py', 'benchmark/readonly_broker_v2.py',
                    'benchmark/readonly_broker.py', 'benchmark/agent_backend.py',
                    'benchmark/provider_ledger.py', 'benchmark/retrieval.py')
        for name in required:
            plan['sources'][name] = sha((BASE / name).read_bytes())
        plan['sources'].pop('benchmark/retrieval.py')
        missing_plan = self.base / 'missing-plan.json'
        missing_plan.write_text(json.dumps(plan), encoding='utf8')
        missing_journal = self.base / 'missing-journal'
        command = [sys.executable, str(BASE / 'benchmark/readonly_broker_stdio_v2.py'),
                   '--plan', str(missing_plan), '--arm', 'graf_v61', '--root', str(self.root),
                   '--graph', str(self.graph)]
        missing = subprocess.run(command + ['--journal', str(missing_journal)], input=b'',
                                  capture_output=True, timeout=10)
        self.assertEqual(missing.returncode, 1)
        self.assertFalse(missing_journal.exists())

        plan['sources']['benchmark/retrieval.py'] = '0' * 64
        changed_plan = self.base / 'changed-plan.json'
        changed_plan.write_text(json.dumps(plan), encoding='utf8')
        changed_journal = self.base / 'changed-journal'
        changed = subprocess.run([*command[:3], str(changed_plan), *command[4:],
                                  '--journal', str(changed_journal)], input=b'',
                                 capture_output=True, timeout=10)
        self.assertEqual(changed.returncode, 1)
        self.assertFalse(changed_journal.exists())


if __name__ == '__main__':
    unittest.main()
