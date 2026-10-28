"""Offline supervisor/agent integration: completion and two interrupted stages."""
import argparse
import json
from pathlib import Path
import sys

from bounded_agent import wire, _save
from deadline_supervisor import run


ROOT = Path(__file__).resolve().parents[1]


def probe(directory):
    if not __debug__:
        raise RuntimeError("diagnostic requires assertions enabled")
    directory = Path(directory).resolve()
    directory.mkdir(mode=0o700, exist_ok=False)
    scenarios = []
    for mode in ("completed", "provider_hang", "broker_hang"):
        case = directory / mode
        case.mkdir(mode=0o700)
        worker_dir = case / "worker"
        command = [sys.executable, str(ROOT / "benchmark/supervised_agent_fixture.py"),
                   "--mode", mode, "--output-directory", str(worker_dir)]
        report = run(case / "supervisor", command, timeout_seconds=10 if mode == "completed" else 2,
                     cwd=ROOT)
        assert report["group_exit_verified"] is True and report["output_capture_complete"] is True
        assert report["usage"] == "unknown" and report["usage_accounting_complete"] is False
        assert json.loads((case / "supervisor/report.json").read_bytes()) == report
        if mode == "completed":
            assert report["outcome"] == "completed" and report["returncode"] == 0
            saved = json.loads((worker_dir / "report.json").read_bytes())
            assert saved["diagnostic_pass"] is True and saved["fixture_forwarded_calls"] == 13
            facts = {"fixture_calls": 13, "interrupted_generation_usage": False}
        else:
            assert report["outcome"] == "timeout" and report["deadline_exceeded"] is True
            events = [json.loads(p.read_bytes()) for p in sorted((worker_dir / "loop").glob("event-*.json"))]
            assert not (worker_dir / "loop/report.json").exists()
            begins = sorted((worker_dir / "transport").glob("begin-*.json"))
            ends = sorted((worker_dir / "transport").glob("end-*.json"))
            ledger = [json.loads(row) for row in (worker_dir / "provider/generation-ledger.jsonl").read_bytes().splitlines()]
            assert len(begins) == 2
            assert sum(row.get("event") == "admitted" for row in ledger) == 1
            if mode == "provider_hang":
                assert b"fixture generation entered" in (case / "supervisor/stdout.bin").read_bytes()
                assert len(ends) == 1
                assert events[-1]["event"] == "model_begin"
                assert not any(row.get("event") == "finish" for row in ledger)
            else:
                assert b"fixture broker entered" in (case / "supervisor/stdout.bin").read_bytes()
                assert len(ends) == 2
                assert events[-1]["event"] == "tool_begin"
                finished = [row for row in ledger if row.get("event") == "finish"]
                assert len(finished) == 1 and finished[0]["usage"]["total_tokens"] == 24
            facts = {"fixture_calls": 2, "interrupted_generation_usage": mode == "provider_hang",
                     "unfinished_loop": True, "transport_begins": len(begins),
                     "transport_ends": len(ends)}
        scenarios.append({"id": mode, "supervisor": report, "facts": facts})
    report = {"scope": "offline_process_supervision_only", "diagnostic_pass": True,
              "scenarios": scenarios, "network_requests": 0, "native_retrievals": 0,
              "solver_calls": 0, "complete_accounting": False,
              "live_provider_verified": False, "product_promotion_pass": False,
              "agent_savings": "unmeasured", "graphify_comparison": "unchanged"}
    _save(directory / "report.json", wire(report))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, required=True)
    args = parser.parse_args()
    report = probe(args.output_directory)
    print(json.dumps({k: report[k] for k in ("diagnostic_pass", "network_requests", "solver_calls")}))
