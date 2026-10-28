import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from request_ledger import Ledger, LedgerError


def body(model="fixture-model", output=40, **extra):
    return json.dumps({"model": model, "max_output_tokens": output, **extra},
                      separators=(",", ":")).encode()


def bound(raw, tokens=10, model="fixture-model"):
    return {"source": "synthetic_fixture", "request_sha256": hashlib.sha256(raw).hexdigest(),
            "model": model, "input_tokens": tokens}


def test_durable_admission_and_reservation_survive_failed_attempt(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path, token_limit=100, attempt_limit=3)
    raw = body()
    first = ledger.admit(raw, bound(raw))
    assert first == 1
    ledger.finish(first, outcome="transport_error")
    retry = ledger.admit(raw, bound(raw))
    assert retry == 2
    state = ledger.summary()
    assert state["reserved_tokens"] == 100
    assert state["unknown_usage_attempts"] == 2
    assert state["pending_attempts"] == 1
    assert state["agent_savings"] == "unmeasured"
    ledger.close()
    events = [json.loads(line) for line in path.read_text().splitlines()]
    assert [e["event"] for e in events] == ["config", "admitted", "finish", "admitted"]
    assert path.stat().st_mode & 0o777 == 0o600


def test_failed_journal_sync_never_returns_permit(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / "journal", 100, 2)
    raw = body()
    monkeypatch.setattr(os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(LedgerError):
        ledger.admit(raw, bound(raw))
    assert ledger.summary()["poisoned"] is True
    with pytest.raises(LedgerError):
        ledger.admit(raw, bound(raw))


def test_concurrent_admissions_obey_attempt_and_token_caps(tmp_path):
    ledger = Ledger(tmp_path / "journal", 1000, 5)
    raw = body(output=100)
    receipt = bound(raw, 100)
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _n: ledger.admit(raw, receipt), range(30)))
    accepted = [r for r in results if r is not None]
    assert len(accepted) == 5
    assert len(set(accepted)) == 5
    assert ledger.summary()["reserved_tokens"] == 1000
    assert ledger.summary()["denied_requests"] == 25


@pytest.mark.parametrize("raw", [
    b'{"model":"x","model":"y","max_output_tokens":20}',
    b'{"model":"x","max_output_tokens":20,"x":NaN}',
    b'[]', b'{bad json',
])
def test_malformed_json_is_durably_denied(tmp_path, raw):
    ledger = Ledger(tmp_path / "journal", 100, 2)
    assert ledger.admit(raw, None) is None
    assert ledger.summary()["denied_requests"] == 1


def test_unknown_or_mismatched_bounds_are_denied(tmp_path):
    ledger = Ledger(tmp_path / "journal", 1000, 4)
    raw = body()
    assert ledger.admit(raw, None) is None
    wrong = bound(raw)
    wrong["request_sha256"] = "0" * 64
    assert ledger.admit(raw, wrong) is None
    wrong = bound(raw)
    wrong["input_tokens"] = True
    assert ledger.admit(raw, wrong) is None
    assert ledger.summary()["reserved_tokens"] == 0


@pytest.mark.parametrize("extra", [
    {"previous_response_id": None}, {"conversation": "c"}, {"background": True},
    {"tools": [{"type": "web_search"}]}, {"truncation": "auto"},
])
def test_unsupported_request_modes_denied(tmp_path, extra):
    ledger = Ledger(tmp_path / "journal", 100, 2)
    raw = body(**extra)
    assert ledger.admit(raw, bound(raw)) is None


def test_invalid_output_usage_poisoned_and_excessive_usage_visible(tmp_path):
    ledger = Ledger(tmp_path / "journal", 100, 2)
    raw = body(output=30)
    attempt = ledger.admit(raw, bound(raw, 20))
    with pytest.raises(LedgerError, match="invalid usage"):
        ledger.finish(attempt, outcome="completed", usage={"input_tokens": 20,
                      "output_tokens": 31, "total_tokens": 51})
    assert ledger.summary()["poisoned"] is True
    assert ledger.summary()["pending_attempts"] == 0
    assert ledger.summary()["unknown_usage_attempts"] == 1
    assert "invalid_usage" in json.loads((tmp_path / "journal").read_text().splitlines()[-1])


@pytest.mark.parametrize("detail_key,detail_value", [
    ("input_tokens_details", {"cached_tokens": 21}),
    ("output_tokens_details", {"reasoning_tokens": 31}),
])
def test_usage_detail_counts_cannot_exceed_parent_totals(tmp_path, detail_key, detail_value):
    ledger = Ledger(tmp_path / "journal", 100, 1)
    raw = body(output=30)
    attempt = ledger.admit(raw, bound(raw, 20))
    usage = {"input_tokens": 20, "output_tokens": 30, "total_tokens": 50,
             detail_key: detail_value}
    with pytest.raises(LedgerError, match="invalid usage"):
        ledger.finish(attempt, outcome="completed", usage=usage)
    assert ledger.summary()["pending_attempts"] == 0
    assert ledger.summary()["unknown_usage_attempts"] == 1
    assert ledger.summary()["poisoned"] is True


def test_failed_fsync_while_finishing_preserves_pending_and_poisons(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / "journal", 100, 1)
    raw = body()
    attempt = ledger.admit(raw, bound(raw))
    monkeypatch.setattr(os, "fsync", lambda _fd: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(LedgerError, match="write/fsync failed"):
        ledger.finish(attempt, outcome="transport_error")
    summary = ledger.summary()
    assert summary["poisoned"] is True
    assert summary["pending_attempts"] == 1
    assert summary["unknown_usage_attempts"] == 1


def test_completed_usage_validation_and_duplicate_finish(tmp_path):
    ledger = Ledger(tmp_path / "journal", 100, 1)
    raw = body(output=30)
    attempt = ledger.admit(raw, bound(raw, 20))
    ledger.finish(attempt, outcome="completed", usage={"input_tokens": 18,
                  "output_tokens": 22, "total_tokens": 40,
                  "input_tokens_details": {"cached_tokens": 2}})
    assert ledger.summary()["actual_observed_tokens"] == 40
    assert ledger.summary()["unknown_usage_attempts"] == 0
    with pytest.raises(LedgerError, match="already finished"):
        ledger.finish(attempt, outcome="completed", usage={"input_tokens": 18,
                      "output_tokens": 22, "total_tokens": 40})


def test_existing_file_is_not_resumed_or_overwritten(tmp_path):
    path = tmp_path / "journal"
    path.write_text("pending\n")
    with pytest.raises(FileExistsError):
        Ledger(path, 100, 2)
    assert path.read_text() == "pending\n"


def test_pending_attempt_is_not_released_when_reopening_is_refused(tmp_path):
    path = tmp_path / "journal"
    ledger = Ledger(path, 100, 2)
    raw = body()
    ledger.admit(raw, bound(raw))
    ledger.close()
    with pytest.raises(FileExistsError):
        Ledger(path, 100, 2)
    assert json.loads(path.read_text().splitlines()[1])["reserved_tokens"] == 50
