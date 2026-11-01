"""Batch t15 runtimes (natural_tasks_t15): Go modules, Python virtualenvs and node_modules for the repositories with
judged bugs and a runner, each built from a temporary copy of the pinned snapshot; the package code still comes from
the workspace.

    python benchmark/t15_runtime.py [repo ...]

As benchmark/t14_runtime.py: one repository at a time; Python virtualenvs hold test dependencies only, on CPython
3.14.8, with requirements-frozen.txt; node_modules come from the pinned lockfile, or are resolved on the day when npm
cannot read it (pdfkit's Yarn 4 lockfile) or there is none (node-semver).
"""
import sys
import tomllib

from t12_runtime import BASE, CORPORA, EXPORT, RUNTIME, SCRATCH, go as go_step, npm as npm_step, pnpm as pnpm_step, python as python_step
import t12_runtime
import shutil

GO_MODULES = {"env": (".",), "carbon": (".",), "lancet": (".",), "gocron": (".",), "revive": (".",), "fq": (".",),
              "oapi-codegen": (".", "internal/test"), "go-git": (".",), "compress": (".",), "vale": (".",),
              "go-swagger": (".",), "dns": (".",)}
COMPILE = "uv pip compile -q requirements.in -o requirements.txt"
PYTHON_REQUIREMENTS = {  # Shell steps run in the temporary copy; each writes requirements.txt.
    "tenacity": f"printf 'pytest\\ntornado>=6.0\\ntrio\\n' > requirements.in && {COMPILE}",  # Its test extra, plus trio.
    "sqlglot": f"printf 'pytest\\nduckdb>=0.6\\npandas\\npython-dateutil\\npytz\\n' > requirements.in && {COMPILE}",
    # Its test group without the services' C clients asyncmy, PySide6 and uwsgi (excluded on 3.14 or Linux-only).
    "apscheduler": "printf 'anyio[trio]~=4.0\\nattrs>=22.1\\ntenacity>=8.0,<10.0\\ntzlocal>=3.0\\ncbor2>=6.0\\n"
                   "pymongo>=4.13.0\\npaho-mqtt>=2.0\\nredis>=5.0.1\\nsqlalchemy[asyncio]>=2.0.24\\naiosqlite>=0.19\\n"
                   "psycopg[binary]\\npymysql[rsa]\\npytest>=7.4,<9.1\\npytest-lazy-fixtures\\npytest-mock\\nsniffio\\n"
                   f"time-machine>=2.13.0\\n' > requirements.in && {COMPILE}",
    "falcon": f"cp requirements/tests requirements.in && {COMPILE}",
    "django-ninja": f"uv export {EXPORT} --no-default-groups --group test -o requirements.txt",
    "streamlink": f"uv export {EXPORT} --no-default-groups --group test -o requirements.txt",
    "trio": "cp test-requirements.txt requirements.txt",  # Its pinned, universal test requirements.
    "sqlfluff": "printf 'hypothesis\\npytest\\n' > tests.in && uv pip compile -q pyproject.toml tests.in -o requirements.txt",
}
NPM = ("node-semver", "sinon", "pdfkit", "cytoscape")  # npm install --ignore-scripts; pdfkit and node-semver resolved.
PNPM = ("chroma", "svgo", "formidable")  # pnpm install --frozen-lockfile --ignore-scripts, hoisted.


def metadata(repo):
    """Distribution metadata only, with the pinned pyproject's entry points (sqlfluff reads its version and finds its
    own dialect and rule plugins through them); the code still comes from the workspace."""
    project = tomllib.loads((CORPORA / repo / "pyproject.toml").read_text())["project"]
    info = RUNTIME / f"{repo}-venv/lib/python3.14/site-packages/{repo}-{project['version']}.dist-info"
    info.mkdir(exist_ok=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {repo}\nVersion: {project['version']}\n")
    (info / "INSTALLER").write_text("graf-t15-runtime\n")
    (info / "entry_points.txt").write_text("".join(
        f"[{group}]\n" + "".join(f"{name} = {target}\n" for name, target in points.items()) + "\n"
        for group, points in project.get("entry-points", {}).items()))


METADATA = ("sqlfluff",)


def main(repos):
    t12_runtime.GO_MODULES.update(GO_MODULES)
    t12_runtime.PYTHON_REQUIREMENTS.update(PYTHON_REQUIREMENTS)
    for repo in repos or [*GO_MODULES, *PYTHON_REQUIREMENTS, *NPM, *PNPM]:
        step = go_step if repo in GO_MODULES else python_step if repo in PYTHON_REQUIREMENTS else \
            npm_step if repo in NPM else pnpm_step
        print(f"== {repo}", flush=True)
        step(repo)
        if repo in METADATA:
            metadata(repo)
    shutil.rmtree(SCRATCH, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
