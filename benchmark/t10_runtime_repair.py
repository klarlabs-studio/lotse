"""Batch t10 runtime repair (v117): what an install of the pinned package would add to its virtualenv, minus its code.

pre-commit and networkx read their own distribution metadata (version, entry points); locust imports the version
module hatch-vcs generates at build time. The package code still comes only from the workspace.
pipenv's pytest configuration passes --no-cov and loads xdist, so it needs pytest-cov and pytest-xdist."""
from pathlib import Path
import subprocess

BASE = Path(__file__).resolve().parent.parent
RUNTIME = BASE / ".research/task-runtime"
SITE = "lib/python3.12/site-packages"
METADATA = {  # Versions as the pinned sources declare them (setup.cfg, networkx/__init__.py).
    "pre-commit": ("pre_commit", "4.7.0", ""),
    "networkx": ("networkx", "3.7.1rc0.dev0",
                 "[networkx.backends]\nnx_loopback = networkx.classes.tests.dispatch_interface:backend_interface\n"),
}
LOCUST_VERSION = "2.46.8.dev71"  # hatch-vcs at the pin: 71 commits after tag 2.46.7, no local version.
LOCUST_FINDER = f'''"""graf t10 runtime (v117): hatch-vcs writes locust/_version.py at build time; the snapshot has none."""
import importlib.abc
import importlib.util
import sys

SOURCE = 'version = __version__ = "{LOCUST_VERSION}"\\n'


class VersionModule(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path, target=None):
        return importlib.util.spec_from_loader(name, self) if name == "locust._version" else None

    def exec_module(self, module):
        exec(SOURCE, module.__dict__)


sys.meta_path.append(VersionModule())  # Last: a real locust/_version.py would still win.
'''
PIPENV_PLUGINS = ("pytest-cov", "pytest-xdist")


def metadata_only(repo, name, version, entry_points):
    info = RUNTIME / f"{repo}-venv" / SITE / f"{name}-{version}.dist-info"
    info.mkdir(exist_ok=True)
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
    (info / "INSTALLER").write_text("graf-t10-runtime-v117\n")
    if entry_points:
        (info / "entry_points.txt").write_text(entry_points)


def main():
    for repo, spec in METADATA.items():
        metadata_only(repo, *spec)
    site = RUNTIME / "locust-venv" / SITE
    (site / "_graf_locust_version.py").write_text(LOCUST_FINDER)
    (site / "_graf_locust_version.pth").write_text("import _graf_locust_version\n")
    venv = RUNTIME / "pipenv-venv"
    subprocess.run(["uv", "pip", "install", "--python", str(venv / "bin/python"), *PIPENV_PLUGINS], check=True)
    frozen = subprocess.run(["uv", "pip", "freeze", "--python", str(venv / "bin/python")],
                            check=True, capture_output=True, text=True).stdout
    (venv / "requirements-frozen.txt").write_text(frozen)


if __name__ == "__main__":
    main()
