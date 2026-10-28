"""Frozen, single-request provider diagnostic; preflight is network-free."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import time

from openai_transport import OpenAITransport
from provider_request import ProviderRequestSession
from provider_ledger import _parse_request

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "benchmark/openai_transport.py", "benchmark/provider_request.py",
    "benchmark/provider_ledger.py", "benchmark/probe_openai_provider.py",
    "benchmark/test_openai_transport.py", "benchmark/test_provider_request.py",
    "benchmark/test_probe_openai_provider.py",
)
REQUEST = {"model": "gpt-6-luna", "input": "Reply OK.",
           "instructions": "Be concise.", "max_output_tokens": 16}
LIMITS = {"token_limit": 256, "attempt_limit": 1, "count_limit": 1}


def digest(body):
    return hashlib.sha256(body).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode()


def write_private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        data = memoryview(encode(value))
        while data:
            written = os.write(fd, data)
            if written <= 0:
                raise OSError("short write")
            data = data[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    dfd = os.open(Path(path).parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def validate_plan(path):
    raw = path.read_bytes()
    plan = _parse_request(raw)
    if set(plan) != {"id", "scope", "request", "request_sha256", "limits",
                     "sources", "criteria", "credential_source"}:
        raise ValueError("invalid_plan_fields")
    if (plan["id"] != "openai-provider-v1" or
            plan["scope"] != "single_request_protocol_diagnostic" or
            plan["credential_source"] != "OPENAI_API_KEY_environment_only" or
            plan["request"] != REQUEST or plan["limits"] != LIMITS or
            any(type(plan["limits"].get(k)) is not int for k in LIMITS) or
            plan["request_sha256"] != digest(encode(REQUEST)) or
            plan["criteria"] != ["exact_returned_model", "count_matches_observed_input", "output_within_cap",
                                  "no_unknown_or_pending_generation_usage",
                                  "one_count_and_at_most_one_generation", "no_retries"]):
        raise ValueError("unsupported_plan")
    if type(plan["sources"]) is not dict or set(plan["sources"]) != set(SOURCES):
        raise ValueError("invalid_source_set")
    for name, expected in plan["sources"].items():
        if digest((ROOT / name).read_bytes()) != expected:
            raise ValueError("source_pin_mismatch")
    return plan, digest(raw)


def run(plan_path, output, *, execute=False):
    plan_path, output = Path(plan_path).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError("report_already_exists")
    plan, plan_hash = validate_plan(plan_path)
    report = {"id": plan["id"], "plan_sha256": plan_hash,
              "scope": plan["scope"], "request": REQUEST, "limits": LIMITS,
              "network_requests": 0, "live_diagnostic_pass": False,
              "complete_accounting": False, "product_promotion_pass": False,
              "live_token_cap": "real_provider_unverified",
              "agent_savings": "unmeasured", "graphify_comparison": "unchanged"}
    # Preflight never constructs a transport or creates generation artifacts.
    key = os.environ.get("OPENAI_API_KEY")
    blocks = []
    if not key:
        blocks.append("missing_OPENAI_API_KEY")
    if not execute:
        blocks.append("execution_not_requested")
    if blocks:
        report.update(status="blocked", blockers=blocks, exit_code=1)
        write_private(output, report)
        return report

    directory = output.parent / (output.stem + "-private")
    transport = OpenAITransport(key)
    with ProviderRequestSession(directory, transport, **LIMITS) as session:
        def recorded(path, body):
            # A durable receipt precedes each actual network attempt, including
            # errors. The session preserves the request and response payloads.
            seq = report["network_requests"] + 1
            write_private(directory / f"transport-{seq}-begin.json",
                          {"path": path, "request_sha256": digest(body)})
            report["network_requests"] = seq
            start = time.monotonic()
            try:
                status, response = transport(path, body)
            except Exception:
                write_private(directory / f"transport-{seq}-end.json",
                              {"elapsed_seconds": time.monotonic() - start,
                               "outcome": "transport_error", "usage": "unknown"})
                raise RuntimeError("transport_error") from None
            write_private(directory / f"transport-{seq}-end.json",
                          {"status": status, "response_sha256": digest(response),
                           "elapsed_seconds": time.monotonic() - start})
            return status, response

        session.transport = recorded
        try:
            report["result"] = session.run(encode(REQUEST))
        except Exception:
            report["result"] = {"outcome": "protocol_error"}
        report["session"] = session.summary()
    generation = report["session"]["generation"]
    observed = None
    raw = directory / "generation-response-000001.json"
    if raw.exists():
        try:
            value = _parse_request(raw.read_bytes())
            usage = value.get("usage", {})
            observed = {"model": value.get("model"),
                        "input_tokens": usage.get("input_tokens"),
                        "output_tokens": usage.get("output_tokens")}
        except Exception:
            pass
    report["observed"] = observed
    count = None
    for line in (directory / "count-journal.jsonl").read_bytes().splitlines():
        entry = _parse_request(line)
        if entry.get("event") == "count_end":
            count = entry.get("input_tokens")
    report["counted_input_tokens"] = count
    passed = bool(
        report["result"]["outcome"] in ("completed", "incomplete") and observed and
        observed["model"] == REQUEST["model"] and
        type(count) is int and type(observed["input_tokens"]) is int and
        count == observed["input_tokens"] and
        type(observed["output_tokens"]) is int and
        0 <= observed["output_tokens"] <= REQUEST["max_output_tokens"] and
        generation["accepted_attempts"] == 1 and
        generation["unknown_usage_attempts"] == 0 and
        generation["pending_attempts"] == 0 and not report["session"]["poisoned"] and
        report["network_requests"] == 2 and report["session"]["count_attempts"] == 1)
    report.update(status="completed" if passed else "failed",
                  live_diagnostic_pass=passed, exit_code=0 if passed else 1)
    report["artifact_directory"] = str(directory)
    report["artifacts"] = {p.name: digest(p.read_bytes())
                           for p in sorted(directory.iterdir()) if p.is_file()}
    write_private(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    report = run(args.plan, args.output, execute=args.execute)
    print(json.dumps({k: report[k] for k in ("status", "exit_code", "network_requests")}))
    return report["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())
