"""Bounded synthetic HTTP provider probes; never evaluates Graf or fresh tasks."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

from codex_solver import command, environment
from codex_mcp_solver import native_command
from codex_otel import Collector, decode_logs
from probe_codex_otel import ping_server as v1_ping_server


CASES = ('plain_http', 'one_synthetic_mcp_ping_http')
REQUIRED_HASHES = {
    'benchmark/probe_codex_http.py',
    'benchmark/probe_codex_otel.py', 'benchmark/codex_solver.py',
    'benchmark/codex_mcp_solver.py', 'benchmark/codex_otel.py',
}
PROVIDER_SETTINGS = {
    'model_provider': 'graf_probe_http',
    'model_providers.graf_probe_http.name': 'OpenAI HTTP',
    'model_providers.graf_probe_http.requires_openai_auth': True,
    'model_providers.graf_probe_http.supports_websockets': False,
    'model_providers.graf_probe_http.supports_standalone_web_search': True,
    'model_providers.graf_probe_http.wire_api': 'responses',
    'model_providers.graf_probe_http.request_max_retries': 0,
    'model_providers.graf_probe_http.stream_max_retries': 0,
}
INSTRUCTIONS = 'Follow only the synthetic probe request. Do not inspect files or use other tools.\n'
PROMPTS = {
    'plain_http': 'Reply with exactly OK. Do not use any tools.',
    'one_synthetic_mcp_ping_http': 'Call the broker ping tool exactly once with no arguments. After its reply, say exactly OK.',
}


def _setting(key, value):
    return ['-c', f'{key}={json.dumps(value)}']


def build_command(case, model, effort, instructions, ping_log, collector_endpoint):
    """Build the isolated HTTP probe command, retaining existing v1 CLI controls."""
    if case not in CASES:
        raise ValueError(f'unknown probe case: {case}')
    if case == 'plain_http':
        argv = command(model, effort, instructions, instructions)
        index = argv.index('--output-schema')
        del argv[index:index + 2]
    else:
        argv = native_command({'model': model, 'reasoning_effort': effort,
                               'python': sys.executable}, instructions,
                             [str(Path(__file__).resolve()), '--ping-server', str(ping_log)])
    if argv[-1] == '-':
        argv.pop()
    settings = dict(PROVIDER_SETTINGS)
    if case == 'plain_http':
        settings['features.code_mode'] = False
    settings.update({
        'otel.environment': 'graf-synthetic-accounting-probe',
        'otel.log_user_prompt': False,
    })
    for key, value in settings.items():
        argv.extend(_setting(key, value))
    argv.extend(['-c', 'otel.exporter={otlp-http={endpoint=' + json.dumps(collector_endpoint) + ',protocol="json"}}'])
    return [*argv, '-']


def validate_plan(plan, benchmark_dir):
    if not isinstance(plan, dict) or plan.get('id') != 'codex-otel-probe-v2' or plan.get('scope') != 'synthetic_instrumentation_diagnostic_only':
        raise ValueError('This runner accepts only codex-otel-probe-v2 synthetic diagnostics')
    if plan.get('cases') != list(CASES):
        raise ValueError('v2 plan must freeze the exact case order')
    if plan.get('frozen_before_invocations') is not True:
        raise ValueError('v2 plan must be frozen before invocations')
    if plan.get('failure_policy') != 'retain_failed_attempts_no_replacement':
        raise ValueError('v2 plan must freeze the no-replacement failure policy')
    if plan.get('maximum_cli_invocations') != 2:
        raise ValueError('v2 plan must cap model calls at two')
    if plan.get('timeout_seconds_per_invocation') != 60:
        raise ValueError('v2 plan must use a 60 second invocation timeout')
    if plan.get('requested_model') != 'gpt-6-luna' or plan.get('reasoning_effort') != 'low':
        raise ValueError('v2 plan must use gpt-6-luna at low reasoning effort')
    if type(plan.get('token_stop_threshold_after_invocation')) is not int or plan['token_stop_threshold_after_invocation'] != 50000:
        raise ValueError('v2 plan must freeze a 50000 token stop threshold')
    hashes = plan.get('implementation_sha256')
    if not isinstance(hashes, dict) or not REQUIRED_HASHES.issubset(hashes):
        raise ValueError('v2 plan must pin every required implementation SHA-256')
    for name, expected in hashes.items():
        path = (benchmark_dir.parent / name).resolve()
        if not path.is_relative_to(benchmark_dir.parent.resolve()) or not path.is_file():
            raise ValueError(f'invalid implementation hash path: {name}')
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f'implementation hash changed: {name}')


def second_case_allowed(first, threshold):
    usage = first.get('completed_usage', [])
    if (first.get('exit_code') != 0 or first.get('timeout') is not False or not first.get('capture_files')
            or first.get('malformed_stream') is not False or first.get('decode_errors') != []
            or first.get('collector_errors') != [] or first.get('telemetry_usage_errors') != []
            or first.get('cli_error_items') != 0 or len(usage) != 1 or first.get('usage_errors') != []):
        return False
    event = usage[0].get('usage')
    if not isinstance(event, dict):
        return False
    input_tokens, output_tokens = event.get('input_tokens'), event.get('output_tokens')
    if any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens)):
        return False
    cached = event.get('cached_input_tokens')
    reasoning = event.get('reasoning_output_tokens')
    if type(cached) is not int or not 0 <= cached <= input_tokens:
        return False
    if reasoning is not None and (type(reasoning) is not int or not 0 <= reasoning <= output_tokens):
        return False
    total = first.get('telemetry_usage_sum')
    if not isinstance(total, dict):
        return False
    tele_input, tele_output = total.get('input_token_count'), total.get('output_token_count')
    if any(type(value) is not int or value < 0 for value in (tele_input, tele_output)):
        return False
    return tele_input + tele_output <= threshold


def telemetry_usage(events):
    """Sum all response.completed token counts; unknown or malformed counts fail closed."""
    completions = [event for event in events
                   if event.get('attributes', {}).get('event.kind') == 'response.completed'
                   and event.get('attributes', {}).get('event.name', event.get('event_name')) == 'codex.sse_event']
    if not completions:
        return None, ['missing_completion_usage']
    result = {'input_token_count': 0, 'output_token_count': 0,
              'cached_token_count': 0, 'reasoning_token_count': 0}
    for event in completions:
        attrs = event.get('attributes', {})
        values = {}
        for field in result:
            value = attrs.get(field)
            if isinstance(value, str) and value.isascii() and value.isdecimal():
                value = int(value)
            if type(value) is not int or value < 0:
                return None, ['invalid_completion_usage']
            values[field] = value
        if values['cached_token_count'] > values['input_token_count'] or values['reasoning_token_count'] > values['output_token_count']:
            return None, ['invalid_completion_usage']
        for field, value in values.items():
            result[field] += value
    return result, []


def _usage_errors(events):
    completed = [event for event in events if event.get('type') == 'turn.completed']
    if len(completed) != 1:
        return ['expected_exactly_one_turn_completed']
    usage = completed[0].get('usage')
    if not isinstance(usage, dict):
        return ['missing_usage_object']
    errors = []
    for key in ('input_tokens', 'output_tokens'):
        value = usage.get(key)
        if type(value) is not int or value < 0:
            errors.append(f'invalid_{key}')
    cached = usage.get('cached_input_tokens')
    if (type(cached) is not int or cached < 0 or
            (type(usage.get('input_tokens')) is int and cached > usage['input_tokens'])):
        errors.append('invalid_cached_input_tokens')
    reasoning = usage.get('reasoning_output_tokens')
    if reasoning is not None and (type(reasoning) is not int or reasoning < 0 or
                                  (type(usage.get('output_tokens')) is int and reasoning > usage['output_tokens'])):
        errors.append('invalid_reasoning_output_tokens')
    return errors


def _read_stream(path):
    events, malformed = [], False
    for line in path.read_text().splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            malformed = True
    return events, malformed


def execute(plan_path, case):
    plan = json.loads(plan_path.read_text())
    validate_plan(plan, Path(__file__).resolve().parent)
    directory = plan_path.resolve().parent / case
    # O_EXCL semantics via mkdir: never reuse or overwrite an attempt.
    directory.mkdir(mode=0o700)
    cwd = directory / 'empty-cwd'
    cwd.mkdir()
    instructions = directory / 'instructions.txt'
    instructions.write_text(INSTRUCTIONS)
    prompt = PROMPTS[case]
    (directory / 'prompt.txt').write_text(prompt)
    timeout = False
    process = None
    decoded, decode_errors, usage, usage_errors = [], [], [], []
    captures, collector_errors, execution_error = [], [], None
    with Collector(directory / 'telemetry') as collector:
        argv = build_command(case, plan['requested_model'], plan['reasoning_effort'],
                             str(instructions.resolve()), directory / 'ping.jsonl', collector.endpoint)
        (directory / 'command.json').write_text(json.dumps(argv, indent=2) + '\n')
        with (directory / 'stream.jsonl').open('w') as out, (directory / 'stderr.txt').open('w') as err:
            try:
                process = subprocess.Popen(argv, cwd=cwd, env=environment(), stdin=subprocess.PIPE,
                                           stdout=out, stderr=err, text=True, start_new_session=True)
                try:
                    process.communicate(prompt, timeout=plan['timeout_seconds_per_invocation'])
                except subprocess.TimeoutExpired:
                    timeout = True
            except OSError as error:
                execution_error = f'launch_failed:{type(error).__name__}'
            finally:
                if process is not None:
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait()
        collector_errors = collector.errors
    captures = sorted((directory / 'telemetry').glob('capture-*.json'))
    for path in captures:
        try:
            decoded.extend(decode_logs(json.loads(path.read_text())))
        except (ValueError, TypeError, KeyError) as error:
            decode_errors.append(type(error).__name__)
    telemetry_total, telemetry_errors = telemetry_usage(decoded)
    events, malformed = _read_stream(directory / 'stream.jsonl')
    usage = [event for event in events if event.get('type') == 'turn.completed']
    usage_errors = _usage_errors(events)
    cli_error_items = sum(event.get('item', {}).get('type') == 'error' for event in events)
    (directory / 'decoded-events.json').write_text(json.dumps(decoded, indent=2) + '\n')
    receipt = {
        'case': case, 'plan_sha256': hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        'exit_code': process.returncode if process is not None else None, 'timeout': timeout,
        'execution_error': execution_error, 'malformed_stream': malformed,
        'completed_usage': usage, 'usage_errors': usage_errors,
        'telemetry_usage_sum': telemetry_total, 'telemetry_usage_errors': telemetry_errors,
        'cli_error_items': cli_error_items,
        'capture_files': [path.name for path in captures], 'decoded_events': len(decoded),
        'decode_errors': decode_errors, 'collector_errors': collector_errors,
        'product_promotion_pass': False, 'agent_savings': 'unmeasured',
    }
    if not usage_errors and len(usage) == 1:
        receipt['observed_tokens'] = usage[0]['usage']['input_tokens'] + usage[0]['usage']['output_tokens']
    (directory / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    # Print receipt metadata only; decoded telemetry remains in its local artifact.
    print(json.dumps(receipt, indent=2))
    success = (process is not None and process.returncode == 0 and not timeout and not execution_error
               and not malformed and captures and len(usage) == 1 and not usage_errors
               and not decode_errors and not collector_errors and not telemetry_errors
               and not cli_error_items and telemetry_total is not None)
    return receipt, bool(success)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--case', choices=CASES, required=True)
    args = parser.parse_args()
    try:
        if args.case == CASES[1]:
            plan = json.loads(args.plan.read_text())
            validate_plan(plan, Path(__file__).resolve().parent)
            first_path = args.plan.resolve().parent / CASES[0] / 'receipt.json'
            first = json.loads(first_path.read_text())
            if not second_case_allowed(first, plan['token_stop_threshold_after_invocation']):
                raise ValueError('First probe did not satisfy all safety and usage gates')
        _, success = execute(args.plan, args.case)
        return 0 if success else 1
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--ping-server':
        v1_ping_server(sys.argv[2])
    else:
        raise SystemExit(main())
