import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from readonly_broker_v2 import source_manifest_sha256
from readonly_session import Session
from agent_backend import QUERY_ARGS
import readonly_broker


def sha(data):
    return hashlib.sha256(data).hexdigest()


class FakeSnapshot:
    made = 0
    def __init__(self, *args, **kwargs):
        type(self).made += 1
        self.root = Path('/private/session-root')
        self.graph_path = Path('/private/session-graph')
        self.artifact_path = Path('/private/session-artifact')
        self.receipt = {'artifact_sha256': 'a' * 64, 'phase': 'ready'}
        self.entered = self.cleaned = 0
    def __enter__(self):
        self.entered += 1
        return self
    def __exit__(self, *_):
        self.cleaned += 1


class FakeBroker:
    made = 0
    def __init__(self, plan, arm, root, graph, journal, *, max_actions, runner):
        type(self).made += 1
        self.plan, self.max_actions = plan, max_actions
        self.closed = 0
        self.calls = []
    def handle(self, request):
        self.calls.append(request)
        return {'ok': True}
    def close(self):
        self.closed += 1


class ReadonlySessionTests(unittest.TestCase):
    def setUp(self):
        token_patch = patch.object(readonly_broker, 'tokens', lambda _text: [])
        token_patch.start()
        self.addCleanup(token_patch.stop)
        FakeSnapshot.made = FakeBroker.made = 0
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name).resolve()
        self.root = base / 'repo'
        self.root.mkdir()
        self.graph = base / 'graph/graphify-out/graph.json'
        self.graph.parent.mkdir(parents=True)
        self.graph.write_bytes(b'graph')
        manifest = {'src.py': sha(b'source')}
        self.plan = {
            'source_manifest': manifest,
            'artifacts': {'graf_test': {'path': str(self.root / '.graf/index.bin'),
                'sha256': sha(b'original'),
                'source_manifest_sha256': source_manifest_sha256(manifest)}},
            'snapshot_index_build': {'binary': '/bin/index', 'binary_sha256': 'b' * 64,
                                     'semantics_sha256': 'c' * 64},
        }

    def session(self, max_actions=4, **kwargs):
        return Session(self.plan, 'graf_test', self.root, self.graph, '/tmp/journal',
                       '/tmp/snapshots', max_actions, snapshot_factory=FakeSnapshot,
                       broker_factory=FakeBroker, **kwargs)

    def test_one_snapshot_and_broker_are_reused_for_multiple_requests(self):
        original = copy.deepcopy(self.plan)
        session = self.session(7)
        with session as active:
            broker = active.broker
            self.assertIs(active, session)
            self.assertEqual(active.root, Path('/private/session-root'))
            self.assertEqual(active.graph_path, Path('/private/session-graph'))
            self.assertEqual(active.handle({'id': 1}), {'ok': True})
            self.assertEqual(active.handle({'id': 2}), {'ok': True})
            self.assertEqual(active.broker.max_actions, 7)
        self.assertEqual(FakeSnapshot.made, 1)
        self.assertEqual(FakeBroker.made, 1)
        self.assertEqual(len(broker.calls), 2)
        self.assertEqual(self.plan, original)
        self.assertEqual(session.receipt['phase'], 'ready')
        self.assertFalse(session.active)
        for timing in session.timings.values():
            self.assertIsNotNone(timing)

    def test_max_actions_bounds_and_rejects_bool(self):
        for value in (True, False, 0, -1, 33, 1.0, '2'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.session(value)

    def test_caller_plan_is_deep_copied_before_broker_staging(self):
        session = self.session()
        session.plan['source_manifest']['src.py'] = 'd' * 64
        self.assertEqual(self.plan['source_manifest']['src.py'], sha(b'source'))

    def test_close_is_idempotent_and_requests_after_close_fail_closed(self):
        session = self.session()
        session.__enter__()
        broker, snapshot = session.broker, session.snapshot
        session.close()
        session.close()
        self.assertEqual(broker.closed, 1)
        self.assertEqual(snapshot.cleaned, 1)
        with self.assertRaisesRegex(RuntimeError, 'not open'):
            session.handle({'id': 1})
        with self.assertRaisesRegex(RuntimeError, 'more than once'):
            session.__enter__()

    def test_body_exception_still_closes_and_preserves_receipt(self):
        session = self.session()
        with self.assertRaisesRegex(RuntimeError, 'body'):
            with session:
                raise RuntimeError('body')
        self.assertEqual(session.receipt['phase'], 'ready')
        self.assertEqual(session.snapshot.cleaned, 1)

    def test_broker_startup_failure_cleans_snapshot(self):
        def broken_broker(*args, **kwargs):
            raise RuntimeError('broker startup')
        session = Session(self.plan, 'graf_test', self.root, self.graph,
            '/tmp/journal', '/tmp/snapshots', 4, snapshot_factory=FakeSnapshot,
            broker_factory=broken_broker)
        with self.assertRaisesRegex(RuntimeError, 'startup'):
            session.__enter__()
        self.assertEqual(session.snapshot.cleaned, 1)
        self.assertIsNotNone(session.receipt)
        with self.assertRaisesRegex(RuntimeError, 'not open'):
            session.handle({'id': 1})

    def test_snapshot_enter_failure_preserves_receipt_and_fails_closed(self):
        class EnterFailure(FakeSnapshot):
            def __enter__(self):
                self.receipt['phase'] = 'failed_after_receipt'
                raise RuntimeError('snapshot enter')
        session = Session(self.plan, 'graf_test', self.root, self.graph,
            '/tmp/journal', '/tmp/snapshots', 4, snapshot_factory=EnterFailure,
            broker_factory=FakeBroker)
        with self.assertRaisesRegex(RuntimeError, 'snapshot enter'):
            session.__enter__()
        self.assertEqual(session.receipt['phase'], 'failed_after_receipt')
        self.assertFalse(session.active)
        with self.assertRaisesRegex(RuntimeError, 'not open'):
            session.handle({'id': 1})

    def test_cleanup_failure_still_attempts_snapshot_cleanup_and_keeps_receipt(self):
        class ClosingFailure(FakeBroker):
            def close(self):
                self.closed += 1
                raise OSError('broker close')
        session = Session(self.plan, 'graf_test', self.root, self.graph,
            '/tmp/journal', '/tmp/snapshots', 4, snapshot_factory=FakeSnapshot,
            broker_factory=ClosingFailure)
        session.__enter__()
        with self.assertRaisesRegex(OSError, 'broker close'):
            session.close()
        self.assertEqual(session.snapshot.cleaned, 1)
        self.assertEqual(session.receipt['phase'], 'ready')
        self.assertIsNotNone(session.timings['snapshot_cleanup_wall_ms'])

    def make_real_graphify_session(self, base, max_actions=4, runner=None):
        base = Path(base).resolve()
        root = base / 'repo'
        root.mkdir()
        source = root / 'src.py'
        source.write_text('original source\n', encoding='utf8')
        graph = base / 'graph/graphify-out/graph.json'
        graph.parent.mkdir(parents=True)
        graph.write_bytes(b'graph artifact')
        binary = base / 'backend'
        binary.write_bytes(b'backend')
        manifest = {'src.py': sha(source.read_bytes())}
        plan = {
            'comparison': {'shared_output_budget': 100},
            'subprocess_environment': {'PYTHONHASHSEED': '0'},
            'arms': [{'id': 'graphify', 'binary': str(binary),
                'binary_sha256': sha(binary.read_bytes()), 'query_args': QUERY_ARGS['graphify']}],
            'source_manifest': manifest,
            'artifacts': {'graphify': {'path': str(graph), 'sha256': sha(graph.read_bytes()),
                'source_manifest_sha256': source_manifest_sha256(manifest)}},
        }
        session = Session(plan, 'graphify', root, graph, base / 'journal',
            base / 'snapshots', max_actions, runner=runner or (
                lambda argv, **kwargs: SimpleNamespace(returncode=0, stdout='result', stderr='')))
        return session

    @staticmethod
    def retrieve(session, ident):
        return session.handle({'jsonrpc': '2.0', 'id': ident, 'method': 'tools/call',
            'params': {'name': 'retrieve', 'arguments': {'query': 'find source', 'budget': 64}}})

    def test_real_snapshot_and_broker_enforce_action_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            calls = []
            def runner(argv, **kwargs):
                calls.append(argv)
                return SimpleNamespace(returncode=0, stdout='result', stderr='')
            session = self.make_real_graphify_session(Path(temp), max_actions=2, runner=runner)
            with session:
                self.assertFalse(self.retrieve(session, 1)['result']['isError'])
                self.assertFalse(self.retrieve(session, 2)['result']['isError'])
                self.assertTrue(self.retrieve(session, 3)['result']['isError'])
                self.assertEqual(len(calls), 2)

    def test_real_broker_detects_source_and_artifact_mutations_between_calls(self):
        for mutate in ('source', 'artifact'):
            with self.subTest(mutate=mutate), tempfile.TemporaryDirectory() as temp:
                calls = []
                def runner(argv, **kwargs):
                    calls.append(argv)
                    return SimpleNamespace(returncode=0, stdout='result', stderr='')
                session = self.make_real_graphify_session(Path(temp), runner=runner)
                with session as active:
                    self.assertFalse(self.retrieve(active, 1)['result']['isError'])
                    target = active.root / 'src.py' if mutate == 'source' else active.graph_path
                    target.parent.chmod(0o700)
                    target.chmod(0o600)
                    target.write_bytes(b'mutated after retrieval')
                    second = self.retrieve(active, 2)
                    self.assertTrue(second['result']['isError'])
                    self.assertEqual(len(calls), 1)

    def test_bad_artifact_pin_fails_closed(self):
        session = self.session()
        session.plan['artifacts']['graf_test']['path'] = '/wrong/path'
        with self.assertRaisesRegex(ValueError, 'artifact_configuration_mismatch'):
            session.__enter__()
        self.assertFalse(session.active)
        with self.assertRaisesRegex(RuntimeError, 'not open'):
            session.handle({'id': 1})


if __name__ == '__main__':
    unittest.main()
