"""Validate both tool brokers on all six faults. No solver/model is invoked.

Restoration uses the known pristine source, so these are fixture checks, NOT
agent repairs, task-success measurements, or inference/token-saving evidence.
"""
import argparse
import json
from pathlib import Path

from agent_trials import BASE, tasks, audit_trace
from task_session import prepare_session, sha


def require_ok(response):
    if not response["ok"]:
        raise RuntimeError(response["error"])
    return response["result"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="paired-broker-v1")
    args = parser.parse_args()
    records = []
    for task in tasks():
        pair = []
        for arm in ("graf", "graphify"):
            session = prepare_session(task, f"{args.run_id}-{task['id']}-{arm}", arm)
            path = session.root / task["fault"]["path"]
            faulty = path.read_text()
            original = (BASE / ".research/corpora" / task["repo"] / task["fault"]["path"]).read_text()
            assert faulty != original
            assert not (session.root / ".graf").exists()
            path.write_text(original)  # Trusted fixture check, not a solver action.
            base = require_ok(session.call("test", {}))
            assert base["verification_passed"], (task["id"], arm, "base", base)
            path.write_text(faulty)
            fault = require_ok(session.call("test", {}))
            assert not fault["verification_passed"], (task["id"], arm, "fault not detected")
            navigation = require_ok(session.call("retrieve", {"query": task["issue"], "budget": 2000}))
            initial = json.loads((session.directory / "index-build-1.json").read_text())
            assert (session.input / task["fault"]["path"]).read_text() == faulty
            # Check source reads against the known fault line, without calling this localization success.
            start = original[:original.index(task["fault"]["before"])].count("\n") + 1
            read = require_ok(session.call("read", {"path": task["fault"]["path"], "start": start, "lines": 3}))
            assert read["source_hash"] == sha(faulty)
            require_ok(session.call("edit", {"path": task["fault"]["path"], "old": faulty, "new": original}))
            require_ok(session.call("retrieve", {"query": task["issue"], "budget": 2000}))
            assert (session.input / task["fault"]["path"]).read_text() == original
            restored = require_ok(session.call("finish", {}))
            assert restored["verification_passed"], (task["id"], arm, "restored", restored)
            assert session.closed
            try:
                audit_trace(session.events)
            except ValueError:
                pass  # No model usage exists; these traces must not be scoreable as solver trials.
            else:
                raise AssertionError("Fixture trace was incorrectly accepted as an agent trial")
            pair.append(initial["source_hashes"])
            records.append({"task": task["id"], "arm": arm,
                            "kind": "broker_fixture_validation_not_agent_repair",
                            "base_pass": True, "injected_fault_failed": True, "restored_pass": True,
                            "clean_indexes_excluded": True, "faulty_snapshot_indexed": True,
                            "edited_snapshot_refreshed": True, "source_read_hash_verified": True,
                            "solver_invoked": False, "provider_usage_measured": False,
                            "transcripts": str(session.directory.relative_to(BASE))})
            print(task["id"], arm, "base pass / fault fail / restored pass; broker validated", flush=True)
        assert pair[0] == pair[1], "Arms indexed different faulty source inputs"
    output = BASE / "results/task-broker-sanity.json"
    output.write_text(json.dumps({"kind": "fixture_validation_not_agent_evaluation", "records": records,
                                 "all_matched_inputs": True, "solver_invoked": False}, indent=2) + "\n")
    print(output)


if __name__ == "__main__":
    main()
