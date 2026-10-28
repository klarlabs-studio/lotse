"""Bounded broker for the separately frozen native Codex MCP diagnostic."""
import argparse
import json
from pathlib import Path
import sys

from agent_trials import BASE, tasks
from broker_mcp import handle, _text
import task_session


class LimitedBroker:
    def __init__(self, session, limit, log, require_test_before_finish=False, show_action_budget=False):
        self.session, self.limit, self.log = session, limit, log
        self.requests = 0
        self.require_test_before_finish = require_test_before_finish
        self.tested = False
        self.show_action_budget = show_action_budget
        self.control_log = log.with_name(log.name + '.control.jsonl')

    def handle(self, message):
        if message.get('method') != 'tools/call' or 'id' not in message:
            reply = handle(message, self.session)
            # Preserve the actual startup/tool discovery exchange, rather than
            # inferring advertised tools later from a potentially changed schema.
            # These are protocol messages, not solver actions or tool payloads.
            with self.control_log.open('a') as stream:
                stream.write(json.dumps({'request': message, 'reply': reply}) + '\n')
            return reply
        self.requests += 1
        blocked = self.requests > self.limit or self.session.closed or self.session.infrastructure_failed
        name = (message.get('params') or {}).get('name')
        policy_rejected = not blocked and self.require_test_before_finish and name == 'finish' and not self.tested
        if blocked:
            reply = {'jsonrpc': '2.0', 'id': message['id'], 'result': _text(
                'Attempt closed or broker action limit reached. Return your final answer now.', True)}
        elif policy_rejected:
            reply = {'jsonrpc': '2.0', 'id': message['id'], 'result': _text(
                'Finish refused: call test after your latest successful edit (or before finishing without edits). '
                'Inspect failures and continue repairing while resources remain. This request counts toward your action limit.', True)}
        else:
            reply = handle(message, self.session)
            if not reply.get('result', {}).get('isError', True):
                if name == 'edit':
                    self.tested = False
                elif name == 'test' and not self.session.infrastructure_failed:
                    self.tested = True
        if self.show_action_budget:
            # Count refusals and failures too. Append to the actual MCP reply so
            # native payload accounting and the independent request log include it.
            remaining = max(0, self.limit - self.requests)
            status = f'Broker budget: {remaining} of {self.limit} tool calls remain; failures and refusals count.'
            if blocked or self.session.closed or self.session.infrastructure_failed or not remaining:
                status += ' Return your final answer without further tools.'
            elif remaining <= 3:
                status += ' Reserve calls for test after any edit and finish; a failed test remains a failed repair.'
            reply['result']['content'].append({'type': 'text', 'text': status})
        with self.log.open('a') as stream:
            stream.write(json.dumps({'request': message, 'reply': reply, 'blocked': blocked,
                                     'policy_rejected': policy_rejected}) + '\n')
        return reply


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('task')
    parser.add_argument('trial_id')
    parser.add_argument('--arm', required=True, choices=task_session.ARMS)
    parser.add_argument('--catalog', type=Path, required=True)
    parser.add_argument('--graf-binary', type=Path, required=True)
    parser.add_argument('--graphify-adapter', choices=task_session.GRAPHIFY_ADAPTERS)
    parser.add_argument('--max-actions', type=int, required=True)
    parser.add_argument('--require-test-before-finish', action='store_true')
    parser.add_argument('--show-action-budget', action='store_true')
    args = parser.parse_args()
    if args.max_actions < 1:
        parser.error('max-actions must be positive')
    task_session.GRAF_T6 = args.graf_binary
    task = next(t for t in tasks(args.catalog) if t['id'] == args.task)
    session = task_session.Session(task, args.trial_id, args.arm, args.graphify_adapter)
    broker = LimitedBroker(session, args.max_actions, session.directory / 'mcp-requests.jsonl',
                           args.require_test_before_finish, args.show_action_budget)
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            reply = broker.handle(json.loads(line))
        except Exception as exc:
            reply = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': str(exc)}}
        if reply is not None:
            print(json.dumps(reply, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
