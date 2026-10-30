"""Smoke-test the t12 runtimes on unmodified pinned snapshots, with test files that are no candidate's hidden tests.

    python benchmark/t12_runtime_smoke.py [repo ...]

Runs each repository's t12 runner as agent_trials.verify would (same environment and runtime links) and prints the
exit code and wall time; no task, fault or hidden test is involved. Writes .research/history/t12-runtime-smoke.json.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from mine_natural_tasks import JS_RUNTIME, PYTHON_RUNTIME, verify_command

BASE = Path(__file__).resolve().parents[1]
WORK = BASE / ".research/runtime-smoke"
SMOKE = {
    "micro": ["internal/buffer/buffer_test.go"], "go-zero": ["core/mathx/int_test.go", "tools/goctl/util/string_test.go"],
    "prometheus": ["model/relabel/relabel_test.go"],
    "arrow": ["tests/test_util.py"], "boltons": ["tests/test_strutils.py"], "more-itertools": ["tests/test_recipes.py"],
    "attrs": ["tests/test_funcs.py"], "isort": ["tests/unit/test_wrap.py"], "pendulum": ["tests/date/test_add.py"],
    "sympy": ["sympy/core/tests/test_basic.py"],
    "textual": ["tests/test_color.py", "tests/snapshot_tests/__snapshots__/test_snapshots/test_auto_width_input.svg"],
    "markitdown": ["packages/markitdown/tests/test_csv.py"],
    "mathjs": ["test/unit-tests/function/arithmetic/add.test.js"], "nunjucks": ["tests/api.js"],
    "prism": ["tests/plugins/autolinker/basic-functionality.js"], "knex": ["test/unit/schema-builder/postgres.js"],
    "browserify": ["test/args.js"],
    "cyberchef": ["tests/operations/tests/Base64.mjs", "tests/node/tests/Utils.mjs"],
    "svelte": ["packages/svelte/tests/runtime-runes/samples/action-context/_config.js"],
}


def smoke(repo, tests):
    root = WORK / repo / "workspace"
    shutil.rmtree(root.parent, ignore_errors=True)
    shutil.copytree(BASE / ".research/corpora" / repo, root,
                    ignore=shutil.ignore_patterns(".git", "node_modules", ".graf", "graphify-out", "__pycache__"))
    if repo in JS_RUNTIME:
        (root / "node_modules").symlink_to(BASE / ".research/task-runtime" / JS_RUNTIME[repo] / "node_modules",
                                           target_is_directory=True)
    venv = BASE / ".research/task-runtime" / PYTHON_RUNTIME.get(repo, "../venv")
    command = [str(venv / "bin/python") if a == "{python}" else a for a in verify_command(repo, tests)]
    env = {**os.environ, "GOCACHE": str(BASE / ".research/go-cache"), "GOMODCACHE": str(BASE / ".research/go-modules"),
           "PYTHONPATH": str(root / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    start = time.perf_counter()
    try:
        run = subprocess.run(command, cwd=root, env=env, capture_output=True, text=True, timeout=600)
        code, tail = run.returncode, (run.stdout + run.stderr)[-1500:]
    except subprocess.TimeoutExpired:
        code, tail = "timeout", ""
    shutil.rmtree(root.parent)
    return {"repo": repo, "tests": tests, "command": command, "returncode": code,
            "wall_s": round(time.perf_counter() - start, 1), "tail": tail}


def main(repos):
    out = BASE / ".research/history/t12-runtime-smoke.json"
    results = json.loads(out.read_text()) if out.is_file() else {}
    for repo in repos or SMOKE:
        results[repo] = smoke(repo, SMOKE[repo])
        print(repo, results[repo]["returncode"], results[repo]["wall_s"], flush=True)
    out.write_text(json.dumps(results, indent=1) + "\n")
    shutil.rmtree(WORK, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
