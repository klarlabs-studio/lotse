"""Bounded synthetic telemetry probes; never evaluates Graf or fresh tasks."""
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

BASE = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--case', choices=('plain_final', 'one_synthetic_mcp_ping'), required=True)
    args = parser.parse_args()
    plan = json.loads(args.plan.read_text())
    if plan.get('id') != 'codex-otel-probe-v1' or plan.get('scope') != 'synthetic_instrumentation_diagnostic_only':
        parser.error('This runner accepts only the frozen synthetic v1 probe')
    directory = args.plan.resolve().parent / args.case
    # Never overwrite an attempt, including one which failed before model usage.
    directory.mkdir(mode=0o700)
    cwd = directory / 'empty-cwd'
    cwd.mkdir()
    instructions = directory / 'instructions.txt'
    instructions.write_text('Follow only the synthetic probe request. Do not inspect files or use other tools.\n')
    if args.case == 'one_synthetic_mcp_ping':
        first = json.loads((directory.parent / 'plain_final' / 'receipt.json').read_text())
        if (first['exit_code'] != 0 or not first['capture_files'] or first.get('decode_errors')
                or first.get('collector_errors') or len(first.get('completed_usage', [])) != 1
                or first.get('observed_tokens', 0) > plan['token_stop_threshold_after_invocation']):
            raise ValueError('First probe did not permit a second invocation')
        prompt = 'Call the broker ping tool exactly once with no arguments. After its reply, say exactly OK.'
    else:
        prompt = 'Reply with exactly OK. Do not use any tools.'
    (directory / 'prompt.txt').write_text(prompt)
    with Collector(directory / 'telemetry') as collector:
        if args.case == 'plain_final':
            argv = command(plan['requested_model'], plan['reasoning_effort'], instructions, instructions)
            i = argv.index('--output-schema')
            del argv[i:i + 2]
        else:
            argv = native_command({'model': plan['requested_model'], 'reasoning_effort': plan['reasoning_effort'],
                                   'python': sys.executable}, instructions,
                                  [str(Path(__file__).resolve()), '--ping-server', str(directory / 'ping.jsonl')])
        settings = [
            'otel.environment="graf-synthetic-accounting-probe"',
            'otel.log_user_prompt=false',
            'otel.exporter={otlp-http={endpoint=' + json.dumps(collector.endpoint) + ',protocol="json"}}',
        ]
        argv = [*argv[:-1], *[v for setting in settings for v in ('-c', setting)], '-']
        (directory / 'command.json').write_text(json.dumps(argv, indent=2) + '\n')
        timeout = False
        with (directory / 'stream.jsonl').open('w') as out, (directory / 'stderr.txt').open('w') as err:
            process = subprocess.Popen(argv, cwd=cwd, env=environment(), stdin=subprocess.PIPE,
                                       stdout=out, stderr=err, text=True, start_new_session=True)
            try:
                process.communicate(prompt, timeout=plan['timeout_seconds_per_invocation'])
            except subprocess.TimeoutExpired:
                timeout = True
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
    captures = sorted((directory / 'telemetry').glob('capture-*.json'))
    events, decode_errors = [], []
    for path in captures:
        try:
            events.extend(decode_logs(json.loads(path.read_text())))
        except (ValueError, TypeError, KeyError) as error:
            decode_errors.append(str(error))
    stream = [json.loads(line) for line in (directory / 'stream.jsonl').read_text().splitlines() if line.strip()]
    usage = [event for event in stream if event.get('type') == 'turn.completed']
    receipt = {'case': args.case, 'plan_sha256': hashlib.sha256(args.plan.read_bytes()).hexdigest(),
               'exit_code': process.returncode, 'timeout': timeout, 'completed_usage': usage,
               'capture_files': [p.name for p in captures], 'decoded_events': len(events),
               'decode_errors': decode_errors, 'collector_errors': collector.errors,
               'product_promotion_pass': False, 'agent_savings': 'unmeasured'}
    if len(usage) == 1:
        u = usage[0].get('usage', {})
        receipt['observed_tokens'] = u.get('input_tokens', 0) + u.get('output_tokens', 0)
    (directory / 'decoded-events.json').write_text(json.dumps(events, indent=2) + '\n')
    (directory / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    # Print metadata only; raw telemetry can contain identifiers and snippets.
    print(json.dumps(receipt, indent=2))
    return 0 if process.returncode == 0 and captures and len(usage) == 1 and not decode_errors and not collector.errors else 1


def ping_server(log):
    """Fixed synthetic MCP tool; cannot access repository files or execute code."""
    for line in sys.stdin:
        message = json.loads(line)
        method = message.get('method')
        result = None
        if method == 'initialize':
            result = {'protocolVersion': message.get('params', {}).get('protocolVersion', '2024-11-05'),
                      'capabilities': {'tools': {}}, 'serverInfo': {'name': 'synthetic-ping', 'version': '1'}}
        elif method == 'tools/list':
            result = {'tools': [{'name': 'ping', 'description': 'Synthetic accounting probe. Returns pong.',
                                 'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False}}]}
        elif method == 'tools/call':
            params = message.get('params') or {}
            ok = params.get('name') == 'ping' and not params.get('arguments')
            result = {'content': [{'type': 'text', 'text': 'pong' if ok else 'Unknown synthetic request'}], 'isError': not ok}
        if 'id' in message:
            reply = {'jsonrpc': '2.0', 'id': message['id'], 'result': result}
            with Path(log).open('a') as stream:
                stream.write(json.dumps({'request': message, 'reply': reply}) + '\n')
            print(json.dumps(reply), flush=True)


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--ping-server':
        ping_server(sys.argv[2])
    else:
        raise SystemExit(main())
