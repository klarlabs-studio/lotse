"""Match the comparison's subprocess hash seed; preserve the failed v1 run."""
import argparse
import json
import os
from pathlib import Path
import sys

from probe_readonly_broker_real import run


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    plan_path = parser.parse_args().plan.resolve()
    plan = json.loads(plan_path.read_text())
    if plan.get('subprocess_environment') != {'PYTHONHASHSEED': '0'}:
        raise ValueError('The parity run must match the comparison hash seed')
    os.environ['PYTHONHASHSEED'] = '0'
    sys.exit(run(plan_path))
