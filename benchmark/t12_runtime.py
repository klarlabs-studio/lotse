"""Batch t12 runtimes (natural_tasks_t12): Go modules, Python virtualenvs and node_modules for the repositories with
judged bugs, each built from a temporary copy of the pinned snapshot; the package code still comes from the workspace.

    python benchmark/t12_runtime.py [repo ...]

Steps run one repository at a time. Python virtualenvs hold test dependencies only, on CPython 3.14.8, with
requirements-frozen.txt; node_modules come from the pinned lockfile, or are resolved on the day when there is none.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys

BASE = Path(__file__).resolve().parents[1]
CORPORA = BASE / ".research/corpora"
RUNTIME = BASE / ".research/task-runtime"
SCRATCH = BASE / ".research/runtime-build"
PYTHON = "/opt/homebrew/opt/python@3.14/bin/python3.14"  # 3.14.8, as ansible-venv (v122).
GO_MODULES = {"micro": (".",), "gitleaks": (".",), "lipgloss": (".",), "glance": (".",), "ntfy": (".",),
              "go-zero": (".", "tools/goctl"), "beego": (".",), "prometheus": (".",), "syncthing": (".",)}
EXPORT = "--frozen --no-emit-project --no-editable --no-hashes --no-header"
PYTHON_REQUIREMENTS = {  # Shell steps run in the temporary copy; each writes requirements.txt.
    "arrow": "printf 'python-dateutil\\npytest\\npytest-cov\\npytest-mock\\npytz==2025.2\\nsimplejson==3.*\\ndateparser==1.*\\n'"
             " > requirements.in && uv pip compile -q requirements.in -o requirements.txt",
    "boltons": "printf 'pytest\\n' > requirements.in && uv pip compile -q requirements.in -o requirements.txt",
    "more-itertools": "printf 'pytest\\n' > requirements.in && uv pip compile -q requirements.in -o requirements.txt",
    "attrs": f"uv export {EXPORT} --only-group tests -o requirements.txt",
    "isort": f"uv export {EXPORT} --only-group dev -o requirements.txt",
    "pendulum": "printf 'python-dateutil>=2.6\\ntzdata>=2020.1\\npytest\\ntime-machine>=3.0.0\\npytest-benchmark\\n'"
                " > requirements.in && uv pip compile -q requirements.in -o requirements.txt",
    "sympy": "printf 'mpmath\\npytest\\nhypothesis\\n' > requirements.in"
             " && uv pip compile -q requirements.in -o requirements.txt",
    "textual": f"uv export {EXPORT} --all-extras -o requirements.txt && printf 'pytest>=8.3.1,<9\\npytest-asyncio\\n"
               "pytest-textual-snapshot>=1.0.0\\n' > tests.in && uv pip compile -q tests.in -o tests.txt"
               " && cat tests.txt >> requirements.txt",
    "markitdown": "printf 'pytest\\n' > requirements.in && uv pip compile -q packages/markitdown/pyproject.toml"
                  " --extra all requirements.in -o requirements.txt",
}
NPM = ("mathjs", "nunjucks", "prism", "knex", "browserify", "cyberchef")  # npm install --ignore-scripts.
# Without a runtime: hypothesis imports its Rust extension (hypothesis._native, built by maturin) with no Python
# fallback; node-fetch's mocha 9 cannot load its yargs under node 26.
POSTINSTALL = {"cyberchef": ("src/core/config/scripts/fixCryptoApiImports.mjs",  # Its postinstall script's steps.
                             "src/core/config/scripts/fixSnackBarMarkup.mjs")}
METADATA = {"isort": ("isort", "10.0.0b2.dev27")}  # hatch-vcs at the pin: 27 commits after tag 10.0.0b1.
PNPM = ("svelte",)  # A pnpm workspace, hoisted so one node_modules serves packages/svelte.


def run(command, cwd, env=None):
    print("+", command, flush=True)
    subprocess.run(command, cwd=cwd, shell=True, check=True, env=env)


def copy(repo):
    dest = SCRATCH / repo
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(CORPORA / repo, dest, ignore=shutil.ignore_patterns(".git", "node_modules", ".graf", "graphify-out"))
    return dest


def go(repo):
    env = {**os.environ, "GOMODCACHE": str(BASE / ".research/go-modules"), "GOCACHE": str(BASE / ".research/go-cache")}
    work = copy(repo)
    for module in GO_MODULES[repo]:
        run("go mod download all", work / module, env)


def python(repo):
    work, venv = copy(repo), RUNTIME / f"{repo}-venv"
    run(PYTHON_REQUIREMENTS[repo], work, {**os.environ, "UV_PYTHON": PYTHON})  # Resolved for the venv's Python.
    run(f"uv venv -q --python {PYTHON} {venv}", work)
    run(f"uv pip install -q --python {venv}/bin/python --no-deps -r requirements.txt", work)
    frozen = subprocess.run(["uv", "pip", "freeze", "--python", str(venv / "bin/python")],
                            check=True, capture_output=True, text=True).stdout
    (venv / "requirements-frozen.txt").write_text(frozen)
    if repo in METADATA:  # Distribution metadata only, as t10's v117 repair: the code still comes from the workspace.
        name, version = METADATA[repo]
        info = venv / "lib/python3.14/site-packages" / f"{name}-{version}.dist-info"
        info.mkdir(exist_ok=True)
        (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
        (info / "INSTALLER").write_text("graf-t12-runtime\n")


def npm(repo):
    dest = RUNTIME / f"{repo}-full"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", "package-lock.json"):
        if (CORPORA / repo / name).is_file():
            shutil.copy2(CORPORA / repo / name, dest / name)
    run("npm install --ignore-scripts --legacy-peer-deps --no-audit --no-fund > npm-log.txt 2>&1", dest)
    for script in POSTINSTALL.get(repo, ()):
        run(f"node --no-warnings {CORPORA / repo / script}", dest)


def pnpm(repo):
    dest = copy(repo)
    run("pnpm install --frozen-lockfile --ignore-scripts --config.node-linker=hoisted > pnpm-log.txt 2>&1", dest)
    target = RUNTIME / f"{repo}-full"
    target.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", "pnpm-lock.yaml", "pnpm-log.txt"):
        shutil.copy2(dest / name, target / name)
    shutil.move(dest / "node_modules", target / "node_modules")


def main(repos):
    for repo in repos or [*GO_MODULES, *PYTHON_REQUIREMENTS, *NPM, *PNPM]:
        step = go if repo in GO_MODULES else python if repo in PYTHON_REQUIREMENTS else npm if repo in NPM else pnpm
        print(f"== {repo}", flush=True)
        step(repo)
    shutil.rmtree(SCRATCH, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
