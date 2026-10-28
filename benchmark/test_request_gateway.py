"""Gateway tests use a tiny loopback HTTP fixture and never contact a provider."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
from threading import Thread
import urllib.error
import urllib.request
import unittest

from request_gateway import LocalGateway, validate_upstream
from request_ledger import Ledger


class FakeLedger:
    def __init__(self, events=None, allow=True, bounds=None):
        self.events = events if events is not None else []
        self.allow = allow
        self.bounds = bounds or {}
    def admit(self, body, bound):
        self.events.append(("admit", body, bound))
        if not self.allow or bound is None or bound != self.bounds.get(hashlib.sha256(body).hexdigest()):
            return None
        return 1
    def finish(self, attempt_id, *, outcome, usage=None, response_sha256=None):
        self.events.append(("finish", attempt_id, outcome, usage, response_sha256))
    def summary(self):
        return {}


class Fixture:
    def __init__(self, body, content_type="application/json", status=200):
        self.body, self.content_type, self.status = body, content_type, status
        self.calls = []
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args): pass
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                fixture.calls.append((self.path, raw, dict(self.headers)))
                self.send_response(fixture.status)
                self.send_header("Content-Type", fixture.content_type)
                self.send_header("Content-Length", str(len(fixture.body)))
                self.end_headers()
                self.wfile.write(fixture.body)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=2)


class RequestGatewayTests(unittest.TestCase):
    def setUp(self):
        self.request_body = json.dumps({"model": "fixture-model", "input": "fixture", "max_output_tokens": 16}, separators=(",", ":")).encode()
        self.digest = hashlib.sha256(self.request_body).hexdigest()
        self.bound = {"source": "synthetic_fixture", "request_sha256": self.digest,
                      "model": "fixture-model", "input_tokens": 4}
        self.bounds = {self.digest: self.bound}
        self.response = json.dumps({"status": "completed", "usage": {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}}).encode()
        self.fixture = Fixture(self.response)
        self.ledger = FakeLedger(bounds=self.bounds)
        self.gateway = LocalGateway(self.ledger, self.fixture.url, self.bounds).__enter__()

    def tearDown(self):
        self.gateway.__exit__(None, None, None)
        self.fixture.close()

    def post(self, body=None, headers=None, url=None):
        req = urllib.request.Request((url or self.gateway.endpoint) + ("/responses" if url is None else ""), data=self.request_body if body is None else body,
                                     headers=headers or {"Content-Type": "application/json"}, method="POST")
        try:
            return urllib.request.urlopen(req, timeout=2)
        except urllib.error.HTTPError as error:
            return error

    def test_valid_request_admits_then_finishes_before_reply_and_forwards_only_content_type(self):
        response = self.post(headers={"Content-Type": "application/json", "X-Do-Not-Forward": "secret"})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read(), self.response)
        self.assertEqual([event[0] for event in self.ledger.events], ["admit", "finish"])
        self.assertEqual(self.ledger.events[-1][2], "completed")
        self.assertEqual(self.ledger.events[-1][3]["output_tokens"], 2)
        self.assertEqual(self.fixture.calls[0][0], "/v1/responses")
        headers = {k.lower(): v for k, v in self.fixture.calls[0][2].items()}
        self.assertNotIn("x-do-not-forward", headers)
        self.assertNotIn("authorization", headers)

    def test_auth_header_is_rejected_without_admission_or_forwarding(self):
        result = self.post(headers={"Content-Type": "application/json", "Authorization": "Bearer fixture"})
        self.assertEqual(result.code, 400)
        self.assertEqual(self.ledger.events, [])
        self.assertEqual(self.fixture.calls, [])

    def test_unknown_request_and_unsupported_routes_never_reach_upstream(self):
        result = self.post(body=b'{"model":"other"}')
        self.assertEqual(result.code, 429)
        self.assertEqual(self.ledger.events[0][0], "admit")
        self.assertEqual(self.ledger.events[0][2], None)
        result = self.post(url=self.gateway.url + "/v1/chat/completions")
        self.assertEqual(result.code, 404)
        self.assertEqual(self.fixture.calls, [])

    def test_denied_admission_never_forwards(self):
        self.ledger.allow = False
        result = self.post()
        self.assertEqual(result.code, 429)
        self.assertEqual(self.fixture.calls, [])

    def test_http_error_records_unknown_usage_and_response_hash(self):
        self.fixture.status = 429
        result = self.post()
        self.assertEqual(result.code, 429)
        self.assertEqual(self.ledger.events[-1][2], "http_error")
        self.assertIsNone(self.ledger.events[-1][3])
        self.assertEqual(self.ledger.events[-1][4], hashlib.sha256(self.response).hexdigest())

    def test_duplicate_json_keys_malformed_body_and_missing_terminal_are_not_success(self):
        self.fixture.body = b'{"status":"completed","status":"completed","usage":{}}'
        self.assertEqual(self.post().code, 502)
        self.assertEqual(self.ledger.events[-1][2], "malformed_response")
        self.fixture.body = b'data: {"type":"response.output_text.delta"}\n\n'
        self.fixture.content_type = "text/event-stream"
        self.assertEqual(self.post().code, 502)
        self.assertEqual(self.ledger.events[-1][2], "interrupted")

    def test_truncated_and_repeated_sse_terminal_events_have_distinct_outcomes(self):
        self.fixture.content_type = "text/event-stream"
        terminal = b'data: {"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":4,"output_tokens":3,"total_tokens":7}}}'
        self.fixture.body = terminal
        self.assertEqual(self.post().code, 502)
        self.assertEqual(self.ledger.events[-1][2], "interrupted")
        self.fixture.body = terminal + b"\n\n" + terminal + b"\n\n"
        self.assertEqual(self.post().code, 502)
        self.assertEqual(self.ledger.events[-1][2], "malformed_response")

    def test_duplicate_content_length_is_rejected_before_admission(self):
        import socket
        host = self.gateway.url.removeprefix("http://")
        port = int(host.split(":", 1)[1])
        sock = socket.create_connection(("127.0.0.1", port), timeout=2)
        request = (b"POST /v1/responses HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                   b"Content-Type: application/json\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}")
        sock.sendall(request)
        response = sock.recv(1024)
        sock.close()
        self.assertIn(b"400", response)
        self.assertEqual(self.ledger.events, [])

    def test_completed_sse_captures_terminal_usage(self):
        self.fixture.content_type = "text/event-stream"
        self.fixture.body = b'data: {"type":"response.completed","response":{"status":"completed","usage":{"input_tokens":4,"output_tokens":3,"total_tokens":7}}}\n\n'
        self.assertEqual(self.post().code, 200)
        self.assertEqual(self.ledger.events[-1][2], "completed")
        self.assertEqual(self.ledger.events[-1][3]["output_tokens"], 3)

    def test_admission_failure_does_not_forward_and_upstream_validation_is_literal(self):
        with self.assertRaises(ValueError): validate_upstream("http://localhost:9000/v1")
        with self.assertRaises(ValueError): validate_upstream("https://127.0.0.1:9000/v1")
        with self.assertRaises(ValueError): validate_upstream("http://127.0.0.1/v1")
        with self.assertRaises(ValueError): validate_upstream("http://127.0.0.1:9000/v1/other")

    def test_malformed_request_gets_a_durable_denial_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = Path(tmp) / "events.jsonl"
            durable_ledger = Ledger(journal, token_limit=100, attempt_limit=2)
            gateway = LocalGateway(durable_ledger, self.fixture.url, {}).__enter__()
            try:
                result = self.post(body=b'{"model":', url=gateway.endpoint + "/responses")
                self.assertEqual(result.code, 429)
                events = [json.loads(line) for line in journal.read_text().splitlines()]
                denied = [item for item in events if item["event"] == "denied"]
                self.assertEqual(len(denied), 1)
                self.assertTrue(denied[0]["reason"].startswith("invalid_json:"))
                self.assertEqual(self.fixture.calls, [])
            finally:
                gateway.__exit__(None, None, None)
                durable_ledger.close()


if __name__ == "__main__":
    unittest.main()
