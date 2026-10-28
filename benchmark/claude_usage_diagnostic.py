"""Conservative offline accounting diagnostic for Claude Code stream-json logs.

This summarizes observations in a captured stream. It cannot establish provider
request completeness, a hard spend cap, or actual billing.
"""
import json
import math
from collections import Counter


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"non-finite JSON number: {value}")


def _nonnegative_int(value):
    return type(value) is int and value >= 0


def _cost(value):
    if type(value) not in (int, float) or value < 0:
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError, ValueError):
        return False


def _analyze(raw: bytes) -> dict:
    """Analyze JSONL stream bytes without treating absent observations as zero."""
    if not isinstance(raw, bytes):
        raise TypeError("raw must be bytes")
    events = []
    for line_number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line, object_pairs_hook=_unique_object,
                               parse_constant=_reject_constant)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"invalid JSON on line {line_number}: {exc}") from exc
        if not isinstance(event, dict):
            raise ValueError(f"event on line {line_number} must be an object")
        events.append(event)

    counts = Counter(str(e.get("type", "<missing>")) for e in events)
    results = [e for e in events if e.get("type") == "result"]
    assistants = [e for e in events if e.get("type") == "assistant"]
    inits = [e for e in events if e.get("type") == "system" and e.get("subtype") == "init"]
    denial_values = [e["permission_denials"] for e in events if "permission_denials" in e]
    permission_denials_valid = all(isinstance(value, list) for value in denial_values)
    permission_denials = sum(len(value) for value in denial_values if isinstance(value, list))
    model_usage_events = [e for e in events if "modelUsage" in e]
    def usage_observation_count(event):
        count = int("modelUsage" in event) + int("usage" in event)
        message = event.get("message")
        if isinstance(message, dict):
            count += int("usage" in message)
        nested = event.get("event")
        if isinstance(nested, dict):
            count += int("modelUsage" in nested) + int("usage" in nested)
            nested_message = nested.get("message")
            if isinstance(nested_message, dict):
                count += int("usage" in nested_message)
        return count

    usage_events = [e for e in events if usage_observation_count(e)]
    usage_observation_count_total = sum(usage_observation_count(e) for e in events)

    model_usage = {}
    model_usage_valid = (len(model_usage_events) == 1 and len(results) == 1
                         and model_usage_events[0] is results[0])
    aggregate = {"inputTokens": 0, "cacheCreationInputTokens": 0,
                 "cacheReadInputTokens": 0, "outputTokens": 0, "costUSD": 0.0}
    keys_seen = set()
    for event in model_usage_events:
        value = event.get("modelUsage")
        if not isinstance(value, dict):
            model_usage_valid = False
            continue
        for model, detail in value.items():
            keys_seen.add(model)
            if not isinstance(detail, dict):
                model_usage_valid = False
                continue
            fields = ("inputTokens", "cacheCreationInputTokens", "cacheReadInputTokens", "outputTokens")
            if any(not _nonnegative_int(detail.get(field)) for field in fields) or not _cost(detail.get("costUSD")):
                model_usage_valid = False
                continue
            totals = model_usage.setdefault(model, {**{f: 0 for f in fields}, "costUSD": 0.0})
            for field in fields:
                totals[field] += detail[field]
                aggregate[field] += detail[field]
            totals["costUSD"] += detail["costUSD"]
            aggregate["costUSD"] += detail["costUSD"]
    if not keys_seen:
        model_usage_valid = False
    if not model_usage_valid:
        aggregate_report = None
    else:
        aggregate["inputTokensTotal"] = (aggregate["inputTokens"] +
                                          aggregate["cacheCreationInputTokens"] +
                                          aggregate["cacheReadInputTokens"])
        aggregate_report = aggregate

    result_usage = None
    result_usage_valid = False
    if len(results) == 1 and isinstance(results[0].get("usage"), dict):
        source = results[0]["usage"]
        names = {"input_tokens": "inputTokens", "cache_creation_input_tokens": "cacheCreationInputTokens",
                 "cache_read_input_tokens": "cacheReadInputTokens", "output_tokens": "outputTokens"}
        if all(_nonnegative_int(source.get(k)) for k in names):
            result_usage = {dest: source[key] for key, dest in names.items()}
            # API input_tokens excludes the separately reported cache categories;
            # expose the comparable total alongside those component observations.
            result_usage["inputTokens"] += (result_usage["cacheCreationInputTokens"] +
                                             result_usage["cacheReadInputTokens"])
            result_usage_valid = True

    one_success = (len(results) == 1 and results[0].get("subtype") == "success"
                   and results[0].get("is_error") is False)
    totals_match = False
    if result_usage_valid and model_usage_valid:
        totals_match = (result_usage["inputTokens"] == aggregate["inputTokensTotal"]
                        and result_usage["outputTokens"] == aggregate["outputTokens"])
    diagnostic_success = bool(one_success and model_usage_valid and result_usage_valid
                              and totals_match and permission_denials_valid and permission_denials == 0)
    return {
        "event_counts": dict(sorted(counts.items())),
        "event_count": len(events),
        "result_count": len(results),
        "assistant_count": len(assistants),
        "assistant_message_ids": [e.get("message", {}).get("id") for e in assistants
                                   if isinstance(e.get("message"), dict) and e.get("message", {}).get("id") is not None],
        "usage_bearing_event_count": usage_observation_count_total,
        "model_usage_event_count": len(model_usage_events),
        "result_usage_event_count": sum(1 for e in results if "usage" in e),
        "usage_observation_event_count": len(usage_events),
        "init_models": [e.get("model") for e in inits if e.get("model") is not None],
        "assistant_models": [e.get("message", {}).get("model") for e in assistants
                             if isinstance(e.get("message"), dict) and e.get("message", {}).get("model") is not None],
        "model_usage_keys": sorted(keys_seen),
        "model_usage": model_usage if model_usage_valid else None,
        "model_usage_totals": aggregate_report,
        "result_usage": result_usage,
        "result_usage_valid": result_usage_valid,
        "permission_denial_count": permission_denials,
        "totals_match": totals_match,
        "diagnostic_success": diagnostic_success,
        "diagnostic_pass": diagnostic_success,
        "estimated_cost_label": "Claude Code stream modelUsage costUSD observation; not verified billing",
        "complete_accounting": False,
        "product_promotion_pass": False,
        "agent_savings": "unmeasured",
        "live_cap": "unverified",
    }


def analyze(raw: bytes) -> dict:
    """Return a fail-closed report even when the captured bytes are malformed."""
    try:
        return _analyze(raw)
    except (TypeError, ValueError) as exc:
        return {
            "event_counts": {}, "event_count": 0, "result_count": 0,
            "assistant_count": 0, "assistant_message_ids": [],
            "usage_bearing_event_count": 0, "model_usage_event_count": 0,
            "result_usage_event_count": 0, "usage_observation_event_count": 0,
            "init_models": [],
            "assistant_models": [], "model_usage_keys": [],
            "model_usage": None, "model_usage_totals": None,
            "result_usage": None, "result_usage_valid": False,
            "permission_denial_count": None, "totals_match": False,
            "diagnostic_success": False, "diagnostic_pass": False,
            "parse_error": "invalid_stream_json",
            "estimated_cost_label": "Claude Code stream modelUsage costUSD observation; not verified billing",
            "complete_accounting": False, "product_promotion_pass": False,
            "agent_savings": "unmeasured", "live_cap": "unverified",
        }
