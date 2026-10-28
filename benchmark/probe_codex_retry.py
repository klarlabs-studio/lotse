"""Observe Codex HTTP retry behavior against a fixed loopback 503 stub.

This is a local instrumentation diagnostic. The stub never forwards requests,
and the synthetic response cannot complete a model turn.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

from codex_solver import command, environment
from codex_otel import Collector, decode_logs

BASE = Path(__file__).resolve().parent.parent
MAX_BODY_BYTES = 4 * 1024 * 1024
FIXED_RESPONSE = b'{"error":{"message":"Synthetic local retry diagnostic","type":"server_error"}}'
REQUIRED_HASHES = {'benchmark/probe_codex_retry.py', 'benchmark/codex_solver.py', 'benchmark/codex_otel.py'}


def request_receipt(method, path, status, body, auth_present):
    return {'method': method, 'path': path, 'status': status,
            'body_sha256': hashlib.sha256(body).hexdigest(), 'body_size': len(body),
            'auth_present': bool(auth_present),
            'received_time': datetime.now(timezone.utc).isoformat()}


class RetryStub:
    """A bounded HTTP server that only returns fixed failures and writes receipts."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.lock = threading.Lock()
        self.entries = []
        self.server = None
        self.thread = None

    @property
    def endpoint(self):
        if self.server is None:
            raise RuntimeError('stub is not running')
        return f'http://127.0.0.1:{self.server.server_port}/v1'

    def __enter__(self):
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'

            def setup(self):
                super().setup()
                self.connection.settimeout(5)

            def log_message(self, _format, *args):
                return

            def record(self, status, body=b''):
                entry = request_receipt(self.command, self.path, status, body,
                                        bool(self.headers.get('Authorization')))
                with stub.lock:
                    stub.entries.append(entry)
                    path = stub.directory / 'requests.jsonl'
                    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                    os.chmod(path, 0o600)
                    with os.fdopen(fd, 'a', encoding='utf-8') as out:
                        out.write(json.dumps(entry, sort_keys=True) + '\n')
                        out.flush()

            def reply(self, status, payload=FIXED_RESPONSE):
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(payload)))
                self.send_header('Connection', 'close')
                self.end_headers()
                try:
                    self.wfile.write(payload)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_POST(self):
                auth_present = bool(self.headers.get('Authorization'))
                raw_length = self.headers.get('Content-Length')
                try:
                    length = int(raw_length) if raw_length is not None else -1
                except ValueError:
                    length = -1
                if 0 <= length <= MAX_BODY_BYTES:
                    body = self.rfile.read(length)
                    if len(body) != length:
                        body = body[:MAX_BODY_BYTES]
                else:
                    body = b''
                status = 503
                if auth_present:
                    status = 401
                elif self.path != '/v1/responses':
                    status = 404
                elif length < 0 or length > MAX_BODY_BYTES:
                    status = 413 if length > MAX_BODY_BYTES else 400
                self.record(status, body)
                self.reply(status, FIXED_RESPONSE if status == 503 else b'{"error":"local stub"}')

            def do_GET(self):
                self.record(404)
                self.reply(404, b'{"error":"local stub"}')

            def do_PUT(self):
                self.record(404)
                self.reply(404, b'{"error":"local stub"}')

            do_DELETE = do_PUT
            do_PATCH = do_PUT

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        server, thread = self.server, self.thread
        self.server = self.thread = None
        if server:
            server.shutdown()
            server.server_close()
        if thread:
            thread.join(timeout=5)


def verify_hashes(plan):
    hashes = plan.get('implementation_sha256')
    if not isinstance(hashes, dict) or not REQUIRED_HASHES.issubset(hashes):
        raise ValueError('implementation_sha256 must include the runner, codex_solver.py, and codex_otel.py')
    checks = {}
    for name, expected in hashes.items():
        path = (BASE / name).resolve()
        if not path.is_relative_to(BASE) or not path.is_file():
            checks[name] = False
            continue
        checks[name] = hashlib.sha256(path.read_bytes()).hexdigest() == expected
    if not all(checks.values()):
        raise ValueError('implementation hash verification failed')
    return checks


def _as_status(value):
    if isinstance(value, bool):
        return None
    if type(value) is int:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        return int(value)
    return None


def _explicit_false(value):
    return value is False or (isinstance(value, str) and value.strip().lower() == 'false')


def evaluate_retry(events, receipts):
    """Validate only explicit retry evidence; missing telemetry fields stay unknown."""
    response_events = []
    for event in events:
        attrs = event.get('attributes') or {}
        event_name = attrs.get('event.name', event.get('event_name'))
        endpoint = attrs.get('endpoint')
        if event_name == 'codex.api_request' and isinstance(endpoint, str) and endpoint.rstrip('/').endswith('/responses'):
            response_events.append(attrs)
    response_receipts = [r for r in receipts if r.get('method') == 'POST' and r.get('path', '').rstrip('/').endswith('/responses')]
    attempts = [a.get('attempt') for a in response_events]
    statuses = [_as_status(a.get('http.response.status_code', a.get('status_code', a.get('status'))))
                for a in response_events]
    explicit_failures = [_explicit_false(a.get('success')) for a in response_events]
    valid_attempts = all(type(attempt) is int for attempt in attempts)
    receipt_statuses = [_as_status(r.get('status')) for r in response_receipts]
    receipts_valid = (len(response_receipts) == 2 and all(status == 503 for status in receipt_statuses)
                      and all(r.get('auth_present') is False for r in response_receipts))
    telemetry_complete = (len(response_events) == 2 and receipts_valid and valid_attempts
                          and sorted(attempts) == [0, 1]
                          and all(s == 503 for s in statuses) and all(explicit_failures))
    return {
        'telemetry_response_event_count': len(response_events),
        'response_receipt_count': len(response_receipts),
        'telemetry_attempts': attempts,
        'telemetry_statuses': statuses,
        'receipt_statuses': receipt_statuses,
        'telemetry_success_values': [a.get('success') for a in response_events],
        'telemetry_retry_observation_matches_receipts': telemetry_complete,
        'telemetry_success_false': len(response_events) == 2 and all(explicit_failures),
    }


def settings(endpoint, collector_endpoint):
    return [
        'model_provider="graf_local_retry"',
        'model_providers.graf_local_retry.name="Local synthetic retry"',
        'model_providers.graf_local_retry.base_url=' + json.dumps(endpoint),
        'model_providers.graf_local_retry.requires_openai_auth=false',
        'model_providers.graf_local_retry.supports_websockets=false',
        'model_providers.graf_local_retry.wire_api="responses"',
        'model_providers.graf_local_retry.request_max_retries=1',
        'model_providers.graf_local_retry.stream_max_retries=0',
        'model_providers.graf_local_retry.stream_idle_timeout_ms=3000',
        'features.code_mode=false',
        'analytics.enabled=false',
        'otel.metrics_exporter="none"',
        'otel.trace_exporter="none"',
        'otel.environment="graf-local-retry-diagnostic"',
        'otel.log_user_prompt=false',
        'otel.exporter={otlp-http={endpoint=' + json.dumps(collector_endpoint) + ',protocol="json"}}',
    ]


def run(plan_path):
    plan = json.loads(plan_path.read_text())
    if (plan.get('id') != 'codex-local-retry-v1' or
            plan.get('scope') != 'synthetic_instrumentation_diagnostic_only' or
            plan.get('maximum_cli_invocations') != 1 or plan.get('timeout_seconds') != 60 or
            plan.get('expected_response_attempts') != 2 or plan.get('frozen_before_invocations') is not True or
            plan.get('requested_model') != 'gpt-6-luna' or plan.get('reasoning_effort') != 'low'):
        raise ValueError('plan does not match frozen codex-local-retry-v1 bounds')
    hashes = verify_hashes(plan)
    directory = plan_path.resolve().parent / 'local_retry'
    directory.mkdir(mode=0o700)  # Existing attempts are never overwritten.
    cwd = directory / 'empty-cwd'
    cwd.mkdir()
    instructions = directory / 'instructions.txt'
    instructions.write_text('Follow only the synthetic retry diagnostic request. Do not inspect files or use tools.\n')
    prompt = 'Reply with exactly OK.'
    (directory / 'prompt.txt').write_text(prompt + '\n')
    outcome = {'exit_code': None, 'timeout': False, 'launch_error': None}
    captures, events, decode_errors = [], [], []
    with RetryStub(directory) as stub, Collector(directory / 'telemetry') as collector:
        argv = command(plan['requested_model'], plan['reasoning_effort'], instructions, instructions)
        schema_index = argv.index('--output-schema')
        del argv[schema_index:schema_index + 2]
        argv = [*argv[:-1], *[x for setting in settings(stub.endpoint, collector.endpoint)
                              for x in ('-c', setting)], '-']
        (directory / 'command.json').write_text(json.dumps(argv, indent=2) + '\n')
        with (directory / 'stream.jsonl').open('w') as stdout, (directory / 'stderr.txt').open('w') as stderr:
            process = None
            try:
                process = subprocess.Popen(argv, cwd=cwd, env=environment(), stdin=subprocess.PIPE,
                                           stdout=stdout, stderr=stderr, text=True, start_new_session=True)
                try:
                    process.communicate(prompt, timeout=60)
                except subprocess.TimeoutExpired:
                    outcome['timeout'] = True
                finally:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
                outcome['exit_code'] = process.returncode
            except OSError as exc:
                outcome['launch_error'] = type(exc).__name__
        collector_errors = collector.errors
        requests = list(stub.entries)
    # The collector is shut down before reading captures, so all accepted writes
    # and its request threads have completed before decoding.
    captures = sorted((directory / 'telemetry').glob('capture-*.json'))
    for path in captures:
        try:
            events.extend(decode_logs(json.loads(path.read_text())))
        except (ValueError, TypeError, KeyError) as exc:
            decode_errors.append(type(exc).__name__)
    stream_events = []
    malformed_stream = False
    for line in (directory / 'stream.jsonl').read_text().splitlines():
        try:
            stream_events.append(json.loads(line))
        except ValueError:
            malformed_stream = True
    retry_evaluation = evaluate_retry(events, requests)
    response_receipts = [r for r in requests if r.get('method') == 'POST' and r.get('path') == '/v1/responses']
    receipts_ok = (len(response_receipts) == 2 and
                   all(r['status'] == 503 for r in response_receipts))
    no_auth = all(not r['auth_present'] for r in requests)
    usage = [e for e in stream_events if e.get('type') == 'turn.completed']
    report = {'id': plan['id'], 'plan_sha256': hashlib.sha256(plan_path.read_bytes()).hexdigest(),
              'implementation_hash_checks': hashes, **outcome,
              'request_receipts': requests, 'response_attempt_count': len(requests),
              'unexpected_response_attempt_count': max(0, retry_evaluation['response_receipt_count'] - 2),
              'auth_absent': no_auth, 'stub_receipt_integrity': receipts_ok,
              **retry_evaluation,
              'completed_usage_count': len(usage), 'decode_errors': decode_errors,
              'malformed_stream': malformed_stream,
              'collector_errors': collector_errors, 'capture_files': [p.name for p in captures],
              'stdout_event_count': len(stream_events), 'complete_agent_accounting_pass': False,
              'product_promotion_pass': False, 'agent_savings': 'unmeasured',
              'scope': 'local synthetic failure-path observation only'}
    (directory / 'decoded-events.json').write_text(json.dumps(events, indent=2) + '\n')
    (directory / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0 if (report['exit_code'] not in (None, 0) and not report['timeout'] and no_auth and receipts_ok
                 and report['telemetry_retry_observation_matches_receipts'] and report['telemetry_success_false']
                 and not usage and not decode_errors and not collector_errors and not malformed_stream) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    try:
        return run(args.plan)
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
