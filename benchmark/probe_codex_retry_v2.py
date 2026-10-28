"""Run one half of a sequential local Codex retry/analytics diagnostic.

This only sends the Codex CLI to a loopback 503 stub. It is an instrumentation
diagnostic, not a model-success or efficiency benchmark.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import signal
import subprocess

from codex_solver import command, environment
from codex_otel import Collector, decode_logs
from probe_codex_retry import RetryStub, evaluate_retry, REQUIRED_HASHES as BASE_HASHES

BASE = Path(__file__).resolve().parent.parent
REQUIRED_HASHES = BASE_HASHES | {'benchmark/probe_codex_retry_v2.py'}
PLAN_ID = 'codex-local-retry-v2'
CASES = {'analytics_disabled': False, 'analytics_enabled': True}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_hashes(plan):
    hashes = plan.get('implementation_sha256')
    if not isinstance(hashes, dict) or not REQUIRED_HASHES.issubset(hashes):
        raise ValueError('implementation_sha256 must pin v2 runner, v1 stub, codex_solver.py, and codex_otel.py')
    checks = {}
    for name, expected in hashes.items():
        path = (BASE / name).resolve()
        checks[name] = (path.is_relative_to(BASE) and path.is_file()
                        and isinstance(expected, str) and sha256(path) == expected)
    if not all(checks.values()):
        raise ValueError('implementation hash verification failed')
    return checks


def validate_plan(plan):
    if (plan.get('id') != PLAN_ID or plan.get('scope') != 'synthetic_instrumentation_diagnostic_only'
            or plan.get('maximum_cli_invocations_per_case') != 1
            or plan.get('maximum_cli_invocations_total') != 2
            or plan.get('timeout_seconds') != 60
            or plan.get('maximum_retries') != 1
            or plan.get('expected_response_attempts') != 2
            or plan.get('frozen_before_invocations') is not True
            or plan.get('failure_policy') != 'retain_planned_conditions_no_replacements'
            or plan.get('requested_model') != 'gpt-6-luna'
            or plan.get('reasoning_effort') != 'low'
            or plan.get('cases') != ['analytics_disabled', 'analytics_enabled']):
        raise ValueError('plan does not match frozen codex-local-retry-v2 bounds')
    return verify_hashes(plan)


def settings_v2(endpoint, collector_endpoint, analytics_enabled):
    """Fixed settings with only analytics.enabled varying between cases."""
    from probe_codex_retry import settings as base_settings
    lines = base_settings(endpoint, collector_endpoint)
    return [f'analytics.enabled={str(analytics_enabled).lower()}' if line == 'analytics.enabled=false' else line
            for line in lines]


def require_previous(plan_path, plan):
    previous = plan_path.resolve().parent / 'local_retry_v2' / 'analytics_disabled' / 'receipt.json'
    if not previous.is_file():
        raise ValueError('analytics_enabled requires the analytics_disabled receipt first')
    receipt = json.loads(previous.read_text())
    if (receipt.get('id') != PLAN_ID or receipt.get('case') != 'analytics_disabled'
            or receipt.get('plan_sha256') != sha256(plan_path)
            or receipt.get('implementation_sha256') != plan.get('implementation_sha256')):
        raise ValueError('previous receipt does not match this frozen plan and implementation')
    # A failed first attempt is still a completed planned diagnostic condition.
    return receipt


def run(plan_path, case):
    plan = json.loads(plan_path.read_text())
    hashes = validate_plan(plan)
    if case not in CASES:
        raise ValueError('case must be analytics_disabled or analytics_enabled')
    previous = require_previous(plan_path, plan) if case == 'analytics_enabled' else None
    root = plan_path.resolve().parent / 'local_retry_v2'
    root.mkdir(mode=0o700, exist_ok=True)
    directory = root / case
    directory.mkdir(mode=0o700)  # EEXIST prevents overwriting prior evidence.
    cwd = directory / 'empty-cwd'
    cwd.mkdir()
    instructions = directory / 'instructions.txt'
    instructions.write_text('Follow only the synthetic retry diagnostic request. Do not inspect files or use tools.\n')
    prompt = 'Reply with exactly OK.'
    (directory / 'prompt.txt').write_text(prompt + '\n')
    outcome = {'exit_code': None, 'timeout': False, 'launch_error': None}
    events, decode_errors = [], []
    try:
        with RetryStub(directory) as stub, Collector(directory / 'telemetry') as collector:
            argv = command(plan['requested_model'], plan['reasoning_effort'], instructions, instructions)
            schema_index = argv.index('--output-schema')
            del argv[schema_index:schema_index + 2]
            argv = [*argv[:-1], *[x for setting in settings_v2(stub.endpoint, collector.endpoint, CASES[case])
                                  for x in ('-c', setting)], '-']
            (directory / 'command.json').write_text(json.dumps(argv, indent=2) + '\n')
            with (directory / 'stream.jsonl').open('w') as stdout, (directory / 'stderr.txt').open('w') as stderr:
                process = None
                try:
                    process = subprocess.Popen(argv, cwd=cwd, env=environment(), stdin=subprocess.PIPE,
                                               stdout=stdout, stderr=stderr, text=True, start_new_session=True)
                    try:
                        # Reserve up to five seconds for bounded process-group cleanup.
                        process.communicate(prompt, timeout=55)
                    except subprocess.TimeoutExpired:
                        outcome['timeout'] = True
                    finally:
                        try:
                            import os
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            import os
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
                    outcome['exit_code'] = process.returncode
                except OSError as exc:
                    outcome['launch_error'] = type(exc).__name__
        # Both receivers are shut down before freezing the in-memory receipts.
        requests = list(stub.entries)
        collector_errors = collector.errors
    except Exception as exc:
        outcome['launch_error'] = outcome['launch_error'] or type(exc).__name__
        requests, collector_errors = [], []
        receipts_path = directory / 'requests.jsonl'
        if receipts_path.exists():
            for line in receipts_path.read_text().splitlines():
                try:
                    requests.append(json.loads(line))
                except ValueError:
                    decode_errors.append('ValueError')

    # Read collector artifacts only after its context has shut down.
    captures = sorted((directory / 'telemetry').glob('capture-*.json'))
    for path in captures:
        try:
            events.extend(decode_logs(json.loads(path.read_text())))
        except (ValueError, TypeError, KeyError) as exc:
            decode_errors.append(type(exc).__name__)
    stream_events, malformed_stream = [], False
    stream_path = directory / 'stream.jsonl'
    if stream_path.exists():
        for line in stream_path.read_text().splitlines():
            try:
                stream_events.append(json.loads(line))
            except ValueError:
                malformed_stream = True
    evaluation = evaluate_retry(events, requests)
    response_receipts = [r for r in requests if r.get('method') == 'POST' and r.get('path') == '/v1/responses']
    report = {
        'id': PLAN_ID, 'case': case, 'analytics_enabled': CASES[case],
        'plan_sha256': sha256(plan_path), 'implementation_sha256': plan['implementation_sha256'],
        'implementation_hash_checks': hashes, **outcome,
        'request_receipts': requests, 'request_count': len(requests),
        'response_attempt_count': len(response_receipts),
        'auth_absent': all(r.get('auth_present') is False for r in requests),
        'stub_receipt_integrity': (len(response_receipts) == 2 and all(r.get('status') == 503 for r in response_receipts)
                                   and all(r.get('auth_present') is False for r in response_receipts)),
        **evaluation, 'completed_usage_count': sum(e.get('type') == 'turn.completed' for e in stream_events),
        'decode_errors': decode_errors, 'malformed_stream': malformed_stream,
        'collector_errors': collector_errors, 'capture_files': [p.name for p in captures],
        'stdout_event_count': len(stream_events), 'complete_agent_accounting_pass': False,
        'product_promotion_pass': False, 'agent_savings': 'unmeasured',
        'live_attempt_cap_claim': False,
        'analytics_interpretation': 'setting comparison is observational; no causal effect is established',
        'previous_case_receipt_status': ('failed_or_incomplete' if previous and not previous.get('diagnostic_observation_pass')
                                         else 'present' if previous else None),
        'scope': 'local synthetic failure-path observation only; no success inference',
    }
    (directory / 'decoded-events.json').write_text(json.dumps(events, indent=2) + '\n')
    report['diagnostic_observation_pass'] = bool(
        report['exit_code'] not in (None, 0) and not report['timeout'] and not report['launch_error']
        and bool(captures) and report['auth_absent']
        and report['stub_receipt_integrity'] and evaluation['telemetry_retry_observation_matches_receipts']
        and evaluation['telemetry_success_false'] and report['completed_usage_count'] == 0
        and not decode_errors and not collector_errors and not malformed_stream)
    (directory / 'receipt.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: report[key] for key in (
        'id', 'case', 'analytics_enabled', 'exit_code', 'timeout', 'launch_error',
        'response_attempt_count', 'telemetry_response_event_count', 'diagnostic_observation_pass',
        'complete_agent_accounting_pass', 'product_promotion_pass', 'agent_savings',
    )}, indent=2))
    return 0 if report['diagnostic_observation_pass'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--case', choices=tuple(CASES), required=True)
    args = parser.parse_args()
    try:
        return run(args.plan, args.case)
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())
