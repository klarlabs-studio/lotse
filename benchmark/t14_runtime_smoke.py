"""Smoke-test the t14 runtimes on unmodified pinned snapshots, with test files that are no candidate's hidden tests.

    python benchmark/t14_runtime_smoke.py [repo ...]

As benchmark/t12_runtime_smoke.py. acorn's runner always runs its whole suite (test/run.js), so its smoke includes the
files of its candidates' hidden tests; no task or fault is involved. go-github's otel module has one test file, a
hidden test, so it is not smoke-tested. pelican was first smoked with pelican/tests/test_utils.py: 35 passed, 2
failed (test_memoized uses unittest.mock without importing it; test_regular_files_not_filtered on watchfiles'
Change members), neither in a candidate's test module; then with pelican/tests/test_readers.py. Writes .research/history/t14-runtime-smoke.json.
"""
import json
from pathlib import Path
import shutil
import sys

from t12_runtime_smoke import BASE, WORK, smoke

SMOKE = {
    "jwt": ["hmac_test.go"], "toml": ["lex_test.go"], "mvdan-sh": ["shell/split_test.go"],
    "dasel": ["model/value_test.go"], "yq": ["cmd/version_test.go"],
    "go-github": ["github/meta_test.go"], "bleve": ["analysis/lang/fr/elision_fr_test.go"],
    "kratos": ["log/level_test.go"], "gqlgen": ["graphql/int_test.go"], "ent": ["entql/types_test.go"],
    "pypdf": ["tests/test_xmp.py"], "prompt-toolkit": ["tests/test_filter.py"], "pelican": ["pelican/tests/test_readers.py"],
    "beets": ["test/test_files.py"], "robotframework": ["utest/utils/test_compat.py"],
    "sqlalchemy": ["test/typing/plain_files/sql/dml.py"],
    "qs": ["test/utils.js"], "acorn": ["test/tests-es7.js"], "chai": ["test/utilities.js"], "joi": ["test/ref.js"],
    "terser": ["test/compress/arrays.js"], "luxon": ["test/datetime/set.test.js"],
    "readability": ["test/test-isProbablyReaderable.js"], "papaparse": ["tests/node-tests.js"],
    "alasql": ["test/test042.js"],
}


def main(repos):
    out = BASE / ".research/history/t14-runtime-smoke.json"
    results = json.loads(out.read_text()) if out.is_file() else {}
    for repo in repos or SMOKE:
        results[repo] = smoke(repo, SMOKE[repo])
        print(repo, results[repo]["returncode"], results[repo]["wall_s"], flush=True)
    out.write_text(json.dumps(results, indent=1) + "\n")
    shutil.rmtree(WORK, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
