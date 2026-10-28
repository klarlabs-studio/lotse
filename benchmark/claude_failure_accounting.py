"""Fail-closed task and usage classification for Claude Code JSONL captures.

The underlying usage diagnostic observes stream totals. This module adds a
separate task-outcome classification without treating those observations as
complete provider-attempt or billing records.
"""

import json

from claude_usage_diagnostic import analyze


def _valid_prefix(raw: bytes) -> bytes:
    """Return complete, parseable JSONL lines before the first bad line."""
    good = []
    for line in raw.splitlines(keepends=True):
        if not line.endswith((b"\n", b"\r")):
            break
        if not line.strip():
            good.append(line)
            continue
        try:
            value = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            break
        if not isinstance(value, dict):
            break
        # Reuse the strict diagnostic parser so duplicate keys and non-finite
        # values cannot enter the salvaged observation prefix.
        if "parse_error" in analyze(b"".join(good) + line):
            break
        good.append(line)
    return b"".join(good)


def analyze_failure(raw: bytes) -> dict:
    """Classify task outcome and preserve observed usage, conservatively.

    A terminal usage report is called internally consistent only when there is
    one result, valid result and per-model usage, and matching aggregate totals.
    This says nothing about hidden retries/provider attempts or billing.
    """
    report = analyze(raw)
    # The base analyzer fails closed as a whole. Salvage only complete valid
    # JSONL prefix observations; never infer missing totals from that prefix.
    malformed = "parse_error" in report
    if malformed and isinstance(raw, bytes):
        prefix = _valid_prefix(raw)
        if prefix:
            recovered = analyze(prefix)
            report.update(recovered)
            report["parse_error"] = "invalid_stream_json"
            report["result_usage"] = None
            report["result_usage_valid"] = False
            report["model_usage"] = None
            report["model_usage_totals"] = None
            report["totals_match"] = False
            report["diagnostic_success"] = False
            report["diagnostic_pass"] = False

    results = []
    if isinstance(raw, bytes):
        # Read terminal metadata only from a fully parseable stream. Recovered
        # prefix reports cannot contain a trusted terminal result.
        if not malformed:
            for line in raw.splitlines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    results = []
                    break
                if isinstance(event, dict) and event.get("type") == "result":
                    results.append(event)

    result = results[0] if len(results) == 1 else None
    subtype = result.get("subtype") if result else None
    is_error = result.get("is_error") if result else None
    if malformed:
        outcome = "malformed_stream"
    elif result is None:
        outcome = "interrupted" if report.get("assistant_count", 0) else "no_result"
    elif subtype == "success" and is_error is False:
        outcome = "success"
    elif subtype == "error_max_turns":
        outcome = "error_max_turns"
    elif is_error is True or (isinstance(subtype, str) and subtype.startswith("error")):
        outcome = "error"
    else:
        outcome = "terminal_other"

    consistent = bool(
        result is not None
        and report.get("result_usage_valid") is True
        and report.get("model_usage_totals") is not None
        and report.get("totals_match") is True
        and not malformed
    )
    report.update({
        "task_outcome": outcome,
        "result_type": result.get("type") if result else None,
        "result_subtype": subtype,
        "result_is_error": is_error,
        "exposed_usage": report.get("result_usage"),
        "exposed_model_usage": report.get("model_usage"),
        "exposed_usage_totals": report.get("model_usage_totals"),
        "exposed_estimated_cost": (
            report.get("model_usage_totals", {}).get("costUSD")
            if isinstance(report.get("model_usage_totals"), dict) else None
        ),
        "accounting_status": "reported_terminal_consistent" if consistent else "unknown",
        "internal_provider_attempts": "unmeasured",
        "complete_accounting": False,
        "hardcap_verified": False,
        "promotion": False,
        "savings": "unmeasured",
    })
    if malformed and "parse_error" not in report:
        report["parse_error"] = "invalid_stream_json"
    return report
