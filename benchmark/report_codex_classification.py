"""Reclassify preserved synthetic telemetry without changing historical gates."""
import argparse
import hashlib
import json
from pathlib import Path

from codex_otel_classification import classify

BASE = Path(__file__).resolve().parent.parent
CASES = (
    ('codex-otel-probe-v1', 'plain_final'),
    ('codex-otel-probe-v1', 'one_synthetic_mcp_ping'),
    ('codex-otel-probe-v2', 'plain_http'),
)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def report():
    cases = {}
    for root, case in CASES:
        directory = BASE / '.research/evidence' / root / case
        events = directory / 'decoded-events.json'
        receipt_path = directory / 'receipt.json'
        receipt = json.loads(receipt_path.read_text())
        cases[f'{root}/{case}'] = {
            **classify(json.loads(events.read_text())),
            'cli_exit_code': receipt['exit_code'], 'timeout': receipt['timeout'],
            'capture_count': len(receipt['capture_files']),
            'collector_error_count': len(receipt['collector_errors']),
            'decode_error_count': len(receipt['decode_errors']),
            'evidence_sha256': {str(p.relative_to(BASE)): sha(p)
                                for p in sorted(directory.rglob('*')) if p.is_file()},
        }
    return {
        'id': 'codex-preserved-telemetry-classification-v1',
        'scope': 'post-hoc diagnostic; historical failed gates remain unchanged',
        'cases': cases,
        'analysis_sha256': {name: sha(BASE / name) for name in (
            'benchmark/codex_otel_classification.py',
            'benchmark/test_codex_otel_classification.py',
            'benchmark/report_codex_classification.py')},
        'complete_agent_accounting_pass': False, 'product_promotion_pass': False,
        'live_token_cap': 'unverified', 'agent_savings': 'unmeasured',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = report()
    with args.output.open('x') as out:
        out.write(json.dumps(result, indent=2) + '\n')
    print(json.dumps({'id': result['id'], 'complete_agent_accounting_pass': False}))
    return 1  # This diagnostic cannot establish complete accounting.


if __name__ == '__main__':
    raise SystemExit(main())
