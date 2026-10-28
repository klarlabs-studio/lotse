"""Versioned, fail-closed OpenAI protocol diagnostic with durable transport receipts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

from openai_transport import OpenAITransport
from provider_ledger import ProviderLedger, _parse_request
from provider_request import ProviderRequestSession
from recorded_transport import RecordedTransport

ROOT = Path(__file__).resolve().parents[1]
REQUEST = {"model": "gpt-6-luna", "input": "Reply OK.",
           "instructions": "Be concise.", "max_output_tokens": 16}
LIMITS = {"token_limit": 256, "attempt_limit": 1, "count_limit": 1}
SOURCES = (
    "benchmark/openai_transport.py", "benchmark/provider_ledger.py",
    "benchmark/provider_request.py", "benchmark/recorded_transport.py",
    "benchmark/probe_openai_provider_v2.py", "benchmark/test_probe_openai_provider_v2.py",
)
CRITERIA = ["exact_returned_model", "count_matches_observed_input",
            "output_within_cap", "no_unknown_or_pending_generation_usage",
            "one_count_and_at_most_one_generation", "no_retries",
            "recorded_transport_matches_session_journals"]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def encode(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode()


def make_plan():
    return {"id": "openai-provider-v2", "scope": "single_request_protocol_diagnostic",
            "request": json.loads(encode(REQUEST)), "request_sha256": digest(encode(REQUEST)),
            "limits": json.loads(encode(LIMITS)),
            "credential_source": "OPENAI_API_KEY_environment_only",
            "criteria": list(CRITERIA),
            "sources": {name: digest((ROOT / name).read_bytes()) for name in SOURCES}}


def write_private(path, value):
    path = Path(path)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(encode(value))
        while view:
            n = os.write(fd, view)
            if n <= 0:
                raise OSError("short write")
            view = view[n:]
        os.fsync(fd)
    finally:
        os.close(fd)
    dfd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)


def validate_plan(path):
    raw = Path(path).read_bytes()
    plan = _parse_request(raw)
    expected = {"id", "scope", "request", "request_sha256", "limits", "sources",
                "criteria", "credential_source"}
    if set(plan) != expected:
        raise ValueError("invalid_plan_fields")
    if (plan["id"] != "openai-provider-v2" or
            plan["scope"] != "single_request_protocol_diagnostic" or
            plan["credential_source"] != "OPENAI_API_KEY_environment_only" or
            type(plan["request"]) is not dict or encode(plan["request"]) != encode(REQUEST) or
            plan["request_sha256"] != digest(encode(plan["request"])) or
            plan["limits"] != LIMITS or
            any(type(plan["limits"].get(k)) is not int for k in LIMITS) or
            plan["criteria"] != CRITERIA):
        raise ValueError("unsupported_plan")
    if type(plan["sources"]) is not dict or set(plan["sources"]) != set(SOURCES):
        raise ValueError("invalid_source_set")
    for name, expected_hash in plan["sources"].items():
        if type(expected_hash) is not str or digest((ROOT / name).read_bytes()) != expected_hash:
            raise ValueError("source_pin_mismatch")
    return plan, digest(raw)


def _json_file(directory, name):
    return _parse_request((directory / name).read_bytes())


def _reconcile(directory, session, recorder):
    """Reconcile transport receipts, session journals, ledger, and raw payloads."""
    entries = []
    good = True
    try:
        tdir = directory / "transport"
        config = _json_file(tdir, "config.json")
        good = good and config.get("attempt_limit") == 2
        expected_names = {"config.json"} | {
            f"{kind}-{seq:06d}.{ext}" for seq in (1, 2)
            for kind, ext in (("request", "bin"), ("begin", "json"),
                              ("response", "bin"), ("end", "json"))}
        if {p.name for p in tdir.iterdir()} != expected_names:
            good = False
        for seq in (1, 2):
            try:
                begin = _json_file(tdir, f"begin-{seq:06d}.json")
                end = _json_file(tdir, f"end-{seq:06d}.json")
                req = (tdir / f"request-{seq:06d}.bin").read_bytes()
                rsp = (tdir / f"response-{seq:06d}.bin").read_bytes()
                expected_path = "/v1/responses/input_tokens" if seq == 1 else "/v1/responses"
                expected_req = (directory / "session" /
                                ("count-request-000001.json" if seq == 1 else "generation-request-000001.json"))
                expected_rsp = (directory / "session" /
                                ("count-response-000001.json" if seq == 1 else "generation-response-000001.json"))
                checks = (begin.get("seq") == seq and end.get("seq") == seq and
                          begin.get("path") == expected_path and end.get("path") == expected_path and
                          begin.get("request_sha256") == digest(req) and
                          end.get("request_sha256") == digest(req) and
                          end.get("response_sha256") == digest(rsp) and
                          end.get("outcome") == "response" and end.get("status") == 200 and
                          expected_req.exists() and expected_rsp.exists() and
                          req == expected_req.read_bytes() and rsp == expected_rsp.read_bytes())
                good = good and checks
                entries.append({"seq": seq, "path": expected_path,
                                "request_sha256": digest(req), "response_sha256": digest(rsp),
                                "status": end.get("status")})
            except Exception:
                good = False
        session_dir = directory / "session"
        count_rows = [_parse_request(row) for row in (session_dir / "count-journal.jsonl").read_bytes().splitlines()]
        good = good and [r.get("event") for r in count_rows] == ["config", "count_begin", "count_end"]
        count_begin = [r for r in count_rows if r.get("event") == "count_begin"]
        count_end = [r for r in count_rows if r.get("event") == "count_end"]
        count_req = (session_dir / "count-request-000001.json").read_bytes()
        count_rsp = (session_dir / "count-response-000001.json").read_bytes()
        gen_req = (session_dir / "generation-request-000001.json").read_bytes()
        gen_rsp = (session_dir / "generation-response-000001.json").read_bytes()
        ledger_rows = [_parse_request(row) for row in (session_dir / "generation-ledger.jsonl").read_bytes().splitlines()]
        good = good and [r.get("event") for r in ledger_rows] == ["config", "admitted", "finish"]
        admitted = [r for r in ledger_rows if r.get("event") == "admitted"]
        finished = [r for r in ledger_rows if r.get("event") == "finish"]
        good = good and len(count_begin) == len(count_end) == len(admitted) == len(finished) == 1
        if len(count_begin) == len(count_end) == len(admitted) == len(finished) == 1:
            cb, ce, ad, fin = count_begin[0], count_end[0], admitted[0], finished[0]
            count_value = _parse_request(count_rsp).get("input_tokens")
            usage = fin.get("usage")
            good = good and cb.get("count_request_sha256") == digest(count_req)
            good = good and cb.get("generation_sha256") == digest(encode(REQUEST))
            good = good and ce.get("raw_response_sha256") == digest(count_rsp) and ce.get("input_tokens") == count_value
            good = good and ad.get("request_sha256") == digest(gen_req) == digest(encode(REQUEST))
            good = good and ad.get("count_response_sha256") == digest(count_rsp)
            good = good and ad.get("input_bound") == count_value and ad.get("reserved_tokens") == count_value + 16
            raw_generation = _parse_request(gen_rsp)
            status = raw_generation.get("status")
            expected_outcome = "completed" if status == "completed" else "interrupted"
            valid_status = status == "completed" or (
                status == "incomplete" and
                raw_generation.get("incomplete_details") == {"reason": "max_output_tokens"})
            good = good and valid_status and fin.get("response_sha256") == digest(gen_rsp)
            good = good and fin.get("outcome") == expected_outcome
            good = good and type(usage) is dict and usage == ProviderLedger._validate_usage(
                raw_generation.get("usage"))
            good = good and usage.get("input_tokens") == count_value
    except Exception:
        good = False
    summary = recorder.summary()
    good = good and summary["attempts"] == 2 and summary["denials"] == 0
    good = good and summary["unknown_usage_attempts"] == 2 and summary["pending_attempts"] == 0
    good = good and not summary["poisoned"] and session["count_attempts"] == 1
    return bool(good), entries


def run(plan, output, *, execute=False):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("report_already_exists")
    validated, plan_hash = validate_plan(plan)
    report = {"id": validated["id"], "plan_sha256": plan_hash,
              "scope": validated["scope"], "request": REQUEST, "limits": LIMITS,
              "network_requests": 0, "live_diagnostic_pass": False,
              "complete_accounting": False, "complete_agent_accounting_pass": False,
              "product_promotion_pass": False,
              "agent_savings": "unmeasured", "graphify_comparison": "unchanged",
              "execution_mode": "execution_requested" if execute else "preflight_only",
              "live_provider_verified": False,
              "live_token_cap": "real_provider_unverified",
              "count_basis": "recorded_transport_forwarded_attempts; provider receipt unverified"}
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
    # Exclusive private root protects against accidentally reusing old evidence.
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    os.chmod(directory, 0o700)
    report["artifact_directory"] = str(directory)
    recorder = None
    session = None
    live = None
    try:
        live = OpenAITransport(key)
        recorder = RecordedTransport(directory / "transport", live, attempt_limit=2)
        session = ProviderRequestSession(directory / "session", recorder, **LIMITS)
        result = session.run(encode(REQUEST))
        summary = session.summary()
        report["result"] = result
        report["session"] = summary
        report["network_requests"] = recorder.summary()["attempts"]
        report["transport_summary"] = recorder.summary()
        observed = None
        gen_path = directory / "session" / "generation-response-000001.json"
        if gen_path.exists():
            obj = _parse_request(gen_path.read_bytes())
            usage = obj.get("usage") if type(obj.get("usage")) is dict else {}
            observed = {"model": obj.get("model"),
                        "input_tokens": usage.get("input_tokens"),
                        "output_tokens": usage.get("output_tokens"),
                        "total_tokens": usage.get("total_tokens")}
        report["observed"] = observed
        count = None
        for line in (directory / "session" / "count-journal.jsonl").read_bytes().splitlines():
            row = _parse_request(line)
            if row.get("event") == "count_end":
                count = row.get("input_tokens")
        report["counted_input_tokens"] = count
        reconciled, receipts = _reconcile(directory, summary, recorder)
        report["transport_receipts"] = receipts
        report["transport_reconciled"] = reconciled
        report["forwarded_attempts"] = recorder.summary()["attempts"]
        report["denied_attempts"] = recorder.summary()["denials"]
        generation = summary["generation"]
        passed = bool(result.get("outcome") in ("completed", "incomplete") and observed and
                      observed["model"] == REQUEST["model"] and type(count) is int and
                      type(observed["input_tokens"]) is int and count == observed["input_tokens"] and
                      type(observed["output_tokens"]) is int and
                      0 <= observed["output_tokens"] <= REQUEST["max_output_tokens"] and
                      type(observed["total_tokens"]) is int and
                      observed["total_tokens"] == observed["input_tokens"] + observed["output_tokens"] and
                      generation["accepted_attempts"] == 1 and
                      generation["unknown_usage_attempts"] == 0 and
                      generation["pending_attempts"] == 0 and not summary["poisoned"] and
                      report["network_requests"] == 2 and reconciled)
        report.update(status="completed" if passed else "failed",
                      live_diagnostic_pass=passed, exit_code=0 if passed else 1)
    except BaseException:
        # Preserve any artifacts already written; never expose exception text or credentials.
        report.update(status="failed", exit_code=1, failure="protocol_or_accounting_error",
                      live_diagnostic_pass=False)
        if session is not None:
            try:
                report["session"] = session.summary()
            except Exception:
                pass
        if recorder is not None:
            report["transport_summary"] = recorder.summary()
            report["network_requests"] = recorder.summary()["attempts"]
            report["forwarded_attempts"] = recorder.summary()["attempts"]
            report["denied_attempts"] = recorder.summary()["denials"]
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        if live is not None:
            try:
                live.close()
            except Exception:
                pass
        if recorder is not None:
            try:
                recorder.close()
            except Exception:
                pass
    artifacts = {}
    for p in sorted(directory.rglob("*")):
        if p.is_file():
            artifacts[str(p.relative_to(directory))] = digest(p.read_bytes())
    report["artifacts"] = artifacts
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
