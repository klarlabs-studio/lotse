"""Inventory visible saved context; never infer provider prompts or run a solver."""
import argparse
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace

from agent_trials import BASE
from broker_mcp import handle
from codex_discovery import discovery
from retrieval import tokens


def source_inventory(events):
    seen_content, seen_lines, seen_units = set(), set(), set()
    duplicate_retrievals = repeated_lines = delivered_lines = 0
    repeated_units = 0
    gate_events = []
    for number, event in enumerate(events, 1):
        result = event.get('response', {}).get('result', {})
        content = result.get('content', '')
        if not event.get('response', {}).get('ok') or not isinstance(content, str):
            continue
        if 'isFormDataLike(body) || isIterable(body) || isBlobLike(body)' in content:
            gate_events.append(number)
        if event['operation'] == 'retrieve':
            key = content
            duplicate_retrievals += key in seen_content
            seen_content.add(key)
            for line in content.splitlines():
                if re.fullmatch(r'[^:]+:\d+-\d+', line):
                    repeated_units += line in seen_units
                    seen_units.add(line)
        if event['operation'] == 'read':
            for line in content.splitlines():
                match = re.match(r'^L(\d+): (.*)$', line)
                if not match:
                    continue
                delivered_lines += 1
                key = (result.get('path'), result.get('source_hash'), int(match[1]), match[2])
                repeated_lines += key in seen_lines
                seen_lines.add(key)
    return dict(exact_duplicate_retrieval_replies=duplicate_retrievals,
                repeated_retrieved_unit_headers=repeated_units,
                delivered_read_lines=delivered_lines, repeated_delivered_read_lines=repeated_lines,
                undici_faulted_gate_text_visible_at_tool_numbers=gate_events)


def audit(report_path, spec_path):
    hashes = {}

    def read(path):
        value = path.read_bytes()
        hashes[str(path.relative_to(BASE))] = hashlib.sha256(value).hexdigest()
        return value.decode()

    report, spec = json.loads(read(report_path)), json.loads(read(spec_path))
    reconstruction_checked = False
    cases = []
    for summary in report['cases']:
        directory = BASE / '.research/trials' / summary['trial']
        call = directory / 'call-001'
        prompt = read(call / 'prompt.txt')
        instructions = read(call / 'instructions.txt')
        events = [json.loads(line) for line in read(directory / 'tool-events.jsonl').splitlines()]
        ops = [e for e in events if e.get('type') == 'tool']
        requests = [json.loads(line) for line in read(directory / 'mcp-requests.jsonl').splitlines()]
        stream = [json.loads(line) for line in read(call / 'stream.jsonl').splitlines()]
        controls = directory / 'mcp-requests.jsonl.control.jsonl'
        captured_lists, schema = [], None
        if controls.exists():
            read(controls)  # Include the actual capture in provenance hashes.
            observed = discovery(controls, summary['arm'], validate_schema=False)
            if not observed['valid']:
                raise ValueError('Invalid captured discovery: ' + '; '.join(observed['errors']))
            captured_lists = observed['captured_tool_lists']
        else:
            # Missing historical captures may be reconstructed only from frozen bytes.
            if not reconstruction_checked:
                for name in ('benchmark/broker_mcp.py', 'benchmark/task_session.py'):
                    if hashlib.sha256(read(BASE / name).encode()).hexdigest() != spec['implementation_sha256'][name]:
                        raise ValueError(f'Cannot reconstruct old schemas: {name} differs from freeze')
                reconstruction_checked = True
            schema = handle({'id': 1, 'method': 'tools/list'}, SimpleNamespace(arm=summary['arm']))['result']['tools']
        visible = dict(prompt=len(tokens(prompt)), instructions=len(tokens(instructions)),
            tool_definitions_reconstructed_canonical_json=(len(tokens(json.dumps(schema, sort_keys=True))) if schema is not None else None),
            tool_definitions_captured_canonical_json=(sum(len(tokens(json.dumps(t, sort_keys=True))) for t in captured_lists) if captured_lists else None),
            native_request_params_canonical_json=sum(len(tokens(json.dumps(row['request']['params'], sort_keys=True))) for row in requests),
            native_reply_text=sum(len(tokens(c['text'])) for row in requests
                for c in row['reply'].get('result', {}).get('content', []) if c.get('type') == 'text'))
        stats = source_inventory(ops)
        cases.append(dict(trial=summary['trial'], task=summary['task'], attempt=summary['attempt'], arm=summary['arm'],
            passed=summary['passed'], agent_called_finish=summary['agent_called_finish'],
            visible_cl100k_inventory=visible, reported_tokens=summary['audit']['total_tokens'],
            schema_source='captured_broker_reply' if captured_lists else 'frozen_reconstruction',
            tool_names_reconstructed=[t['name'] for t in schema] if schema is not None else None,
            tool_names_captured=[[t['name'] for t in tools] for tools in captured_lists] if captured_lists else None,
            actual_tools_list_captured=bool(captured_lists),
            captured_tool_lists=captured_lists,
            agent_messages=[e['item']['text'] for e in stream if e.get('type') == 'item.completed'
                and e.get('item', {}).get('type') == 'agent_message'],
            successful_edits=sum(e['operation'] == 'edit' and e['response'].get('ok', False) for e in ops),
            test_calls=sum(e['operation'] == 'test' for e in ops), **stats))
    return dict(batch=spec['id'], mode='offline_only', cases=cases,
        totals=dict(cases=len(cases),
            visible_inventory_case_counts={key:sum(c['visible_cl100k_inventory'][key] is not None for c in cases)
                for key in cases[0]['visible_cl100k_inventory']} if cases else {},
            visible_cl100k_inventory={key:(sum(c['visible_cl100k_inventory'][key] for c in cases if c['visible_cl100k_inventory'][key] is not None)
                if any(c['visible_cl100k_inventory'][key] is not None for c in cases) else None) for key in cases[0]['visible_cl100k_inventory']} if cases else {},
            exact_duplicate_retrieval_replies=sum(c['exact_duplicate_retrieval_replies'] for c in cases),
            repeated_retrieved_unit_headers=sum(c['repeated_retrieved_unit_headers'] for c in cases),
            repeated_delivered_read_lines=sum(c['repeated_delivered_read_lines'] for c in cases),
            actual_tools_list_captured=sum(c['actual_tools_list_captured'] for c in cases)),
        source_artifact_sha256=hashes,
        limitations='Visible cl100k text inventory only: no provider tokenizer, framing, hidden instructions, '
            'per-request replay or cache attribution. Fields use different serialization boundaries; do not '
            'subtract them from provider usage or interpret repeated text as measured savings. Reconstructed '
            'schemas are not proof of what the historical client received. Captured schema counts sum '
            'each broker tools/list reply; broker advertisement does not prove client consumption. Missing '
            'schema inventories remain null, and mixed-batch totals cover only available cases. Repeated unit headers need not '
            'mean unchanged source or identical surrounding text. Gate-text visibility is literal '
            'substring evidence, not proof of understanding or source-anchor recall. No performance comparison.',
        product_promotion_pass=False, agent_efficiency_acceptance_pass=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.report.resolve(), args.spec.resolve())
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['totals'], indent=2))


if __name__ == '__main__':
    main()
