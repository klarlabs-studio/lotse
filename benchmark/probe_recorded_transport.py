"""Offline integration diagnostic; no retrievals, providers, or efficiency claims."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from provider_request import ProviderRequestSession
from recorded_transport import RecordedTransport


ROOT = Path(__file__).resolve().parents[1]
BODY = b'{"model":"fixture","input":"hello","max_output_tokens":16}'


def encode(value):
    return json.dumps(value, sort_keys=True, allow_nan=False).encode()


def digest(body):
    return hashlib.sha256(body).hexdigest()


def reconcile(directory, forwarded, summary):
    """Recompute facts from durable files and the independent fixture call log."""
    if not __debug__:
        raise RuntimeError("diagnostic requires Python assertions enabled")
    begins = sorted(directory.glob("begin-*.json"))
    ends = sorted(directory.glob("end-*.json"))
    denials = sorted(directory.glob("denied-*.json"))
    assert len(begins) == len(ends) == len(forwarded) == summary["attempts"]
    assert len(denials) == summary["denials"]
    assert summary["pending_attempts"] == 0
    for seq, call in enumerate(forwarded, 1):
        stem = f"{seq:06d}"
        begin = json.loads((directory / f"begin-{stem}.json").read_bytes())
        end = json.loads((directory / f"end-{stem}.json").read_bytes())
        assert begin["seq"] == end["seq"] == seq
        assert begin["path"] == end["path"] == call["path"]
        assert begin["request_sha256"] == end["request_sha256"] == call["request_sha256"]
        assert digest((directory / f"request-{stem}.bin").read_bytes()) == call["request_sha256"]
        assert end["usage"] == "unknown"
        if call.get("outcome") == "transport_error":
            assert end["outcome"] == "transport_exception"
            assert not (directory / f"response-{stem}.bin").exists()
        else:
            assert end["status"] == call["status"]
            assert end["response_sha256"] == call["response_sha256"]
            assert digest((directory / f"response-{stem}.bin").read_bytes()) == call["response_sha256"]
    return {"attempts_verified": len(begins), "denials_verified": len(denials)}


def run(directory):
    if not __debug__:
        raise RuntimeError("diagnostic requires Python assertions enabled")
    directory = Path(directory)
    directory.mkdir(mode=0o700, exist_ok=False)
    scenarios = []
    for name in ("completed", "generation_http_error", "generation_transport_error",
                 "count_http_error", "explicit_retry_limit"):
        case = directory / name
        case.mkdir(mode=0o700)
        forwarded = []

        def fixture(path, body):
            forwarded.append({"path": path, "request_sha256": digest(body)})
            if path.endswith("input_tokens"):
                status, response = (503, b'{}') if name == "count_http_error" else (
                    200, encode({"object": "response.input_tokens", "input_tokens": 3}))
            elif name == "generation_transport_error":
                forwarded[-1]["outcome"] = "transport_error"
                raise OSError("fixture-secret-must-not-be-recorded")
            elif name == "generation_http_error":
                status, response = 429, b'{"error":"fixture"}'
            else:
                status, response = 200, encode({"status": "completed", "usage": {
                    "input_tokens": 3, "output_tokens": 2, "total_tokens": 5}})
            forwarded[-1].update(status=status, response_sha256=digest(response))
            return status, response

        limit = 3 if name == "explicit_retry_limit" else 2
        with RecordedTransport(case / "transport", fixture, attempt_limit=limit) as recorded:
            with ProviderRequestSession(case / "session", recorded, token_limit=100,
                                        attempt_limit=3, count_limit=3) as session:
                outcomes = [session.run(BODY)["outcome"]]
                if name == "explicit_retry_limit":
                    outcomes.append(session.run(BODY)["outcome"])
                session_summary = session.summary()
            transport_summary = recorded.summary()
        expected = {
            "completed": (["completed"], 2, 0),
            "generation_http_error": (["http_error"], 2, 1),
            "generation_transport_error": (["transport_error"], 2, 1),
            "count_http_error": (["count_failed"], 1, 0),
            "explicit_retry_limit": (["completed", "transport_error"], 3, 1),
        }[name]
        assert outcomes == expected[0], (name, outcomes)
        assert len(forwarded) == expected[1], (name, forwarded)
        assert session_summary["generation"]["unknown_usage_attempts"] == expected[2]
        assert session_summary["generation"]["pending_attempts"] == 0
        assert session_summary["complete_accounting"] is False
        verification = reconcile(case / "transport", forwarded, transport_summary)
        assert transport_summary["denials"] == (1 if name == "explicit_retry_limit" else 0)
        for file in (case / "transport").iterdir():
            assert b"fixture-secret-must-not-be-recorded" not in file.read_bytes()
        scenarios.append({"id": name, "outcomes": outcomes, "forwarded": forwarded,
                          "session": session_summary, "transport": transport_summary,
                          "verification": verification})
    sources = ["benchmark/recorded_transport.py", "benchmark/test_recorded_transport.py",
               "benchmark/probe_recorded_transport.py", "benchmark/test_probe_recorded_transport.py",
               "benchmark/provider_request.py",
               "benchmark/provider_ledger.py", "benchmark/preregistration.json",
               "benchmark/corpora.json", "results/acceptance.json", "results/decision.json",
               "results/confirmation-v64-readiness.json"]
    report = {"scope": "offline_accounting_integration_only", "scenarios": scenarios,
              "network_requests": 0, "retrievals": 0, "solver_calls": 0,
              "diagnostic_pass": True, "complete_agent_accounting_pass": False,
              "live_token_cap": "unverified", "product_promotion_pass": False,
              "agent_savings": "unmeasured", "graphify_comparison": "unchanged",
              "source_sha256": {name: digest((ROOT / name).read_bytes()) for name in sources},
              "artifact_sha256": {str(p.relative_to(directory)): digest(p.read_bytes())
                                  for p in sorted(directory.rglob("*")) if p.is_file()}}
    (directory / "report.json").write_bytes(encode(report) + b"\n")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    report = run(args.directory)
    print(json.dumps({"diagnostic_pass": report["diagnostic_pass"],
                      "scenarios": len(report["scenarios"]), "network_requests": 0}))


if __name__ == "__main__":
    main()
