"""Experimental provider-protocol count-then-generate request pipeline.

This module has not verified provider protocol behavior or live token enforcement.
Its injected transport exists for deterministic tests; it makes no live claim.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading
from typing import Any, Callable

from provider_ledger import ProviderLedger, LedgerError, _parse_request

_MAX_BODY = 1 << 20


def _strict_int(value):
    return type(value) is int


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ProviderRequestSession:
    """Experimental, unverified provider-protocol session with injected transport."""

    def __init__(self, directory, transport: Callable[[str, bytes], tuple[int, bytes]],
                 token_limit: int, attempt_limit: int, count_limit: int):
        if not callable(transport):
            raise ValueError("transport must be callable")
        if not _strict_int(count_limit) or count_limit < 0:
            raise ValueError("count_limit must be a nonnegative integer")
        self.directory = Path(directory)
        self.directory.mkdir(mode=0o700, parents=False, exist_ok=False)
        parent_fd = os.open(self.directory.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        self.transport = transport
        self.count_limit = count_limit
        self._lock = threading.RLock()
        self._poisoned = False
        self._closed = False
        self._count_attempts = 0
        self._count_failures = 0
        self._generation = ProviderLedger(self.directory / "generation-ledger.jsonl",
                                          token_limit, attempt_limit)
        self._count_path = self.directory / "count-journal.jsonl"
        self._count_fd = None
        try:
            self._count_fd = os.open(self._count_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.fchmod(self._count_fd, 0o600)
            self._append_count({"event": "config", "scope": "provider_protocol_unverified",
                                "count_limit": count_limit})
            dfd = os.open(self.directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except BaseException:
            self._poisoned = True
            if self._count_fd is not None:
                os.close(self._count_fd)
                self._count_fd = None
            self._generation.close()
            raise

    def _append_count(self, event):
        if self._poisoned or self._count_fd is None:
            raise LedgerError("count journal is poisoned")
        try:
            view = memoryview(_json_bytes(event))
            while view:
                size = os.write(self._count_fd, view)
                if size <= 0:
                    raise OSError("short write to count journal")
                view = view[size:]
            os.fsync(self._count_fd)
        except BaseException as exc:
            self._poisoned = True
            raise LedgerError("count journal write/fsync failed; request cannot be forwarded") from exc

    def _preserve(self, name: str, body: bytes):
        """Exclusively preserve a raw payload or poison the session."""
        path = self.directory / name
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                view = memoryview(body)
                while view:
                    n = os.write(fd, view)
                    if n <= 0:
                        raise OSError("short raw payload write")
                    view = view[n:]
                os.fsync(fd)
            finally:
                os.close(fd)
        except Exception as exc:
            self._poisoned = True
            raise LedgerError("raw payload write/fsync failed; request cannot be forwarded") from exc

    def _reject(self, raw_hash, reason):
        self._append_count({"event": "rejected", "request_sha256": raw_hash,
                            "reason": reason})
        return {"outcome": "rejected", "reason": reason}

    def run(self, body: bytes) -> dict:
        with self._lock:
            if self._closed or self._poisoned or self._generation.summary()["poisoned"]:
                self._poisoned = True
                raise LedgerError("provider session is closed or poisoned")
            if not isinstance(body, bytes) or len(body) > _MAX_BODY:
                return self._reject(_digest(body) if isinstance(body, bytes) else None,
                                    "invalid_or_oversized_request")
            raw_hash = _digest(body)
            try:
                request = _parse_request(body)
            except (ValueError, UnicodeError, TypeError, json.JSONDecodeError):
                return self._reject(raw_hash, "invalid_json")
            allowed = {"model", "input", "instructions", "max_output_tokens"}
            if set(request) - allowed:
                return self._reject(raw_hash, "unsupported_fields")
            if not isinstance(request.get("model"), str) or not request["model"]:
                return self._reject(raw_hash, "invalid_model")
            if not isinstance(request.get("input"), str):
                return self._reject(raw_hash, "invalid_input")
            if "instructions" in request and not isinstance(request["instructions"], str):
                return self._reject(raw_hash, "invalid_instructions")
            output = request.get("max_output_tokens")
            if not _strict_int(output) or output < 16:
                return self._reject(raw_hash, "invalid_max_output_tokens")

            projection = {"model": request["model"], "input": request["input"]}
            if "instructions" in request:
                projection["instructions"] = request["instructions"]
            try:
                count_body = _json_bytes(projection)
            except (UnicodeError, ValueError):
                return self._reject(raw_hash, "invalid_count_projection")
            if len(count_body) > _MAX_BODY:
                return self._reject(raw_hash, "oversized_count_projection")
            if self._count_attempts >= self.count_limit:
                return self._reject(raw_hash, "count_limit")
            count_no = self._count_attempts + 1
            self._append_count({"event": "count_begin", "count_attempt": count_no,
                                "generation_sha256": raw_hash,
                                "count_request_sha256": _digest(count_body)})
            self._count_attempts += 1
            try:
                self._preserve(f"count-request-{count_no:06d}.json", count_body)
            except LedgerError:
                raise
            try:
                status, response = self.transport("/v1/responses/input_tokens", count_body)
                if type(status) is not int or not isinstance(response, bytes) or len(response) > _MAX_BODY:
                    raise ValueError("invalid count transport response")
            except Exception as exc:
                self._count_failures += 1
                self._append_count({"event": "count_end", "count_attempt": count_no,
                                    "raw_response_sha256": None,
                                    "error": type(exc).__name__})
                return {"outcome": "count_failed", "reason": type(exc).__name__}
            self._preserve(f"count-response-{count_no:06d}.json", response)
            try:
                value = json.loads(response.decode("utf-8"), object_pairs_hook=self._strict_pairs,
                                   parse_constant=self._bad_constant)
                if type(status) is not int or status != 200 or type(value) is not dict:
                    raise ValueError("count endpoint did not return HTTP 200 object")
                if value.get("object") != "response.input_tokens":
                    raise ValueError("unexpected count response object")
                count = value.get("input_tokens")
                if not _strict_int(count) or count < 0:
                    raise ValueError("invalid input_tokens")
            except Exception as exc:
                self._count_failures += 1
                self._append_count({"event": "count_end", "count_attempt": count_no,
                                    "raw_response_sha256": _digest(response) if isinstance(locals().get("response"), bytes) else None,
                                    "error": type(exc).__name__})
                return {"outcome": "count_failed", "reason": type(exc).__name__}
            self._append_count({"event": "count_end", "count_attempt": count_no,
                                "raw_response_sha256": _digest(response), "input_tokens": count})
            bound = {"source": "openai_input_tokens", "request_sha256": raw_hash,
                     "model": request["model"], "input_tokens": count,
                     "count_response_sha256": _digest(response)}
            try:
                attempt = self._generation.admit(body, bound)
            except LedgerError:
                self._poisoned = True
                raise
            if attempt is None:
                return {"outcome": "admission_denied"}
            try:
                self._preserve(f"generation-request-{attempt:06d}.json", body)
            except LedgerError:
                self._poisoned = True
                raise
            try:
                status, generation = self.transport("/v1/responses", body)
                if type(status) is not int or not isinstance(generation, bytes) or len(generation) > _MAX_BODY:
                    raise ValueError("invalid generation transport response")
            except Exception as exc:
                try:
                    self._generation.finish(attempt, outcome="transport_error")
                except LedgerError:
                    self._poisoned = True
                    raise
                return {"outcome": "transport_error", "reason": type(exc).__name__}
            try:
                self._preserve(f"generation-response-{attempt:06d}.json", generation)
            except LedgerError:
                self._poisoned = True
                raise
            response_hash = _digest(generation)
            if status != 200:
                try:
                    self._generation.finish(attempt, outcome="http_error", response_sha256=response_hash)
                except LedgerError:
                    self._poisoned = True
                    raise
                return {"outcome": "http_error"}
            try:
                result = json.loads(generation.decode("utf-8"), object_pairs_hook=self._strict_pairs,
                                    parse_constant=self._bad_constant)
                if type(result) is not dict:
                    raise ValueError("generation response must be object")
                status_name = result.get("status")
                incomplete = status_name == "incomplete"
                if status_name != "completed" and not incomplete:
                    raise ValueError("unsupported response status")
                if incomplete:
                    details = result.get("incomplete_details")
                    if type(details) is not dict or details.get("reason") != "max_output_tokens":
                        raise ValueError("unsupported incomplete reason")
                usage = result.get("usage")
                if type(usage) is not dict:
                    self._generation.finish(attempt, outcome="malformed_response",
                                            response_sha256=response_hash)
                    return {"outcome": "usage_unknown", "reason": "missing_usage"}
                normalized = {k: usage.get(k) for k in ("input_tokens", "output_tokens", "total_tokens")}
                if "input_tokens_details" in usage:
                    normalized["input_tokens_details"] = usage["input_tokens_details"]
                if "output_tokens_details" in usage:
                    normalized["output_tokens_details"] = usage["output_tokens_details"]
                self._generation.finish(attempt, outcome="interrupted" if incomplete else "completed",
                                        usage=normalized, response_sha256=response_hash)
                return {"outcome": "incomplete" if incomplete else "completed"}
            except LedgerError:
                self._poisoned = True
                raise
            except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
                self._generation.finish(attempt, outcome="malformed_response",
                                        response_sha256=response_hash)
                return {"outcome": "malformed_response"}

    @staticmethod
    def _strict_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    @staticmethod
    def _bad_constant(value):
        raise ValueError(f"non-finite JSON number: {value}")

    def summary(self):
        with self._lock:
            return {"count_attempts": self._count_attempts,
                    "count_failures": self._count_failures,
                    "generation": self._generation.summary(),
                    "poisoned": self._poisoned or self._generation.summary()["poisoned"],
                    "scope": "provider_protocol_unverified",
                    "live_provider_unverified": True,
                    "agent_savings": "unmeasured",
                    "product_promotion_pass": False,
                    "complete_accounting": False}

    def close(self):
        with self._lock:
            if not self._closed:
                if self._count_fd is not None:
                    os.close(self._count_fd)
                    self._count_fd = None
                self._generation.close()
                self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()

