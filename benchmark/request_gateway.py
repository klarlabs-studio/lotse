"""Loopback-only, single-attempt fixture gateway with durable request accounting."""
from __future__ import annotations

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import http.client
import json
import socket
from urllib.parse import urlsplit

MAX_REQUEST = 1024 * 1024
MAX_RESPONSE = 1024 * 1024
SOCKET_TIMEOUT = 5


def validate_upstream(url: str) -> tuple[str, int, str]:
    """Accept only explicit-port IPv4 loopback HTTP fixture endpoints."""
    parsed = urlsplit(url)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or
            parsed.port is None or parsed.username is not None or parsed.password is not None or
            parsed.query or parsed.fragment):
        raise ValueError("upstream must be an explicit-port http://127.0.0.1 fixture URL")
    path = parsed.path.rstrip("/")
    if path not in ("", "/v1", "/v1/responses"):
        raise ValueError("upstream path must be empty, /v1, or /v1/responses")
    target = "/v1/responses"
    return "127.0.0.1", parsed.port, target


def _pairs_no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _json_no_duplicates(data: bytes):
    return json.loads(data.decode("utf-8"), object_pairs_hook=_pairs_no_duplicates,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("invalid constant")))


def _usage_from_json(body: bytes) -> dict:
    value = _json_no_duplicates(body)
    if not isinstance(value, dict) or value.get("status") != "completed":
        raise ValueError("response is not completed")
    usage = value.get("usage")
    if not isinstance(usage, dict):
        raise ValueError("missing usage")
    return usage


def _usage_from_sse(body: bytes) -> dict:
    terminals = []
    text = body.decode("utf-8").replace("\r\n", "\n")
    blocks = text.split("\n\n")
    if blocks[-1].strip():
        raise InterruptedError("truncated SSE event")
    for block in blocks[:-1]:
        data_lines = [line[5:].lstrip(" ") for line in block.split("\n") if line.startswith("data:")]
        if not data_lines:
            continue
        data = "\n".join(data_lines)
        if data == "[DONE]":
            continue
        obj = _json_no_duplicates(data.encode("utf-8"))
        if isinstance(obj, dict) and obj.get("type") == "response.completed":
            terminals.append(obj)
    if len(terminals) > 1:
        raise ValueError("repeated terminal response.completed event")
    if not terminals:
        raise InterruptedError("missing terminal response.completed event")
    response = terminals[0].get("response")
    if not isinstance(response, dict) or response.get("status") != "completed" or not isinstance(response.get("usage"), dict):
        raise ValueError("invalid terminal completion event")
    return response["usage"]


class LocalGateway:
    def __init__(self, ledger, upstream_url: str, bounds: dict):
        self.ledger = ledger
        self.upstream_host, self.upstream_port, self.upstream_path = validate_upstream(upstream_url)
        self.bounds = bounds
        self.server = None
        self.url = None

    @property
    def endpoint(self) -> str:
        if self.url is None:
            raise RuntimeError("gateway is not running")
        return self.url + "/v1"

    def __enter__(self):
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args):
                pass

            def setup(self):
                super().setup()
                self.connection.settimeout(SOCKET_TIMEOUT)

            def _reply(self, code, message=b""):
                self.send_response(code)
                self.send_header("Content-Length", str(len(message)))
                self.send_header("Connection", "close")
                self.end_headers()
                if message:
                    try:
                        self.wfile.write(message)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

            def _reject(self, status=400):
                self.close_connection = True
                self._reply(status)

            def do_POST(self):
                if self.path != "/v1/responses":
                    return self._reject(404)
                if self.headers.get("Authorization") is not None:
                    return self._reject(400)
                if self.headers.get("Upgrade") is not None:
                    return self._reject(400)
                lengths = self.headers.get_all("Content-Length", [])
                if self.headers.get("Transfer-Encoding") is not None or not lengths:
                    return self._reject(411)
                if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdecimal():
                    return self._reject(400)
                try:
                    length = int(lengths[0])
                except (TypeError, ValueError):
                    return self._reject(400)
                if length < 0 or length > MAX_REQUEST:
                    return self._reject(413)
                body = self.rfile.read(length)
                if len(body) != length:
                    return self._reject(400)
                try:
                    request_hash = hashlib.sha256(body).hexdigest()
                    bound = gateway.bounds.get(request_hash)
                    attempt_id = gateway.ledger.admit(body, bound)
                except Exception:
                    return self._reject(503)
                if attempt_id is None:
                    return self._reject(429)

                conn = http.client.HTTPConnection(gateway.upstream_host, gateway.upstream_port, timeout=SOCKET_TIMEOUT)
                try:
                    conn.request("POST", gateway.upstream_path, body=body,
                                 headers={"Content-Type": self.headers.get("Content-Type", "application/json")})
                    response = conn.getresponse()
                    response_body = response.read(MAX_RESPONSE + 1)
                    if len(response_body) > MAX_RESPONSE:
                        gateway.ledger.finish(attempt_id, outcome="malformed_response")
                        return self._reply(502)
                    digest = hashlib.sha256(response_body).hexdigest()
                    if response.status < 200 or response.status >= 300:
                        gateway.ledger.finish(attempt_id, outcome="http_error", response_sha256=digest)
                        return self._reply(response.status, response_body)
                    content_type = (response.getheader("Content-Type") or "").split(";", 1)[0].strip().lower()
                    try:
                        if content_type == "application/json":
                            usage = _usage_from_json(response_body)
                        elif content_type == "text/event-stream":
                            usage = _usage_from_sse(response_body)
                        else:
                            raise ValueError("unsupported response content type")
                    except InterruptedError:
                        gateway.ledger.finish(attempt_id, outcome="interrupted", response_sha256=digest)
                        return self._reply(502)
                    except Exception:
                        gateway.ledger.finish(attempt_id, outcome="malformed_response", response_sha256=digest)
                        return self._reply(502)
                    gateway.ledger.finish(attempt_id, outcome="completed", usage=usage, response_sha256=digest)
                    self.send_response(response.status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(response_body)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    try:
                        self.wfile.write(response_body)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                except (OSError, http.client.HTTPException, socket.timeout):
                    try:
                        gateway.ledger.finish(attempt_id, outcome="transport_error")
                    except Exception:
                        pass
                    self._reply(502)
                except Exception:
                    # Includes durable finish failures: do not expose a response whose accounting is uncertain.
                    self._reply(503)
                finally:
                    conn.close()

            def do_GET(self): self._reject(405)
            def do_PUT(self): self._reject(405)
            def do_DELETE(self): self._reject(405)
            def do_PATCH(self): self._reject(405)
            def do_OPTIONS(self): self._reject(405)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        import threading
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self._thread.join(timeout=SOCKET_TIMEOUT)
        self.server = None
        self.url = None


@contextmanager
def gateway(ledger, upstream_url: str, bounds: dict):
    """Convenience context manager; yields a running LocalGateway."""
    with LocalGateway(ledger, upstream_url, bounds) as instance:
        yield instance
