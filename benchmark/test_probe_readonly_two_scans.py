"""Factory isolation and validation count controls for the experimental probe."""
import pytest
import probe_readonly_two_scans as probe

class FakeBroker:
    def __init__(self, *args, **kwargs):
        self._verify_inventory()
    def _verify_inventory(self):
        pass
    def handle(self, request):
        self._verify_inventory()
        if request.get('fail'):
            raise LookupError('injected')
        return request

class FakeSession:
    def __init__(self, *args, broker_factory, **kwargs):
        self.broker = broker_factory()
    def handle(self, request):
        return self.broker.handle(request)

@pytest.mark.parametrize('mode', ['v2', 'v3'])
def test_factory_counts_and_restores_on_failure(monkeypatch, mode):
    monkeypatch.setattr(probe, 'Session', FakeSession)
    monkeypatch.setattr(probe, 'V2Broker', FakeBroker)
    monkeypatch.setattr(probe, 'V3Broker', FakeBroker)
    original = probe.lifecycle.Session
    captures = []
    with pytest.raises(LookupError):
        with probe.session_factory(mode, captures):
            session = probe.lifecycle.Session()
            assert session.handle({'id': 1}) == {'id': 1}
            session.handle({'id': 2, 'fail': True})
    assert probe.lifecycle.Session is original
    assert captures == [{'kind': 'init', 'inventory_checks': 1},
                        {'kind': 'request', 'request_id': 1, 'inventory_checks': 1},
                        {'kind': 'request', 'request_id': 2, 'inventory_checks': 1}]

def test_order_and_invalid_mode():
    assert probe.modes(0) == probe.modes(2) == ('v2', 'v3')
    assert probe.modes(1) == ('v3', 'v2')
    original = probe.lifecycle.Session
    with pytest.raises(ValueError, match='unknown_broker_mode'):
        with probe.session_factory('invalid', []):
            pass
    assert probe.lifecycle.Session is original
