"""Integrity and order controls for the lifecycle diagnostic."""
from pathlib import Path
import json
from probe_readonly_session import pair_checks, orders, arm_order


def test_competitors_and_lifecycle_modes_are_counterbalanced():
    assert orders(0) == orders(2) == ('fresh', 'reused')
    assert orders(1) == ('reused', 'fresh')
    assert len(set(arm_order(i) for i in range(6))) == 6
    assert [arm_order(i) for i in range(6)] == [arm_order(i) for i in range(6, 12)]


def test_source_corruption_cannot_pass_on_success_flag_alone(monkeypatch):
    import probe_readonly_session as probe
    monkeypatch.setattr(probe, 'tokens', lambda _text: [])
    native = {'stdout': 'native\n', 'stderr': '', 'returncode': 0}
    retrieve = {'result': {'isError': False, 'diagnostic_tokens': 0,
                           'content': [{'type': 'text', 'text': json.dumps(native)}]}}
    read = {'result': {'isError': False, 'source': {'text': '1: bytes', 'sha256': 'frozen'}}}
    def checks():
        return pair_checks(retrieve, read, native, 'graf_evidence_selective', Path('/old'), Path('/new'),
                           {'a.py': 'frozen'}, 'a.py', '1: bytes')
    assert all(checks().values())
    read['result']['source']['sha256'] = 'corrupt'
    assert not all(checks().values())
    read['result']['source']['sha256'] = 'frozen'
    read['result']['source']['text'] = '1: corrupt'
    assert not all(checks().values())
    read['result']['source']['text'] = '1: bytes'
    retrieve['result']['isError'] = True
    assert not all(checks().values())
