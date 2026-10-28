"""Small local OTLP/HTTP JSON collector for Codex diagnostics.

This module intentionally uses only the Python standard library. It accepts
synthetic/local diagnostic logs and does not make model or network calls.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import json
import os
import re
from pathlib import Path
import threading
from typing import Any


MAX_BODY_BYTES = 4 * 1024 * 1024


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number: {value}")


def _decode_json_object(raw: bytes) -> dict[str, Any] | None:
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _attribute_value(attribute: Any) -> tuple[str, Any] | None:
    if not isinstance(attribute, dict) or not isinstance(attribute.get("key"), str):
        return None
    value = attribute.get("value")
    if not isinstance(value, dict):
        return None
    for field in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if field not in value:
            continue
        decoded = value[field]
        if field == "stringValue" and isinstance(decoded, str):
            return attribute["key"], decoded
        if field == "boolValue" and isinstance(decoded, bool):
            return attribute["key"], decoded
        if field == "intValue" and isinstance(decoded, (str, int)) and not isinstance(decoded, bool):
            try:
                return attribute["key"], int(decoded)
            except (ValueError, TypeError):
                return None
        if field == "doubleValue" and isinstance(decoded, (str, int, float)) and not isinstance(decoded, bool):
            try:
                number = float(decoded)
                if number == number and abs(number) != float("inf"):
                    return attribute["key"], number
            except (ValueError, TypeError):
                return None
        return None
    return None


def _attributes(items: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if not isinstance(items, list):
        return result
    for item in items:
        decoded = _attribute_value(item)
        if decoded is not None:
            result[decoded[0]] = decoded[1]
    return result


def decode_logs(payload: bytes | str | dict[str, Any]) -> list[dict[str, Any]]:
    """Decode useful OTLP JSON log fields without exposing arbitrary log text.

    Malformed structures are ignored and yield an empty/partial result.
    """
    if isinstance(payload, bytes):
        root = _decode_json_object(payload)
    elif isinstance(payload, str):
        try:
            root = _decode_json_object(payload.encode("utf-8"))
        except UnicodeEncodeError:
            root = None
    elif isinstance(payload, dict):
        root = payload
    else:
        root = None
    if not isinstance(root, dict):
        return []
    resource_logs = root.get("resourceLogs")
    if not isinstance(resource_logs, list):
        return []

    output: list[dict[str, Any]] = []
    for resource_log in resource_logs:
        if not isinstance(resource_log, dict):
            continue
        resource = resource_log.get("resource")
        resource_attrs = _attributes(resource.get("attributes")) if isinstance(resource, dict) else {}
        scopes = resource_log.get("scopeLogs")
        if not isinstance(scopes, list):
            continue
        for scope_log in scopes:
            if not isinstance(scope_log, dict):
                continue
            scope = scope_log.get("scope")
            scope_name = scope.get("name") if isinstance(scope, dict) else None
            records = scope_log.get("logRecords")
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                attrs = _attributes(record.get("attributes"))
                body = record.get("body")
                body_text = body.get("stringValue") if isinstance(body, dict) else None
                event_name = record.get("eventName")
                if not isinstance(event_name, str):
                    event_name = attrs.get("event.name")
                if (
                    not isinstance(event_name, str)
                    and isinstance(body_text, str)
                    and len(body_text) <= 256
                    and re.fullmatch(r"codex\.[A-Za-z0-9_.-]+", body_text)
                ):
                    event_name = body_text
                decoded: dict[str, Any] = {
                    "event_name": event_name,
                    "attributes": attrs,
                    "resource_attributes": resource_attrs,
                    "scope_name": scope_name if isinstance(scope_name, str) else None,
                }
                for field in ("timeUnixNano", "observedTimeUnixNano"):
                    if field in record:
                        decoded[field] = record[field]
                output.append(decoded)
    return output


class Collector(AbstractContextManager["Collector"]):
    """A loopback-only OTLP/HTTP JSON logs receiver with durable captures."""

    def __init__(self, directory: str | Path):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.directory.chmod(0o700)
        self._lock = threading.Lock()
        self._errors_lock = threading.Lock()
        self._errors: list[str] = []
        self._sequence = 0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def endpoint(self) -> str:
        if self._server is None:
            raise RuntimeError("Collector must be entered before endpoint is available")
        return f"http://127.0.0.1:{self._server.server_port}/v1/logs"

    @property
    def errors(self) -> list[str]:
        """Local persistence errors, without request bodies or payload values."""
        with self._errors_lock:
            return list(self._errors)

    def _record_error(self, message: str) -> None:
        with self._errors_lock:
            self._errors.append(message)

    def __enter__(self) -> "Collector":
        if self._server is not None:
            raise RuntimeError("Collector is already running")
        collector = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def setup(self) -> None:
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, _format: str, *args: Any) -> None:
                # Never send request metadata or payload fragments to stderr.
                return

            def _reply(self, status: int) -> None:
                body = b"{}"
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def _receipt(self, entry: dict[str, Any]) -> bool:
                try:
                    receipt_path = collector.directory / "receipts.jsonl"
                    fd = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                    os.chmod(receipt_path, 0o600)
                    with os.fdopen(fd, "a", encoding="utf-8") as stream:
                        stream.write(json.dumps(entry, sort_keys=True, allow_nan=False) + "\n")
                        stream.flush()
                    return True
                except OSError:
                    # Do not claim accepted telemetry if its audit record could
                    # not be persisted. Keep details local and payload-free.
                    collector._record_error("receipt_persist_failed")
                    return False

            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
                received = datetime.now(timezone.utc).isoformat()
                if self.path != "/v1/logs":
                    self._receipt({"received_time": received, "status": 404, "reason": "wrong_path"})
                    self._reply(404)
                    return
                content_type = self.headers.get("Content-Type", "")
                media_type = content_type.split(";", 1)[0].strip().lower()
                if media_type != "application/json":
                    self._receipt({"received_time": received, "status": 415, "reason": "content_type", "content_type": content_type})
                    self._reply(415)
                    return
                encoding = self.headers.get("Content-Encoding", "identity").strip().lower()
                if encoding not in ("", "identity"):
                    self._receipt({"received_time": received, "status": 415, "reason": "content_encoding", "content_type": content_type})
                    self._reply(415)
                    return
                raw_length = self.headers.get("Content-Length")
                try:
                    length = int(raw_length) if raw_length is not None else -1
                except ValueError:
                    length = -1
                if length < 0:
                    self._receipt({"received_time": received, "status": 400, "reason": "content_length", "content_type": content_type})
                    self._reply(400)
                    return
                if length > MAX_BODY_BYTES:
                    self._receipt({"received_time": received, "status": 413, "reason": "body_too_large", "content_type": content_type})
                    self._reply(413)
                    return
                raw = self.rfile.read(length)
                payload = _decode_json_object(raw) if len(raw) == length else None
                if payload is None or not isinstance(payload.get("resourceLogs"), list):
                    self._receipt({"received_time": received, "status": 400, "reason": "malformed_otlp_json", "content_type": content_type})
                    self._reply(400)
                    return

                with collector._lock:
                    collector._sequence += 1
                    capture_name = f"capture-{collector._sequence:04d}.json"
                    capture_path = collector.directory / capture_name
                    try:
                        fd = os.open(capture_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                        os.chmod(capture_path, 0o600)
                        with os.fdopen(fd, "wb") as stream:
                            stream.write(raw)
                            stream.flush()
                        receipt = {
                            "path": capture_name,
                            "sha256": hashlib.sha256(raw).hexdigest(),
                            "content_type": content_type,
                            "received_time": received,
                            "status": 200,
                        }
                        if not self._receipt(receipt):
                            self._reply(500)
                            return
                    except OSError:
                        collector._record_error("capture_write_failed")
                        self._receipt({"received_time": received, "status": 500, "reason": "capture_write_failed", "content_type": content_type})
                        self._reply(500)
                        return
                self._reply(200)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="codex-otel-collector", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)

