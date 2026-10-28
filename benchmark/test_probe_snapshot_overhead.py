import json

from benchmark import probe_snapshot_overhead as probe


def test_counterbalance_cycles_through_all_six_arm_orders():
    observed = [tuple(probe.counterbalanced_arms(i)) for i in range(12)]
    assert len(set(observed[:6])) == 6
    assert observed[:6] == observed[6:]


def test_phase_sum_and_native_subtraction_are_explicit():
    record = {"phases": {"snapshot_enter_wall_ms": 7.0, "broker_init_wall_ms": 3.0,
                          "retrieve_wall_ms": 11.0, "source_read_wall_ms": 5.0,
                          "broker_close_wall_ms": 2.0, "snapshot_cleanup_wall_ms": 4.0},
              "native_child_samples_ms": [8.0, 1.0]}
    probe.finalize_metrics(record, 34.5)
    assert record["total_ms"] == 32.0
    assert record["native_child_ms"] == 9.0
    assert record["overhead_total_minus_native_ms"] == 23.0
    assert record["actual_wall_ms"] == 34.5


def test_serialized_token_count_includes_both_requests_and_replies():
    messages = [{"request": "retrieve"}, {"reply": "retrieve"},
                {"request": "read"}, {"reply": "read"}]
    assert probe.serialized_payload_tokens(*messages) == sum(
        len(probe.tokens(json.dumps(message, separators=(",", ":")))) for message in messages)


def test_source_text_or_hash_mismatch_fails_session_gate():
    valid = {"errors": [], "retrieve_success": True,
             "payload_parity": {"stdout": True, "stderr": True},
             "source_parity": {"success": True, "text": True, "sha256": True},
             "snapshot_cleanup": {"removed": True}}
    assert probe.session_passed(valid)
    valid["source_parity"]["text"] = False
    assert not probe.session_passed(valid)
    valid["source_parity"].update(text=True, sha256=False)
    assert not probe.session_passed(valid)


def test_failed_snapshot_enter_keeps_receipt_and_error(tmp_path, monkeypatch):
    class FailingSnapshot:
        def __init__(self, *args, **kwargs):
            self.receipt = {"phase": "copying", "preserve": "failure receipt"}

        def __enter__(self):
            raise RuntimeError("synthetic snapshot failure")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(probe, "make_spec", lambda *args: {
        "artifacts": {"graf_v61": {"path": "/unused", "sha256": "a" * 64}},
        "source_manifest": {"README.md": "b" * 64},
    })
    monkeypatch.setattr(probe, "index_options", lambda *args: {})
    monkeypatch.setattr(probe, "Snapshot", FailingSnapshot)
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    comparison = {"scratch": "fixture", "repositories": {"repo": {"file_hashes": {"README.md": "b" * 64}}}}
    row = {"id": "q1", "budget": 2000}
    old_plan = {"index_semantics": {"repo": "c" * 64}}
    plan = {"sources": {}}
    result = probe.run_session(plan, old_plan, evidence, comparison, {"q1": {"query": "fixture"}},
                              row, "repo", "graf_evidence_selective", 1, 0)
    folder = evidence / "r01-p00-q1-2000-graf_v61"
    assert result["errors"][0]["error"] == "synthetic snapshot failure"
    assert result["phases"]["snapshot_enter_wall_ms"] >= 0
    assert json.loads((folder / "snapshot-receipt.json").read_text()) == FailingSnapshot().receipt
    session = json.loads((folder / "session.json").read_text())
    assert session["record"]["errors"][0]["error_type"] == "RuntimeError"
