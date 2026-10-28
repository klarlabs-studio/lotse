"""Experimental pinned stdio broker operating on a private verified copy.

The private copy isolates later live-workspace writes. Read-only modes are not
an OS security boundary against an adversarial process running as the same user.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

from provider_ledger import _parse_request
from readonly_broker_v2 import Broker, source_manifest_sha256
from readonly_snapshot import Snapshot

BASE = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def artifact_pin(plan, arm_id, root, graph):
    pin = plan.get('artifacts', {}).get(arm_id)
    if not isinstance(pin, dict):
        raise ValueError('missing_artifact_pin')
    expected = Path(root).resolve(strict=True) / '.graf/index.bin' if arm_id.startswith('graf_') else Path(graph).absolute()
    if pin.get('path') != str(expected):
        raise ValueError('artifact_configuration_mismatch')
    if pin.get('source_manifest_sha256') != source_manifest_sha256(plan['source_manifest']):
        raise ValueError('artifact_manifest_binding_mismatch')
    return pin


def staged_plan(plan, arm_id, snapshot):
    result = copy.deepcopy(plan)
    result['artifacts'][arm_id]['path'] = str(snapshot.artifact_path)
    return result


def authenticate(plan):
    pins = plan.get('sources')
    required = {'benchmark/readonly_broker_stdio_v3.py', 'benchmark/readonly_snapshot.py',
                'benchmark/readonly_broker_v2.py', 'benchmark/readonly_broker.py',
                'benchmark/agent_backend.py', 'benchmark/provider_ledger.py',
                'benchmark/retrieval.py'}
    if not isinstance(pins, dict) or not required.issubset(pins):
        raise ValueError('incomplete_frozen_launcher_source_pins')
    for name, digest in pins.items():
        rel = Path(name)
        if rel.is_absolute() or any(p in ('.', '..') or p.startswith('.') for p in rel.parts):
            raise ValueError('invalid_frozen_source_path')
        current = BASE
        for part in rel.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError('symlink_frozen_source')
        if not current.is_file() or sha(current) != digest:
            raise ValueError('frozen_source_pin_mismatch')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--arm', required=True, choices=['graf_v59', 'graf_v61', 'graphify'])
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--graph', required=True, type=Path)
    parser.add_argument('--journal', required=True, type=Path)
    parser.add_argument('--snapshot-parent', required=True, type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    args = parser.parse_args(argv)
    plan = _parse_request(args.plan.read_bytes())
    authenticate(plan)
    for arm in plan.get('arms', []):
        binary = Path(arm['binary'])
        arm['binary'] = str(binary if binary.is_absolute() else BASE / binary)
    pin = artifact_pin(plan, args.arm, args.root, args.graph)
    with Snapshot(args.root, plan['source_manifest'], Path(pin['path']), pin['sha256'],
                  args.arm, args.snapshot_parent) as snapshot:
        # The receipt survives cleanup; exclusive creation prevents replacing evidence.
        with args.receipt.open('x', encoding='utf-8') as handle:
            json.dump(snapshot.receipt, handle, indent=2, allow_nan=False)
            handle.write('\n')
            handle.flush()
            import os
            os.fsync(handle.fileno())
        broker = Broker(staged_plan(plan, args.arm, snapshot), args.arm,
                        snapshot.root, snapshot.graph_path, args.journal,
                        max_actions=plan.get('max_actions', 4))
        try:
            broker.serve(sys.stdin.buffer, sys.stdout)
        finally:
            broker.close()
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (ValueError, OSError) as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__}), file=sys.stderr)
        raise SystemExit(1)
