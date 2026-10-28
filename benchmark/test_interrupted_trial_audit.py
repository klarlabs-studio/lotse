import json
import shutil
import sys
from pathlib import Path

import pytest

from interrupted_trial_audit import audit
from probe_supervised_agent import probe


@pytest.fixture(scope="session")
def trial_fixtures(tmp_path_factory):
    base = tmp_path_factory.mktemp("interrupted-audit-source") / "generated"
    probe(base)
    return base


def copied_case(tmp_path, trial_fixtures, case="provider_hang"):
    target = tmp_path / case
    shutil.copytree(trial_fixtures / case, target)
    return target


def test_provider_hang_is_integral_but_unaccepted(tmp_path, trial_fixtures):
    result = audit(copied_case(tmp_path, trial_fixtures))
    assert result["integrity_pass"] is True
    assert result["trial_acceptance_pass"] is False
    assert result["complete_accounting"] is False
    assert result["product_promotion_pass"] is False
    assert result["agent_efficiency_acceptance_pass"] is False
    assert result["savings"] == "unmeasured"
    assert result["scope"] == "offline_postmortem_only"
    assert result["generation"] == {
        "admitted_attempts": 1,
        "finished_known_usage_attempts": 0,
        "pending_or_unknown_attempts": 1,
        "reserved_tokens": 72,
        "observed_tokens": 0,
        "unadmitted_pending_reservation": 0,
    }
    assert result["last_loop_boundary"] == "model_begin"
    assert result["cleanup"]["remote_provider_cancellation"] == "unknown"


def test_broker_hang_usage_requires_response_and_finish_agreement(tmp_path, trial_fixtures):
    result = audit(copied_case(tmp_path, trial_fixtures, "broker_hang"))
    assert result["integrity_pass"] is True
    assert result["generation"]["reserved_tokens"] == 72
    assert result["generation"]["observed_tokens"] == 24
    assert result["generation"]["pending_or_unknown_attempts"] == 0
    assert result["last_loop_boundary"] == "tool_begin"
    assert result["trial_acceptance_pass"] is False


@pytest.mark.parametrize("mutation", [
    "partial_json", "changed_raw", "missing_receipt", "count_mismatch", "fake_usage",
])
def test_tampered_evidence_fails_closed(tmp_path, trial_fixtures, mutation):
    case = copied_case(tmp_path, trial_fixtures, "broker_hang")
    if mutation == "partial_json":
        (case / "worker/transport/end-000002.json").write_text('{"event":"end"', encoding="utf-8")
    elif mutation == "changed_raw":
        with (case / "worker/transport/response-000002.bin").open("ab") as stream:
            stream.write(b"x")
    elif mutation == "missing_receipt":
        (case / "worker/transport/end-000002.json").unlink()
    elif mutation == "count_mismatch":
        path = case / "worker/provider/count-response-000001.json"
        obj = json.loads(path.read_text())
        obj["input_tokens"] = 9
        path.write_text(json.dumps(obj), encoding="utf-8")
    elif mutation == "fake_usage":
        path = case / "worker/provider/generation-ledger.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[-1]["usage"]["total_tokens"] = 999
        path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    result = audit(case)
    assert result["integrity_pass"] is False
    assert result["trial_acceptance_pass"] is False
    assert result["errors"]


def test_unknown_cleanup_stays_unknown_even_when_supervisor_reports_termination(tmp_path, trial_fixtures):
    case = copied_case(tmp_path, trial_fixtures)
    result = audit(case)
    assert result["cleanup"]["local_process_group_termination_verified"] is True
    assert result["cleanup"]["remote_provider_cancellation"] == "unknown"

