"""Offline failure/context audit of saved native Codex trials; never runs solvers."""
import argparse
import hashlib
import json
from pathlib import Path

from agent_trials import BASE
from task_session import bounded


def audit(report_path):
    report = json.loads(report_path.read_text())
    cases, hashes = [], {}

    def read(path):
        raw = path.read_bytes()
        hashes[str(path.relative_to(BASE))] = hashlib.sha256(raw).hexdigest()
        return raw.decode()

    read(report_path)
    for summary in report['cases']:
        directory = BASE / '.research/trials' / summary['trial']
        saved = json.loads(read(directory / 'summary.json'))
        events = [json.loads(line) for line in read(directory / 'tool-events.jsonl').splitlines()]
        operations = [e for e in events if e.get('type') == 'tool']
        failures, reads, overlapping = [], {}, 0
        for i, event in enumerate(operations):
            response = event['response']
            if event['operation'] == 'read' and response.get('ok'):
                args = event['arguments']
                # Requested ranges only: a budget may truncate the actual reply.
                start = args.get('start', 1)
                lines = set(range(start, start + args.get('lines', 80)))
                previous = reads.setdefault(args['path'], set())
                overlapping += len(previous & lines)
                previous.update(lines)
            if event['operation'] != 'test' or response.get('result', {}).get('verification_passed') is not False:
                continue
            raw = json.loads(read(directory / f'verification-{i + 1}.json'))
            full = raw['stdout'] + raw['stderr']
            visible = response['result'].get('output', '')
            failures.append(dict(tool_number=i + 1, reply_matches_prefix_policy=visible == bounded(full, 2000),
                output_truncated=visible != full,
                connection_error_visible='unexpected connection' in visible,
                expected_argument_error_visible='UND_ERR_INVALID_ARG' in visible,
                subsequent_successful_edit=any(e['operation'] == 'edit' and e['response'].get('ok')
                    for e in operations[i + 1:])))
        cases.append(dict(trial=summary['trial'], task=summary['task'], arm=summary['arm'],
            attempt=summary['attempt'], passed=saved['passed'], agent_called_finish=saved['agent_called_finish'],
            reported_tokens=saved['audit']['total_tokens'],
            cached_input_tokens=saved['audit']['cached_input_tokens'],
            native_reply_content_tokens_cl100k=saved['native_reply_content_tokens_cl100k'],
            operations=[e['operation'] for e in operations],
            successful_edit_paths=[e['arguments']['path'] for e in operations
                if e['operation'] == 'edit' and e['response'].get('ok')],
            overlapping_requested_read_lines=overlapping, failed_tests=failures))
    return dict(batch=report['batch'], mode='offline_saved_artifacts_only', cases=cases,
        totals=dict(cases=len(cases), failed_repairs=sum(not c['passed'] for c in cases),
            missing_agent_finish=sum(not c['agent_called_finish'] for c in cases),
            failed_test_calls=sum(len(c['failed_tests']) for c in cases),
            truncated_failed_test_outputs=sum(f['output_truncated'] for c in cases for f in c['failed_tests']),
            failed_tests_without_subsequent_edit=sum(not f['subsequent_successful_edit']
                for c in cases for f in c['failed_tests'])),
        source_artifact_sha256=hashes,
        limitations='Requested read overlap is not repeated delivered text. cl100k reply sizes and provider tokens '
            'use different accounting; their difference cannot be attributed to retrieval or repeated context. '
            'Cached input is included in reported tokens. No causal effect, efficiency gain or promotion established.',
        agent_efficiency_acceptance_pass=False, product_promotion_pass=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.report.resolve())
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result['totals'], indent=2))


if __name__ == '__main__':
    main()
