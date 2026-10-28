"""Offline, bounded, durable accounting around an injected transport callable.

This records transport attempts only. It cannot establish provider token usage or
the completeness of a higher-level agent run.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading

DEFAULT_MAX_REQUEST_BYTES = 1024 * 1024
DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024
ALLOWED_PATHS = frozenset(("/v1/responses/input_tokens", "/v1/responses"))


class RecordedTransportError(RuntimeError):
    """Sanitized transport or persistence failure."""


def _strict_nonnegative_int(value, name):
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a nonnegative strict int")


class RecordedTransport:
    """Serialize calls and durably record every forwarded attempt.

    Files are numbered ``request-NNNNNN.bin``, ``begin-NNNNNN.json``,
    ``response-NNNNNN.bin`` and ``end-NNNNNN.json``. Denied requests have
    ``denied-NNNNNN.json`` receipts and do not consume the forward-attempt cap.
    """

    def __init__(self, directory, transport, attempt_limit,
                 max_request_bytes=DEFAULT_MAX_REQUEST_BYTES,
                 max_response_bytes=DEFAULT_MAX_RESPONSE_BYTES):
        _strict_nonnegative_int(attempt_limit, "attempt_limit")
        _strict_nonnegative_int(max_request_bytes, "max_request_bytes")
        _strict_nonnegative_int(max_response_bytes, "max_response_bytes")
        if not callable(transport):
            raise TypeError("transport must be callable")
        self.directory = Path(directory)
        self.transport = transport
        self.attempt_limit = attempt_limit
        self.max_request_bytes = max_request_bytes
        self.max_response_bytes = max_response_bytes
        self._lock = threading.RLock()
        self._closed = False
        self._poisoned = False
        self._attempts = self._denials = self._unknown = self._pending = 0
        # mkdir is exclusive and rejects pre-existing directories and symlinks.
        self.directory.mkdir(mode=0o700, parents=False, exist_ok=False)
        os.chmod(self.directory, 0o700)
        parent_fd = os.open(self.directory.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        self._json({"file": "config.json", "event": "config",
                    "attempt_limit": attempt_limit, "max_request_bytes": max_request_bytes,
                    "max_response_bytes": max_response_bytes})

    def _write(self, name, data):
        path = self.directory / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            view = memoryview(data)
            while view:
                n = os.write(fd, view)
                if n <= 0:
                    raise OSError("short write")
                view = view[n:]
            os.fsync(fd)
        finally:
            os.close(fd)

    def _sync_dir(self):
        fd = os.open(self.directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _json(self, data):
        self._write(data["file"], (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode())
        self._sync_dir()

    def _persist_fail(self):
        self._poisoned = True
        raise RecordedTransportError("durable accounting failed; transport is poisoned") from None

    def __call__(self, path, body):
        with self._lock:
            if self._closed:
                raise RecordedTransportError("transport is closed")
            if self._poisoned:
                raise RecordedTransportError("transport is poisoned")
            if type(path) is not str or path not in ALLOWED_PATHS:
                self._denied_next("invalid_path")
                raise ValueError("unsupported transport path")
            if type(body) is not bytes:
                self._denied_next("invalid_request")
                raise TypeError("request body must be bytes")
            self._seq = getattr(self, "_seq", 0) + 1
            seq = self._seq
            sha = hashlib.sha256(body).hexdigest()
            if len(body) > self.max_request_bytes:
                self._denied(seq, path, len(body), sha, "request_too_large")
                raise RecordedTransportError("request exceeds configured byte limit")
            if self._attempts >= self.attempt_limit:
                self._denied(seq, path, len(body), sha, "attempt_limit")
                raise RecordedTransportError("attempt limit reached")
            stem = f"{self._attempts + 1:06d}"
            try:
                self._write(f"request-{stem}.bin", body)
                self._json({"file": f"begin-{stem}.json", "event": "begin", "path": path,
                            "request_sha256": sha, "seq": self._attempts + 1})
            except BaseException:
                self._persist_fail()
            self._attempts += 1
            self._pending += 1
            try:
                result = self.transport(path, body)
            except BaseException as exc:
                self._finish_unknown(stem, path, sha, type(exc).__name__)
                if not isinstance(exc, Exception):
                    self._poisoned = True
                    raise
                raise RecordedTransportError(f"transport raised {type(exc).__name__}") from None
            try:
                if (type(result) is not tuple or len(result) != 2 or
                        type(result[0]) is not int or not 100 <= result[0] <= 599 or
                        type(result[1]) is not bytes):
                    self._end(stem, path, sha, "malformed_response", raw_saved=False)
                    self._poisoned = True
                    raise RecordedTransportError("malformed transport response; accounting unknown")
                status, response = result
                response_sha = hashlib.sha256(response).hexdigest()
                if len(response) > self.max_response_bytes:
                    self._end(stem, path, sha, "response_too_large", status=status,
                              response_sha256=response_sha, response_bytes=len(response), raw_saved=False)
                    self._poisoned = True
                    raise RecordedTransportError("response exceeds configured byte limit; accounting unknown")
                self._write(f"response-{stem}.bin", response)
                self._end(stem, path, sha, "http_error" if not 200 <= status < 300 else "response",
                          status=status, response_sha256=response_sha,
                          response_bytes=len(response), raw_saved=True)
            except RecordedTransportError:
                raise
            except BaseException:
                self._persist_fail()
            return status, response

    def _denied(self, seq, path, size, digest, reason):
        try:
            self._json({"file": f"denied-{seq:06d}.json", "event": "denied", "seq": seq,
                        "path": path, "request_bytes": size, "request_sha256": digest,
                        "reason": reason})
            self._denials += 1
        except BaseException:
            self._persist_fail()

    def _denied_next(self, reason):
        self._seq = getattr(self, "_seq", 0) + 1
        self._denied(self._seq, None, None, None, reason)

    def _end(self, stem, path, request_sha, outcome, **extra):
        try:
            self._json({"file": f"end-{stem}.json", "event": "end", "seq": int(stem),
                        "path": path, "request_sha256": request_sha, "outcome": outcome,
                        "usage": "unknown", **extra})
        except BaseException:
            self._persist_fail()
        self._pending -= 1
        self._unknown += 1

    def _finish_unknown(self, stem, path, request_sha, exception_type):
        self._end(stem, path, request_sha, "transport_exception", exception_type=exception_type)

    def summary(self):
        with self._lock:
            return {"attempts": self._attempts, "denials": self._denials,
                    "unknown_usage_attempts": self._unknown, "pending_attempts": self._pending,
                    "complete_agent_accounting": False, "live_caps_unverified": True,
                    "product_promotion": False, "poisoned": self._poisoned, "closed": self._closed}

    def close(self):
        with self._lock:
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
