"""Refuse to freeze a run on anything older than its latest release.

    python benchmark/check_latest.py [--batch ID]

Compares Graphify (PyPI), Claude Code (npm) and ripgrep (GitHub) with what a batch froze, or,
without --batch, with the newest pinned Graphify revision and the installed Claude Code. ripgrep
is the installed one in both cases (batches freeze its hash).
Exits 1 if any is behind; upgrade it (a newer Graphify becomes a separate baseline
revision) before freezing. The model is not checked: confirm it is the latest by hand.
"""
import argparse
import json
import re
import subprocess
import sys
import tomllib
import urllib.request

from retrieval import BASE, graphify_baselines


def version_key(version):
    return tuple(int(part) for part in re.match(r"\d+(?:\.\d+)*", version).group().split("."))


def behind(pinned, latest):
    """Each tool whose pinned version is older than its latest release: {tool: (pinned, latest)}."""
    return {tool: (pinned[tool], latest[tool]) for tool in pinned
            if version_key(pinned[tool]) < version_key(latest[tool])}


def latest_releases():
    with urllib.request.urlopen("https://pypi.org/pypi/graphifyy/json", timeout=30) as response:
        graphify = json.load(response)["info"]["version"]
    claude = subprocess.check_output(["npm", "view", "@anthropic-ai/claude-code", "version"], text=True).strip()
    ripgrep = subprocess.check_output(["gh", "api", "repos/BurntSushi/ripgrep/releases/latest", "--jq", ".tag_name"],
                                      text=True).strip()  # Authenticated: the anonymous API rate-limits.
    return {"graphify": graphify, "claude-code": claude, "ripgrep": ripgrep}


def graphify_version(pin):
    project = BASE / ".research/corpora" / pin["id"] / "pyproject.toml"
    return tomllib.loads(project.read_text())["project"]["version"]


def pinned_versions(batch_id):
    ripgrep = subprocess.check_output(["rg", "--version"], text=True).split()[1]
    baselines = graphify_baselines()
    if batch_id is None:
        claude = subprocess.check_output(["claude", "--version"], text=True).strip()
        return {"graphify": max((graphify_version(p) for p in baselines), key=version_key), "claude-code": claude, "ripgrep": ripgrep}
    batch = json.loads((BASE / "benchmark/preregistration.json").read_text())["agent_trials"]["batches"][batch_id]
    pin = next(p for p in baselines if p["commit"] == batch["graphify_commit"])
    return {"graphify": graphify_version(pin), "claude-code": batch["solver_version"], "ripgrep": ripgrep}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--batch")
    args = parser.parse_args()
    pinned, latest = pinned_versions(args.batch), latest_releases()
    stale = behind(pinned, latest)
    for tool in pinned:
        print(f"{tool}: pinned {pinned[tool]}, latest {latest[tool]}" + (" (BEHIND)" if tool in stale else ""))
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
