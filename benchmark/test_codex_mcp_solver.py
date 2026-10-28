import json
from pathlib import Path
import tempfile
import unittest

from codex_broker_mcp import LimitedBroker
from codex_mcp_solver import native_command, parse_native
from test_broker_mcp import FakeSession


class NativeCodexTests(unittest.TestCase):
    def test_command_requires_only_explicit_broker_and_no_json_action_schema(self):
        args = native_command({'model':'gpt-6-luna','reasoning_effort':'low','python':'/python'}, Path('/instructions'), ['broker.py'])
        self.assertNotIn('--output-schema', args)
        self.assertEqual(args[-1], '-')
        for value in ['mcp_servers.broker.required=true', 'features.code_mode_host=true', 'features.shell_tool=false', 'features.plugins=false',
                      'mcp_servers.broker.default_tools_approval_mode="approve"']:
            self.assertIn(value, args)

    def events(self, tool='read', server='broker', kind='mcp_tool_call'):
        return [{'type':'item.started','item':{'type':kind,'server':server,'tool':tool}},
                {'type':'item.completed','item':{'type':kind,'server':server,'tool':tool}},
                {'type':'turn.completed','usage':{'input_tokens':100,'output_tokens':20,'cached_input_tokens':40}}]

    def test_counts_completed_calls_once_and_retains_unknown_cost(self):
        usage,calls,error = parse_native(self.events(),'m',{'read'})
        self.assertIsNone(error)
        self.assertEqual(len(calls),1)
        self.assertEqual(usage[0]['input_tokens'],100)
        self.assertIsNone(usage[0]['cost_usd'])

    def test_unavailable_tools_or_native_access_invalidates_but_preserves_usage(self):
        for kw in [{'server':'foreign'}, {'tool':'shell'}, {'kind':'command_execution'}]:
            usage,calls,error = parse_native(self.events(**kw),'m',{'read'})
            self.assertIsNotNone(error)
            self.assertEqual(len(usage),1)
        usage,calls,error = parse_native(self.events()[:-1],'m',{'read'})
        self.assertIsNotNone(error)
        self.assertEqual(usage,[])

    def test_invalid_usage_stays_unknown_even_with_a_completed_turn(self):
        events=self.events()
        events[-1]['usage']['input_tokens']=None
        usage,calls,error=parse_native(events,'m',{'read'})
        self.assertEqual(usage,[])
        self.assertIn('Invalid provider usage',error)

    def test_broker_limit_and_closed_state_block_mutation_and_log_every_request(self):
        session=FakeSession('search_only')
        session.closed=False
        session.infrastructure_failed=False
        with tempfile.TemporaryDirectory() as tmp:
            log=Path(tmp)/'requests.jsonl'
            broker=LimitedBroker(session,1,log)
            request={'id':1,'method':'tools/call','params':{'name':'read','arguments':{'path':'source.py'}}}
            self.assertFalse(broker.handle(request)['result']['isError'])
            self.assertTrue(broker.handle(request)['result']['isError'])
            self.assertEqual(len(session.calls),1)
            session.closed=True
            broker.limit=10
            self.assertTrue(broker.handle(request)['result']['isError'])
            self.assertEqual(len(log.read_text().splitlines()),3)

    def test_finish_policy_counts_refusals_and_applies_to_every_arm(self):
        for arm in ('graf_current_orient', 'graphify_orient', 'search_only'):
            session=FakeSession(arm)
            session.closed=session.infrastructure_failed=False
            with tempfile.TemporaryDirectory() as tmp:
                log=Path(tmp)/'requests.jsonl'
                broker=LimitedBroker(session,2,log,True)
                def call(name):
                    return broker.handle({'id':1,'method':'tools/call','params':{'name':name}})['result']
                self.assertTrue(call('finish')['isError'])
                self.assertFalse(session.closed)
                self.assertEqual(session.calls,[])
                self.assertFalse(call('test')['isError'])
                self.assertTrue(call('finish')['isError'])  # Refusal consumed an action.
                records=[json.loads(l) for l in log.read_text().splitlines()]
                self.assertTrue(records[0]['policy_rejected'])
                self.assertFalse(records[0]['blocked'])
                self.assertTrue(records[2]['blocked'])

    def test_finish_requires_test_after_successful_edit_but_not_failed_edit(self):
        session=FakeSession('search_only')
        session.closed=session.infrastructure_failed=False
        original=session.call
        def response(name, args):
            if name=='edit' and args.get('succeed'):
                session.calls.append((name,args))
                return {'ok':True,'result':{'edited':True}}
            return original(name,args)
        session.call=response
        with tempfile.TemporaryDirectory() as tmp:
            broker=LimitedBroker(session,20,Path(tmp)/'log',True)
            def call(name, **args):
                return broker.handle({'id':1,'method':'tools/call','params':{'name':name,'arguments':args}})['result']
            call('test')
            call('edit')  # Failed edit preserves tested source state.
            self.assertFalse(call('finish')['isError'])
            call('edit',succeed=True)
            self.assertTrue(call('finish')['isError'])
            call('test')
            self.assertFalse(call('finish')['isError'])

    def test_optional_budget_feedback_counts_errors_refusals_and_blocked_calls(self):
        for arm in ('graf_current_orient', 'graphify_orient', 'search_only'):
            session = FakeSession(arm)
            session.closed = session.infrastructure_failed = False
            with tempfile.TemporaryDirectory() as tmp:
                log = Path(tmp) / 'log'
                broker = LimitedBroker(session, 3, log, True, True)
                def call(name):
                    return broker.handle({'id': 1, 'method': 'tools/call', 'params': {'name': name}})
                refused = call('finish')
                self.assertTrue(refused['result']['isError'])
                self.assertIn('2 of 3', refused['result']['content'][-1]['text'])
                failed = call('unknown')
                self.assertTrue(failed['result']['isError'])
                self.assertIn('1 of 3', failed['result']['content'][-1]['text'])
                last = call('test')
                self.assertIn('0 of 3', last['result']['content'][-1]['text'])
                blocked = call('read')
                self.assertTrue(blocked['result']['isError'])
                self.assertIn('0 of 3', blocked['result']['content'][-1]['text'])
                self.assertEqual(json.loads(log.read_text().splitlines()[-1])['reply'], blocked)
                self.assertEqual([name for name, _ in session.calls], ['test'])

    def test_budget_feedback_defaults_off_and_does_not_count_notifications(self):
        session = FakeSession('search_only')
        session.closed = session.infrastructure_failed = False
        with tempfile.TemporaryDirectory() as tmp:
            broker = LimitedBroker(session, 2, Path(tmp) / 'log')
            self.assertIsNone(broker.handle({'method': 'notifications/initialized'}))
            reply = broker.handle({'id': 1, 'method': 'tools/call', 'params': {'name': 'test'}})
            self.assertEqual(broker.requests, 1)
            self.assertEqual(len(reply['result']['content']), 1)

    def test_records_actual_discovery_without_consuming_actions_or_changing_replies(self):
        for arm in ('graf_current_orient', 'graphify_orient', 'search_only'):
            session = FakeSession(arm)
            session.closed = session.infrastructure_failed = False
            with tempfile.TemporaryDirectory() as tmp:
                broker = LimitedBroker(session, 2, Path(tmp) / 'requests.jsonl', True, True)
                notification = {'method': 'notifications/initialized'}
                self.assertIsNone(broker.handle(notification))
                request = {'id': 7, 'method': 'tools/list'}
                reply = broker.handle(request)
                names = [t['name'] for t in reply['result']['tools']]
                self.assertIn('edit', names)
                self.assertIn('test', names)
                self.assertIn('finish', names)
                self.assertEqual('retrieve' in names, arm != 'search_only')
                self.assertEqual('outline' in names, arm == 'graf_current_orient')
                self.assertEqual(broker.requests, 0)
                self.assertEqual(session.calls, [])
                self.assertFalse(broker.log.exists())
                records = [json.loads(line) for line in broker.control_log.read_text().splitlines()]
                self.assertEqual(records, [{'request': notification, 'reply': None},
                                           {'request': request, 'reply': reply}])


if __name__ == '__main__':
    unittest.main()
