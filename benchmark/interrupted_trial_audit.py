"""Read-only integrity audit for one saved supervised-agent interrupted trial."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _json_bytes(data: bytes) -> Any:
    return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)


def _read_json(path: Path) -> Any:
    return _json_bytes(path.read_bytes())


def _jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    for index, line in enumerate(path.read_bytes().splitlines(), 1):
        if not line.strip():
            continue
        value = _json_bytes(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}:{index}: expected object")
        records.append(value)
    return records


def audit(directory: str | Path) -> dict[str, Any]:
    """Audit a copied provider_hang or broker_hang case without changing it."""
    root = Path(directory)
    errors: list[str] = []

    def load_json(rel: str) -> dict[str, Any] | None:
        try:
            obj = _read_json(root / rel)
            if not isinstance(obj, dict):
                raise ValueError("expected JSON object")
            return obj
        except Exception as exc:
            errors.append(f"{rel}: {exc}")
            return None

    def load_lines(rel: str) -> list[dict[str, Any]]:
        try:
            return _jsonl(root / rel)
        except Exception as exc:
            errors.append(f"{rel}: {exc}")
            return []

    def raw(rel: str, expected_hash: str | None = None, expected_size: int | None = None) -> bytes | None:
        try:
            data = (root / rel).read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            if expected_hash is not None and digest != expected_hash:
                errors.append(f"{rel}: SHA-256 mismatch")
            if expected_size is not None and len(data) != expected_size:
                errors.append(f"{rel}: byte length mismatch")
            return data
        except Exception as exc:
            errors.append(f"{rel}: {exc}")
            return None

    report = load_json("supervisor/report.json")
    supervisor_events = load_lines("supervisor/events.jsonl")
    if report:
        for field, filename in (("stdout", "stdout.bin"), ("stderr", "stderr.bin")):
            raw(f"supervisor/{filename}", report.get(f"{field}_sha256"), report.get(f"{field}_bytes"))
    if supervisor_events:
        expected = [(1, "start_intent"), (2, "spawned"), (3, "terminated"), (4, "outputs_hashed")]
        observed = [(e.get("seq"), e.get("event")) for e in supervisor_events]
        if observed != expected:
            errors.append("supervisor/events.jsonl: unexpected event sequence")
        if report and supervisor_events[-1].get("stdout_sha256") != report.get("stdout_sha256"):
            errors.append("supervisor stdout hash disagrees with report")
        if report and supervisor_events[-1].get("stderr_sha256") != report.get("stderr_sha256"):
            errors.append("supervisor stderr hash disagrees with report")

    transport = root / "worker/transport"
    begins: dict[int, dict[str, Any]] = {}
    ends: dict[int, dict[str, Any]] = {}
    for p in sorted(transport.glob("begin-*.json")):
        item = load_json(p.relative_to(root).as_posix())
        if item:
            seq = item.get("seq")
            if not isinstance(seq, int) or seq in begins:
                errors.append(f"{p}: invalid or duplicate sequence")
            else:
                begins[seq] = item
                raw(f"worker/transport/request-{seq:06d}.bin", item.get("request_sha256"))
    for p in sorted(transport.glob("end-*.json")):
        item = load_json(p.relative_to(root).as_posix())
        if item:
            seq = item.get("seq")
            if not isinstance(seq, int) or seq in ends:
                errors.append(f"{p}: invalid or duplicate sequence")
            else:
                ends[seq] = item
                if seq not in begins:
                    errors.append(f"{p}: end without begin")
                elif item.get("request_sha256") != begins[seq].get("request_sha256"):
                    errors.append(f"{p}: request identity differs from begin")
                if item.get("outcome") == "response":
                    raw(f"worker/transport/response-{seq:06d}.bin", item.get("response_sha256"), item.get("response_bytes"))
    if not set(ends).issubset(begins):
        errors.append("transport: terminal receipt without begin")

    ledger = load_lines("worker/provider/generation-ledger.jsonl")
    count_journal = load_lines("worker/provider/count-journal.jsonl")
    loop_dir = root / "worker/loop"
    loop_events: list[dict[str, Any]] = []
    try:
        for p in sorted(loop_dir.glob("event-*.json")):
            obj = _read_json(p)
            if not isinstance(obj, dict):
                raise ValueError(f"{p.name}: expected object")
            loop_events.append(obj)
    except Exception as exc:
        errors.append(f"worker/loop: {exc}")

    admitted = [e for e in ledger if e.get("event") == "admitted"]
    finishes = {e.get("attempt_id"): e for e in ledger if e.get("event") == "finish"}
    reserved = sum(e.get("reserved_tokens", 0) for e in admitted if isinstance(e.get("reserved_tokens"), int))
    observed_tokens = 0
    known_attempts = 0
    for admission in admitted:
        attempt = admission.get("attempt_id")
        request_hash = admission.get("request_sha256")
        matched = [seq for seq, begin in begins.items() if begin.get("request_sha256") == request_hash and begin.get("path") == "/v1/responses"]
        if len(matched) != 1:
            errors.append(f"generation attempt {attempt}: admission does not map to exactly one transport begin")
        seq = matched[0] if len(matched) == 1 else None
        req_path = f"worker/provider/generation-request-{int(admission.get('request_number', 0)):06d}.json" if isinstance(admission.get("request_number"), int) else ""
        req = load_json(req_path) if req_path else None
        if req is not None:
            req_raw = (root / req_path).read_bytes()
            if hashlib.sha256(req_raw).hexdigest() != request_hash:
                errors.append(f"{req_path}: does not match admitted request hash")
            if req.get("max_output_tokens") != admission.get("max_output_tokens") or req.get("model") != admission.get("model"):
                errors.append(f"{req_path}: admission bounds/model mismatch")
        finish = finishes.get(attempt)
        if finish is None:
            continue
        if seq is None or seq not in ends:
            errors.append(f"generation attempt {attempt}: finish lacks terminal transport receipt")
            continue
        end = ends[seq]
        response_hash = finish.get("response_sha256")
        if end.get("response_sha256") != response_hash:
            errors.append(f"generation attempt {attempt}: finish response hash differs from transport")
            continue
        response_path = f"worker/provider/generation-response-{int(admission.get('request_number', 0)):06d}.json"
        response = load_json(response_path)
        if response is None:
            continue
        response_raw = (root / response_path).read_bytes()
        if hashlib.sha256(response_raw).hexdigest() != response_hash:
            errors.append(f"{response_path}: response hash mismatch")
            continue
        usage = response.get("usage")
        if not isinstance(usage, dict) or usage != finish.get("usage"):
            errors.append(f"generation attempt {attempt}: ledger usage disagrees with raw response")
            continue
        fields = (usage.get("input_tokens"), usage.get("output_tokens"), usage.get("total_tokens"))
        if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in fields) or fields[0] + fields[1] != fields[2]:
            errors.append(f"generation attempt {attempt}: invalid usage values")
            continue
        if fields[0] > admission.get("input_bound", -1) or fields[1] > admission.get("max_output_tokens", -1):
            errors.append(f"generation attempt {attempt}: usage exceeds admitted bound")
            continue
        observed_tokens += fields[2]
        known_attempts += 1

    # A begun generation with no admitted ledger record is a pending attempt with unknown reservation.
    generation_begins = {seq for seq, e in begins.items() if e.get("path") == "/v1/responses"}
    admitted_hashes = {e.get("request_sha256") for e in admitted}
    orphan_begins = {seq for seq in generation_begins if begins[seq].get("request_sha256") not in admitted_hashes}
    pending = sum(1 for e in admitted if e.get("attempt_id") not in finishes) + len(orphan_begins)
    for e in admitted:
        if not isinstance(e.get("input_bound"), int) or not isinstance(e.get("max_output_tokens"), int) or e.get("reserved_tokens") != e.get("input_bound", -1) + e.get("max_output_tokens", -1):
            errors.append(f"generation attempt {e.get('attempt_id')}: admitted reservation does not equal input plus output bounds")

    # Validate count journal references and saved count request/response objects conservatively.
    cb = {e.get("count_attempt"): e for e in count_journal if e.get("event") == "count_begin"}
    ce = {e.get("count_attempt"): e for e in count_journal if e.get("event") == "count_end"}
    for n, begin in cb.items():
        reqp = f"worker/provider/count-request-{int(n):06d}.json" if isinstance(n, int) else ""
        resp = f"worker/provider/count-response-{int(n):06d}.json" if isinstance(n, int) else ""
        req = load_json(reqp) if reqp else None
        if req is not None and hashlib.sha256((root / reqp).read_bytes()).hexdigest() != begin.get("count_request_sha256"):
            errors.append(f"{reqp}: hash differs from count journal")
        if n not in ce:
            continue
        end = ce[n]
        response_hash = end.get("raw_response_sha256")
        seqs = [s for s, b in begins.items() if b.get("request_sha256") == begin.get("count_request_sha256")]
        if len(seqs) != 1 or seqs[0] not in ends or ends[seqs[0]].get("response_sha256") != response_hash:
            errors.append(f"count attempt {n}: journal does not map to a completed transport response")
        response_obj = load_json(resp) if resp else None
        if response_obj is not None and response_obj.get("input_tokens") != end.get("input_tokens"):
            errors.append(f"{resp}: count differs from journal")

    last_loop = loop_events[-1].get("event") if loop_events else None
    local_termination = bool(report and report.get("outcome") == "timeout" and report.get("group_exit_verified") is True)
    cleanup = {"local_process_group_termination_verified": local_termination,
               "remote_provider_cancellation": "unknown"}
    result = {
        "scope": "offline_postmortem_only",
        "integrity_pass": not errors,
        "trial_acceptance_pass": False,
        "complete_accounting": False,
        "complete_accounting_pass": False,
        "product_promotion_pass": False,
        "agent_efficiency_acceptance_pass": False,
        "savings": "unmeasured",
        "errors": errors,
        "transport": {"begins": len(begins), "terminal_receipts": len(ends), "pending_sequences": sorted(set(begins) - set(ends))},
        "generation": {"admitted_attempts": len(admitted), "finished_known_usage_attempts": known_attempts,
                       "pending_or_unknown_attempts": pending, "reserved_tokens": reserved,
                       "observed_tokens": observed_tokens, "unadmitted_pending_reservation": "unknown" if orphan_begins else 0},
        "count": {"attempts_begun": len(cb), "attempts_finished": len(ce)},
        "last_loop_boundary": last_loop,
        "cleanup": cleanup,
    }
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    result = audit(args.directory)
    encoded = json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n"
    try:
        # Exclusive creation prevents overwriting source evidence or an existing result.
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(encoded)
    except Exception as exc:
        print(f"cannot save audit output: {exc}", file=sys.stderr)
        return 1
    print(encoded, end="")
    return 0 if result["integrity_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
