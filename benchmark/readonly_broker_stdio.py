"""Serve the separately pinned read-only MCP contract; never invokes a solver."""
import argparse
import json
from pathlib import Path
import sys

from probe_claude_subscription_v2 import sha
from provider_ledger import _parse_request
from readonly_broker import Broker

BASE = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--arm', required=True, choices=['graf_v59', 'graf_v61', 'graphify'])
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--graph', required=True, type=Path)
    parser.add_argument('--journal', required=True, type=Path)
    args = parser.parse_args(argv)
    plan = _parse_request(args.plan.read_bytes())
    for name, digest in plan['sources'].items():
        path = (BASE / name).resolve(strict=True)
        if not path.is_relative_to(BASE) or sha(path) != digest:
            raise ValueError('source_pin_mismatch')
    for arm in plan['arms']:
        binary = Path(arm['binary'])
        arm['binary'] = str(binary if binary.is_absolute() else BASE / binary)
    broker = Broker(plan, args.arm, args.root, args.graph, args.journal,
                    max_actions=plan['max_actions'])
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
