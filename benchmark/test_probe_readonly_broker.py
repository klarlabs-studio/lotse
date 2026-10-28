import copy
import sys
from pathlib import Path

import pytest

from agent_backend import QUERY_ARGS
from probe_claude_subscription_v2 import sha
from probe_readonly_broker import CASES, fixture, request


@pytest.mark.parametrize('arm', list(QUERY_ARGS))
@pytest.mark.parametrize('case', CASES)
def test_fixture_checks_contract_without_running_backend(tmp_path, arm, case):
    # The injected runner is the only dispatcher; this executable is hashed,
    # never invoked. This is not a quality/performance measurement.
    plan = {'comparison': {'shared_output_budget': 64}, 'arms': [
        {'id': ident, 'binary': str(Path(sys.executable).resolve()),
         'binary_sha256': sha(Path(sys.executable).resolve()), 'query_args': copy.deepcopy(args)}
        for ident, args in QUERY_ARGS.items()]}
    result = fixture(plan, arm, case, tmp_path)
    assert result['diagnostic_pass'], result


def test_mcp_metadata_is_separate_from_tool_arguments():
    message = request(4, 'read', {'path': 'src/math.py'})
    assert message['params']['arguments'] == {'path': 'src/math.py'}
    assert '_meta' in message['params']
