import io
import json
from unittest.mock import patch

import probe_claude_failures_v2 as probe


def fixtures(case):
    count = 2 if case == 'tool_retry' else 1
    records = []
    for i in range(1, count + 1):
        records.append({'event': 'tool_begin', 'call': i,
                        'request': {'params': {'name': 'ping', 'arguments': {}}}})
        if case != 'interrupted_tool':
            records.append({'event': 'tool_end', 'call': i,
                            'response': {'isError': case == 'tool_retry' and i == 1}})
    events = [{'type': 'system', 'subtype': 'init', 'tools': ['mcp__broker__ping']}]
    analysis = {'accounting_status': 'reported_terminal_consistent',
                'model_usage_keys': [probe.MODEL], 'assistant_models': [probe.MODEL],
                'exposed_estimated_cost': 0.001}
    execution = {'termination': 'exited', 'exit_code': 0 if case == 'tool_retry' else 1}
    if case == 'interrupted_tool':
        analysis['accounting_status'] = 'unknown'
        execution['termination'] = 'intentional_interruption_after_tool_begin'
    else:
        events.append({'type': 'result', 'subtype': 'success' if case == 'tool_retry' else 'error_max_turns',
                       'is_error': case != 'tool_retry', 'result': 'OK'})
    return execution, analysis, records, events


def test_command_is_bounded_and_subscription_compatible():
    for case in probe.CASES:
        cmd = probe.command('/claude', case, '/mcp.json')
        assert cmd[cmd.index('--model') + 1] == probe.MODEL
        assert cmd[cmd.index('--max-turns') + 1] == str(probe.LIMITS['max_turns'][case])
        assert cmd[cmd.index('--max-budget-usd') + 1] == '0.10'
        assert cmd[cmd.index('--tools') + 1] == ''
        assert '--bare' not in cmd


def test_evaluate_expected_cases():
    for case in probe.CASES:
        assert probe.evaluate(case, *fixtures(case))


def test_evaluate_rejects_unknown_usage_and_bad_requests():
    execution, analysis, records, events = fixtures('tool_retry')
    analysis['accounting_status'] = 'unknown'
    assert not probe.evaluate('tool_retry', execution, analysis, records, events)
    analysis['accounting_status'] = 'reported_terminal_consistent'
    records[0]['request']['params']['arguments'] = {'unexpected': True}
    assert not probe.evaluate('tool_retry', execution, analysis, records, events)


def test_interruption_requires_deliberate_kill_and_no_terminal():
    execution, analysis, records, events = fixtures('interrupted_tool')
    execution['termination'] = 'timeout'
    assert not probe.evaluate('interrupted_tool', execution, analysis, records, events)
    execution['termination'] = 'intentional_interruption_after_tool_begin'
    events.append({'type': 'result'})
    assert not probe.evaluate('interrupted_tool', execution, analysis, records, events)


def test_server_records_retry_error_and_success(tmp_path):
    requests = [{'jsonrpc': '2.0', 'id': i, 'method': 'tools/call',
                 'params': {'name': 'ping', 'arguments': {}}} for i in (1, 2)]
    output = io.StringIO()
    log = tmp_path / 'tool.jsonl'
    with patch('sys.stdin', io.StringIO('\n'.join(map(json.dumps, requests)))), patch('sys.stdout', output):
        probe.server('tool_retry', log)
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert [e['event'] for e in records] == ['tool_begin', 'tool_end'] * 2
    assert [e['response']['isError'] for e in records if e['event'] == 'tool_end'] == [True, False]
    assert len(output.getvalue().splitlines()) == 2


def test_execute_kills_process_group_after_marker(tmp_path):
    (tmp_path / 'blocked.json').write_text(json.dumps({'event': 'blocked_tool', 'call': 1}))
    with patch.object(probe.subprocess, 'Popen') as spawn, patch.object(probe.os, 'killpg') as kill:
        proc = spawn.return_value
        proc.poll.return_value = None
        proc.pid = 123
        proc.wait.return_value = -9
        result = probe.execute(['/claude'], tmp_path, {}, 'interrupted_tool')
    assert spawn.call_args.kwargs['start_new_session'] is True
    kill.assert_called_once_with(123, probe.signal.SIGKILL)
    assert result['termination'] == 'intentional_interruption_after_tool_begin'
    assert result['exit_code'] == -9


def test_mcp_metadata_is_not_tool_arguments():
    execution, analysis, records, events = fixtures('tool_retry')
    records[0]['request']['params']['_meta'] = {'claudecode/toolUseId': 'synthetic', 'progressToken': 2}
    assert probe.evaluate('tool_retry', execution, analysis, records, events)
    records[0]['request']['params']['unexpected'] = True
    assert not probe.evaluate('tool_retry', execution, analysis, records, events)


def test_repeated_same_assistant_model_allowed_on_interruption():
    execution, analysis, records, events = fixtures('interrupted_tool')
    analysis['assistant_models'] = [probe.MODEL, probe.MODEL]
    assert probe.evaluate('interrupted_tool', execution, analysis, records, events)
    analysis['assistant_models'].append('unexpected-model')
    assert not probe.evaluate('interrupted_tool', execution, analysis, records, events)
