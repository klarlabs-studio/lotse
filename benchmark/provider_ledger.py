"""Durable admission ledger for experimental, unverified provider protocol work.

This ledger bounds the declared request budget for counted provider requests. It does
not establish a hard cap enforced by any live model provider.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any


class LedgerError(RuntimeError):
    """A ledger operation failed closed."""


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _bad_constant(value):
    raise ValueError(f"non-finite JSON number: {value}")


def _parse_request(body: bytes) -> dict[str, Any]:
    if not isinstance(body, bytes):
        raise ValueError("request body must be bytes")
    value = json.loads(body.decode("utf-8"), object_pairs_hook=_strict_object,
                       parse_constant=_bad_constant)
    if type(value) is not dict:
        raise ValueError("request must be a JSON object")
    return value


class ProviderLedger:
    """Exclusive JSONL journal with fsync-before-permit semantics."""

    def __init__(self, path, token_limit: int, attempt_limit: int):
        if type(token_limit) is not int or token_limit < 0:
            raise ValueError("token_limit must be a nonnegative integer")
        if type(attempt_limit) is not int or attempt_limit < 0:
            raise ValueError("attempt_limit must be a nonnegative integer")
        self.path = Path(path)
        self.token_limit = token_limit
        self.attempt_limit = attempt_limit
        self._lock = threading.RLock()
        self._poisoned = False
        self._request_count = 0
        self._denied = 0
        self._reserved = 0
        self._next_id = 1
        self._attempts = {}
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            self._fd = os.open(self.path, flags, 0o600)
        except OSError:
            raise
        try:
            os.fchmod(self._fd, 0o600)
            self._append({"event": "config", "scope": "provider_protocol_unverified",
                          "token_limit": token_limit, "attempt_limit": attempt_limit})
            directory_fd = os.open(self.path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except BaseException:
            self._poisoned = True
            os.close(self._fd)
            self._fd = None
            raise

    def _append(self, event):
        if self._poisoned:
            raise LedgerError("ledger is poisoned")
        try:
            data = (json.dumps(event, sort_keys=True, separators=(",", ":"),
                               allow_nan=False) + "\n").encode("utf-8")
            view = memoryview(data)
            while view:
                written = os.write(self._fd, view)
                if written <= 0:
                    raise OSError("short write to ledger")
                view = view[written:]
            os.fsync(self._fd)
        except BaseException as exc:
            self._poisoned = True
            raise LedgerError("ledger write/fsync failed; request cannot be forwarded") from exc

    def admit(self, request_body: bytes, input_bound: dict | None) -> int | None:
        """Durably accept a synthetic fixture request or write a denial receipt."""
        with self._lock:
            if self._poisoned:
                raise LedgerError("ledger is poisoned")
            self._request_count += 1
            raw_hash = hashlib.sha256(request_body).hexdigest() if isinstance(request_body, bytes) else None
            reason = None
            try:
                request = _parse_request(request_body)
            except (ValueError, UnicodeError, TypeError, json.JSONDecodeError) as exc:
                request = None
                reason = f"invalid_json:{exc}"
            if request is not None:
                model = request.get("model")
                output = request.get("max_output_tokens")
                if set(request) - {"model", "input", "instructions", "max_output_tokens"}:
                    reason = "unsupported_fields"
                elif not isinstance(request.get("input"), str):
                    reason = "invalid_input"
                elif "instructions" in request and not isinstance(request["instructions"], str):
                    reason = "invalid_instructions"
                elif not isinstance(model, str) or not model:
                    reason = "invalid_model"
                elif type(output) is not int or output < 16:
                    reason = "invalid_max_output_tokens"
                elif any(key in request for key in ("previous_response_id", "conversation")):
                    reason = "unsupported_stateful_request"
                elif request.get("background") is True:
                    reason = "unsupported_background_request"
                elif request.get("tools"):
                    reason = "unsupported_tools"
                elif request.get("truncation") == "auto":
                    reason = "unsupported_auto_truncation"
            bound_tokens = None
            if reason is None:
                if not isinstance(input_bound, dict):
                    reason = "missing_input_bound"
                elif (input_bound.get("source") != "openai_input_tokens"
                      or input_bound.get("request_sha256") != raw_hash
                      or input_bound.get("model") != request["model"]
                      or type(input_bound.get("input_tokens")) is not int
                      or input_bound["input_tokens"] < 0
                      or not isinstance(input_bound.get("count_response_sha256"), str)
                      or len(input_bound["count_response_sha256"]) != 64
                      or any(c not in "0123456789abcdef" for c in input_bound["count_response_sha256"])):
                    reason = "invalid_or_mismatched_input_bound"
                else:
                    bound_tokens = input_bound["input_tokens"]
            reserve = (bound_tokens + request["max_output_tokens"]
                       if reason is None else None)
            if reason is None and self._next_id - 1 >= self.attempt_limit:
                reason = "attempt_limit"
            if reason is None and self._reserved + reserve > self.token_limit:
                reason = "token_limit"
            if reason is not None:
                self._denied += 1
                self._append({"event": "denied", "request_number": self._request_count,
                              "request_sha256": raw_hash, "reason": reason})
                return None
            attempt_id = self._next_id
            event = {"event": "admitted", "attempt_id": attempt_id,
                     "request_number": self._request_count, "request_sha256": raw_hash,
                     "model": request["model"], "input_bound": bound_tokens,
                     "count_response_sha256": input_bound["count_response_sha256"],
                     "max_output_tokens": request["max_output_tokens"],
                     "reserved_tokens": reserve}
            self._append(event)
            self._next_id += 1
            self._reserved += reserve
            self._attempts[attempt_id] = {"input_bound": bound_tokens,
                                          "max_output_tokens": request["max_output_tokens"],
                                          "finished": False, "usage": None}
            return attempt_id

    def finish(self, attempt_id: int, *, outcome: str, usage: dict | None = None,
               response_sha256: str | None = None):
        allowed = {"completed", "http_error", "interrupted", "transport_error",
                   "malformed_response"}
        with self._lock:
            if self._poisoned:
                raise LedgerError("ledger is poisoned")
            if type(attempt_id) is not int or attempt_id not in self._attempts:
                raise LedgerError("unknown attempt id")
            attempt = self._attempts[attempt_id]
            if attempt["finished"]:
                raise LedgerError("attempt already finished")
            if outcome not in allowed:
                raise ValueError("unsupported outcome")
            checked = None
            invalid = None
            if outcome == "completed" or usage is not None:
                try:
                    checked = self._validate_usage(usage)
                    if checked["input_tokens"] > attempt["input_bound"]:
                        raise ValueError("observed input exceeds admitted bound")
                    if checked["output_tokens"] > attempt["max_output_tokens"]:
                        raise ValueError("observed output exceeds admitted bound")
                except (ValueError, TypeError, KeyError) as exc:
                    invalid = str(exc)
            event = {"event": "finish", "attempt_id": attempt_id, "outcome": outcome,
                     "usage": checked, "response_sha256": response_sha256}
            if invalid is not None:
                event["invalid_usage"] = invalid
                self._append(event)
                attempt["finished"] = True
                attempt["usage"] = None
                self._poisoned = True
                raise LedgerError(f"invalid usage; ledger poisoned: {invalid}")
            self._append(event)
            attempt["finished"] = True
            attempt["usage"] = checked

    @staticmethod
    def _validate_usage(usage):
        if type(usage) is not dict:
            raise ValueError("completed attempt requires usage object")
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            if type(usage.get(key)) is not int or usage[key] < 0:
                raise ValueError(f"usage {key} must be a nonnegative integer")
        if usage["total_tokens"] != usage["input_tokens"] + usage["output_tokens"]:
            raise ValueError("usage total_tokens does not equal input plus output")
        normalized = {key: usage[key] for key in
                      ("input_tokens", "output_tokens", "total_tokens")}
        for parent, child in (("input_tokens_details", "cached_tokens"),
                              ("output_tokens_details", "reasoning_tokens")):
            details = usage.get(parent)
            if details is not None:
                if type(details) is not dict:
                    raise ValueError(f"usage {parent} must be an object")
                val = details.get(child)
                if val is not None:
                    if type(val) is not int or val < 0:
                        raise ValueError(f"usage {parent}.{child} must be nonnegative integer")
                    parent_total = (normalized["input_tokens"] if parent == "input_tokens_details"
                                    else normalized["output_tokens"])
                    if val > parent_total:
                        raise ValueError(f"usage {parent}.{child} exceeds {parent_total} tokens")
                    normalized[parent] = {child: val}
        return normalized

    def summary(self):
        with self._lock:
            completed = [a for a in self._attempts.values() if a["usage"] is not None]
            return {"request_count": self._request_count,
                    "accepted_attempts": len(self._attempts),
                    "denied_requests": self._denied,
                    "reserved_tokens": self._reserved,
                    "actual_observed_tokens": sum(a["usage"]["total_tokens"] for a in completed),
                    "unknown_usage_attempts": sum(a["usage"] is None for a in self._attempts.values()),
                    "pending_attempts": sum(not a["finished"] for a in self._attempts.values()),
                    "poisoned": self._poisoned,
                    "scope": "provider_protocol_unverified",
                    "complete_agent_accounting_pass": False,
                    "complete_accounting": False,
                    "product_promotion_pass": False,
                    "live_token_cap": "real_provider_unverified",
                    "agent_savings": "unmeasured"}

    def close(self):
        with self._lock:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
