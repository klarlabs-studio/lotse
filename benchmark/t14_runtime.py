"""Batch t14 runtimes (natural_tasks_t14): Go modules, Python virtualenvs and node_modules for the repositories with
judged bugs, each built from a temporary copy of the pinned snapshot; the package code still comes from the workspace.

    python benchmark/t14_runtime.py [repo ...]

As benchmark/t12_runtime.py: one repository at a time; Python virtualenvs hold test dependencies only, on CPython
3.14.8, with requirements-frozen.txt; node_modules come from the pinned lockfile, or are resolved on the day when there
is none.
"""
from pathlib import Path
import os
import shutil
import subprocess
import sys

from t12_runtime import BASE, CORPORA, PYTHON, RUNTIME, SCRATCH, EXPORT, copy, run

GO_MODULES = {"jwt": (".",), "toml": (".",), "mvdan-sh": (".",), "dasel": (".",), "yq": (".",),
              "go-github": (".", "otel"), "bleve": (".",), "kratos": (".",), "gqlgen": (".",), "ent": (".",)}
# beets' extras without bpd, replaygain and metasync (PyGObject and dbus-python need system libraries), autobpm
# (librosa and numba) and reflink (a C extension).
BEETS_EXTRAS = ("absubmit", "aura", "beatport", "chroma", "discogs", "embedart", "embyupdate", "fetchart", "import",
                "kodiupdate", "lastgenre", "lastimport", "lyrics", "mpdstats", "plexupdate", "scrub", "sonosupdate",
                "tidal", "titlecase", "thumbnails", "web")
COMPILE = "uv pip compile -q requirements.in -o requirements.txt"
PYTHON_REQUIREMENTS = {  # Shell steps run in the temporary copy; each writes requirements.txt.
    "pypdf": f"cp requirements/ci.in requirements.in && {COMPILE}",
    "prompt-toolkit": f"printf 'wcwidth>=0.1.4\\npytest\\npytest-mock\\n' > requirements.in && {COMPILE}",
    "pelican": "uv pip compile -q pyproject.toml requirements/test.pip -o requirements.txt",
    "beets": f"uv export {EXPORT} --no-default-groups --group test -o requirements.txt"
             + "".join(f" --extra {e}" for e in BEETS_EXTRAS),
    "robotframework": f"cp utest/requirements.txt requirements.in && printf 'pytest\\n' >> requirements.in && {COMPILE}",
    "sqlalchemy": "printf 'typing-extensions>=4.6.0\\ngreenlet\\nmypy>=2.4\\ntypes-greenlet\\npytest>8,<10\\n'"
                  f" > requirements.in && {COMPILE}",
}
NPM = ("qs", "acorn", "chai", "joi", "terser", "luxon", "readability", "papaparse")  # npm install --ignore-scripts.
# acorn's lockfile hoists acorn 6.4.2 for buble, its build's only user, so acorn-loose (acorn ^8) would load it: acorn
# 6 and its plugins are nested under buble and the hoisted copy removed; the runner's NODE_PATH=. resolves acorn to
# the workspace's own package.
NESTED = {"acorn": {"buble": ("acorn", "acorn-dynamic-import", "acorn-jsx")}}
BUN = ("alasql",)
BUN_VERSION = "1.4.4"  # v140.  # Its test-only script runs bun test; installed from the pinned bun.lock.


def go(repo):
    env = {**os.environ, "GOMODCACHE": str(BASE / ".research/go-modules"), "GOCACHE": str(BASE / ".research/go-cache")}
    work = copy(repo)
    for module in GO_MODULES[repo]:
        run("go mod download all", work / module, env)


def python(repo):
    work, venv = copy(repo), RUNTIME / f"{repo}-venv"
    run(PYTHON_REQUIREMENTS[repo], work, {**os.environ, "UV_PYTHON": PYTHON})  # Resolved for the venv's Python.
    run(f"uv venv -q --clear --python {PYTHON} {venv}", work)
    run(f"uv pip install -q --python {venv}/bin/python --no-deps -r requirements.txt", work)
    frozen = subprocess.run(["uv", "pip", "freeze", "--python", str(venv / "bin/python")],
                            check=True, capture_output=True, text=True).stdout
    (venv / "requirements-frozen.txt").write_text(frozen)


def npm(repo):
    dest = RUNTIME / f"{repo}-full"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", "package-lock.json"):
        if (CORPORA / repo / name).is_file():
            shutil.copy2(CORPORA / repo / name, dest / name)
    run("npm install --ignore-scripts --legacy-peer-deps --no-audit --no-fund > npm-log.txt 2>&1", dest)
    for owner, packages in NESTED.get(repo, {}).items():
        for package in packages:
            shutil.copytree(dest / "node_modules" / package, dest / "node_modules" / owner / "node_modules" / package)
        shutil.rmtree(dest / "node_modules" / packages[0])


def bun(repo):
    dest = RUNTIME / f"{repo}-full"
    dest.mkdir(parents=True, exist_ok=True)
    for name in ("package.json", "bun.lock"):
        shutil.copy2(CORPORA / repo / name, dest / name)
    run("bun install --frozen-lockfile --ignore-scripts > bun-log.txt 2>&1", dest)
    # v140: bun 1.1.22 lacks the preload-scope beforeAll that test/bun-setup.js uses; the runner's bun is the current
    # release's binary, installed beside the packages.
    tool = RUNTIME / f"bun-{BUN_VERSION}"
    run(f"npm install --prefix {tool} --ignore-scripts --no-audit --no-fund @oven/bun-darwin-aarch64@{BUN_VERSION}"
        " > /dev/null 2>&1", dest)
    shutil.copy2(tool / "node_modules/@oven/bun-darwin-aarch64/bin/bun", dest / "node_modules/.bin/bun")


def main(repos):
    for repo in repos or [*GO_MODULES, *PYTHON_REQUIREMENTS, *NPM, *BUN]:
        step = go if repo in GO_MODULES else python if repo in PYTHON_REQUIREMENTS else npm if repo in NPM else bun
        print(f"== {repo}", flush=True)
        step(repo)
    shutil.rmtree(SCRATCH, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
