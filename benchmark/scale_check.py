"""Large-codebase diagnostic: build time, memory, artifact size, and query latency (no solver).

    python benchmark/scale_check.py [--repo NAME ...] [--skip-graphify]

For each pinned large repository: download the snapshot, then measure `graf index`
(cold), `locate`, `units` (cold, then warm), `triage`, and `outline`, each with wall time
and peak resident memory (/usr/bin/time -l), plus the sizes of the index and
.graf/units.bin. Graphify's code-only extract and one query are measured the same way;
with --skip-graphify the previous Graphify measurements are kept. Results go to results/scale-check.json; snapshots are deleted afterwards.

A diagnostic of whether graf works at scale, not a preregistered comparison.
"""
import argparse
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import urllib.request

from localization import write_json_atomic
from retrieval import BASE, graf_artifact

GRAF = BASE / "target/release/graf"
GRAPHIFY = BASE / ".research/venv/bin/graphify"
WORK = BASE / ".research/scale"
REPOS = {
    "kubernetes": ("kubernetes/kubernetes", "2770c7b39f56a750c23d026bd311cb8d0eda8b4d",
                   "kubelet fails to restart a pod after the container image is garbage collected\n\n"
                   "The kubelet logs `ImagePullBackOff` and `syncPod` never retries; see pkg/kubelet/kuberuntime."),
    "vscode": ("microsoft/vscode", "bdce63db587665a91d1f1237b807d43471f0d9bc",
               "Terminal tabs lose their title after reload\n\n"
               "After `Developer: Reload Window` the `TerminalTabsListWidget` shows the default shell name."),
    "django": ("django/django", "79502d08405dbf24efa39e8731320f31035b9e9c",
               "QuerySet.bulk_update() ignores db_default on fields\n\n"
               "Calling `bulk_update()` with a field that has `db_default` writes NULL."),
}


def fetch(slug, commit, dest):
    if dest.is_dir():
        return
    data = urllib.request.urlopen(urllib.request.Request(
        f"https://codeload.github.com/{slug}/tar.gz/{commit}", headers={"User-Agent": "graf-research"}),
        timeout=600).read()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive:
            if not member.isfile():
                continue
            parts = Path(member.name).parts[1:]
            if not parts:
                continue
            target = dest.joinpath(*parts)
            if not target.resolve().is_relative_to(dest.resolve()):
                raise ValueError("Archive path escapes snapshot")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.extractfile(member).read())


def measured(command):
    """Wall seconds and peak RSS (MB) of one process, via BSD /usr/bin/time -l."""
    run = subprocess.run(["/usr/bin/time", "-l", *command], capture_output=True, text=True,
                         env={**os.environ, "PYTHONHASHSEED": "0"})
    if run.returncode:
        return {"error": run.stderr[-2000:]}
    wall = float(re.search(r"([\d.]+) real", run.stderr).group(1))
    rss = int(re.search(r"(\d+)\s+maximum resident set size", run.stderr).group(1)) / 1e6
    return {"seconds": wall, "peak_rss_mb": round(rss, 1), "output_bytes": len(run.stdout)}


def size_mb(path):
    return round(path.stat().st_size / 1e6, 1) if path.is_file() else None


def check(name, skip_graphify):
    slug, commit, issue = REPOS[name]
    root = WORK / name
    fetch(slug, commit, root)
    graf = lambda *args: measured([str(GRAF), "--root", str(root), *args])
    result = {"repository": slug, "commit": commit}
    result["graf_index"] = graf("index", "--rebuild")
    stats = json.loads(subprocess.run([str(GRAF), "--root", str(root), "stats"], capture_output=True,
                                      text=True).stdout or "{}")
    result["indexed_files"], result["chunks"] = stats.get("files"), stats.get("chunks")
    result["index_file"] = graf_artifact(root).name
    result["index_mb"] = size_mb(graf_artifact(root))
    result["locate"] = graf("locate", "--budget", "2000", "--text", "--", issue)
    result["units_cold"] = graf("units", "--budget", "2000", "--text", "--", issue)
    result["unit_cache_mb"] = size_mb(root / ".graf/units.bin")
    result["units_warm"] = graf("units", "--budget", "2000", "--text", "--", issue)
    result["triage"] = graf("triage", "--budget", "1500", "--text", "--", issue)
    top = re.search(r"^- (.+)$", subprocess.run([str(GRAF), "--root", str(root), "triage", "--budget", "1500",
                                                 "--text", "--", issue], capture_output=True, text=True).stdout,
                    re.M)
    if top:
        result["outline"] = {"path": top.group(1), **graf("outline", "--text", top.group(1))}
    if not skip_graphify:
        out = WORK / f"{name}-graphify"
        shutil.rmtree(out, ignore_errors=True)
        result["graphify_extract"] = measured([str(GRAPHIFY), "extract", str(root), "--code-only", "--no-cluster",
                                               "--max-workers", "2", "--out", str(out)])
        graph = out / "graphify-out/graph.json"
        result["graph_json_mb"] = size_mb(graph)
        if graph.is_file():
            result["graphify_query"] = measured([sys.executable, str(BASE / "benchmark/compare_graphify.py"),
                                                 "--graphify-query", str(graph), issue, "1500"])
        shutil.rmtree(out, ignore_errors=True)
    shutil.rmtree(root, ignore_errors=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", action="append", choices=sorted(REPOS))
    parser.add_argument("--skip-graphify", action="store_true")
    args = parser.parse_args()
    out = BASE / "results/scale-check.json"
    report = json.loads(out.read_text()) if out.is_file() else {}
    graf_sha = __import__("hashlib").sha256(GRAF.read_bytes()).hexdigest()
    report.pop("graf_sha256", None)
    for name in args.repo or list(REPOS):
        previous = report.get(name, {})
        report[name] = {**check(name, args.skip_graphify), "graf_sha256": graf_sha}
        if args.skip_graphify:
            report[name].update({k: v for k, v in previous.items() if k.startswith("graph")})
        report[name]["loadavg"] = os.getloadavg()
        write_json_atomic(out, report)
        print(name, json.dumps(report[name])[:800], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
