"""Strict pinned-source stdio launcher for the experimental v2 broker."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

from provider_ledger import _parse_request
from readonly_broker_v2 import Broker

BASE = Path(__file__).resolve().parents[1]


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--arm', required=True, choices=['graf_v59', 'graf_v61', 'graphify'])
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--graph', required=True, type=Path)
    parser.add_argument('--journal', required=True, type=Path)
    args = parser.parse_args(argv)
    plan = _parse_request(args.plan.read_bytes())
    pins = plan.get('sources')
    if not isinstance(pins, dict) or not pins:
        raise ValueError('missing_frozen_launcher_sources')
    required = {'benchmark/readonly_broker_stdio_v2.py', 'benchmark/readonly_broker_v2.py',
                'benchmark/readonly_broker.py', 'benchmark/agent_backend.py',
                'benchmark/provider_ledger.py', 'benchmark/retrieval.py'}
    if not required.issubset(pins):
        raise ValueError('incomplete_frozen_launcher_source_pins')
    for name, digest in pins.items():
        rel = Path(name)
        if rel.is_absolute() or any(part in ('', '.', '..') or part.startswith('.') for part in rel.parts):
            raise ValueError('invalid_frozen_launcher_source_path')
        path = BASE / rel
        current = BASE
        for part in rel.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError('symlink_frozen_launcher_source')
        if not path.is_file() or not isinstance(digest, str) or _sha(path) != digest:
            raise ValueError('frozen_launcher_source_pin_mismatch')
    for arm in plan.get('arms', []):
        binary = Path(arm['binary'])
        arm['binary'] = str(binary if binary.is_absolute() else BASE / binary)
    broker = Broker(plan, args.arm, args.root, args.graph, args.journal,
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
