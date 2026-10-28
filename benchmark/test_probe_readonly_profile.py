"""Controls for the diagnostic's profiling boundary and constructor restoration."""
import pytest
import probe_readonly_profile as probe
import readonly_broker as base


class FakeSession:
    def __init__(self, *args, runner, **kwargs):
        self.runner = runner

    def handle(self, request):
        if request.get('fail'):
            raise LookupError('injected failure')
        base._json(request)
        return self.runner(request)


def test_profiled_request_preserves_result_and_counts_child(monkeypatch):
    monkeypatch.setattr(probe, 'Session', FakeSession)
    original = probe.lifecycle.Session
    captures = []
    child_calls = []
    def runner(request):
        child_calls.append(request)
        return {'result': request}
    with probe.session_factory('profiled', captures):
        session = probe.lifecycle.Session(runner=runner)
        result = session.handle({'id': 7})
    assert result == {'result': {'id': 7}}
    assert child_calls == [{'id': 7}]
    assert probe.lifecycle.Session is original
    assert len(captures) == 1 and captures[0]['request_id'] == 7
    categories = captures[0]['profile']['categories']
    assert categories['native_child']['count'] == 1
    assert categories['serialization']['count'] == 1


def test_failure_retains_profile_and_restores_constructor(monkeypatch):
    monkeypatch.setattr(probe, 'Session', FakeSession)
    original = probe.lifecycle.Session
    original_json = base._json
    captures = []
    with pytest.raises(LookupError, match='injected failure'):
        with probe.session_factory('profiled', captures):
            probe.lifecycle.Session(runner=lambda _: None).handle({'id': 8, 'fail': True})
    assert captures[0]['request_id'] == 8
    assert probe.lifecycle.Session is original and base._json is original_json


def test_plain_mode_does_not_patch_broker_or_capture(monkeypatch):
    monkeypatch.setattr(probe, 'Session', FakeSession)
    original = base._json
    captures = []
    with probe.session_factory('plain', captures):
        session = probe.lifecycle.Session(runner=lambda r: r)
        assert session.handle({'id': 9}) == {'id': 9}
        assert base._json is original
    assert captures == []


def test_mode_order_balances_over_blocks():
    assert probe.modes(0) == probe.modes(2) == ('plain', 'profiled')
    assert probe.modes(1) == ('profiled', 'plain')
