"""Frozen per-request component diagnostic; profiles never change broker checks."""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sys

import probe_readonly_session as lifecycle
from probe_readonly_broker import tokenizer_identity
from probe_snapshot_overhead import ARMS, save_exclusive
from probe_claude_subscription_v2 import sha
from readonly_session import Session
from readonly_profile import RequestProfile

BASE = Path(__file__).resolve().parents[1]


def modes(block):
    return ('plain', 'profiled') if block % 2 == 0 else ('profiled', 'plain')


@contextmanager
def session_factory(mode, captures):
    """Replace only the diagnostic's constructor, restoring it even on failure."""
    original = lifecycle.Session

    class MeasuredSession(Session):
        def __init__(self, *args, **kwargs):
            self.current_profile = None
            runner = kwargs['runner']

            def native(*argv, **options):
                if self.current_profile is None:
                    return runner(*argv, **options)
                with self.current_profile.measure('native_child'):
                    return runner(*argv, **options)

            kwargs['runner'] = native
            super().__init__(*args, **kwargs)

        def handle(self, request):
            if mode == 'plain':
                return super().handle(request)
            profile = RequestProfile()
            self.current_profile = profile
            try:
                with profile:
                    return super().handle(request)
            finally:
                self.current_profile = None
                captures.append({'request_id': request.get('id'), 'profile': profile.snapshot()})

    lifecycle.Session = MeasuredSession
    try:
        yield
    finally:
        lifecycle.Session = original


def run(path):
    plan = json.loads(path.read_text())
    for name, digest in plan['sources'].items():
        if sha(BASE / name) != digest:
            raise ValueError('source_pin_mismatch:' + name)
    if tokenizer_identity() != plan['tokenizer']:
        raise ValueError('tokenizer_mismatch')
    if sha(BASE / plan['comparison_output']) != plan['comparison_sha256']:
        raise ValueError('comparison_mismatch')
    comp = json.loads((BASE / plan['comparison_output']).read_text())
    if sha(BASE / plan['comparison_plan']) != comp['run_plan']['sha256']:
        raise ValueError('comparison_plan_mismatch')
    old = json.loads((BASE / plan['old_snapshot_plan']).read_text())
    cases = {q['id']: q for q in json.loads((BASE / plan['queries']).read_text())}
    if len(cases) != 6 or comp['repeats'] != 3:
        raise ValueError('unsupported_workload')
    evidence = BASE / plan['evidence']
    evidence.mkdir(mode=0o700, parents=True, exist_ok=False)
    groups = []
    mode_block = block = 0
    for rep in range(1, 4):
        for repo in sorted(comp['repositories']):
            pairs = sorted([r for r in comp['rows'] if r['repo'] == repo], key=lambda r: (r['id'], r['budget']))
            if len(pairs) != 4:
                raise ValueError('incomplete_repo_workload')
            for mode in modes(block):
                for arm in lifecycle.arm_order(mode_block):
                    captures = []
                    with session_factory(mode, captures):
                        group = lifecycle.run_group(plan, old, comp, cases, pairs, repo, rep, mode, arm, evidence)
                    profile = {'group': group['id'], 'mode': mode, 'requests': captures}
                    save_exclusive(evidence / group['id'] / 'profiles.json', profile)
                    groups.append({'lifecycle': group, 'profiles': profile})
                mode_block += 1
            block += 1
    passed = all(g['lifecycle']['passed'] and len(g['profiles']['requests']) ==
                 (8 if g['lifecycle']['mode'] == 'profiled' else 0) for g in groups)
    report = {'scope': 'Exposed per-request component diagnostic; observer perturbation measured, no product promotion',
              'plan_sha256': sha(path), 'comparison_sha256': plan['comparison_sha256'], 'groups': groups,
              'passed': passed, 'product_promotion_allowed': False, 'agent_savings': 'unmeasured', 'benchmark_solver_calls': 0}
    save_exclusive(BASE / plan['output'], report)
    print(json.dumps({'passed': passed, 'groups': len(groups), 'pairs': sum(len(g['lifecycle']['pairs']) for g in groups)}))
    return 0 if passed else 1


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--plan', required=True, type=Path)
    sys.exit(run(p.parse_args().plan))
