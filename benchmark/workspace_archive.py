"""Keep finished trial workspaces as deltas against their pinned snapshot, and bring them back identical.

    python benchmark/workspace_archive.py archive [TRIAL ...]   # every finished trial when none is named
    python benchmark/workspace_archive.py restore TRIAL ...

A trial's workspace is its repository's snapshot in .research/corpora plus the fault, the hidden-test removal and
the agent's edits. archive writes <trial>/workspace-delta.tar.gz (delta.json: removed paths and both manifests'
SHA-256; tree/: every added or changed file, link and directory), reads it back, and removes the workspace only
when the snapshot with the delta applied has exactly the workspace's manifest (paths, contents, executable bits,
link targets, empty directories). restore rebuilds the workspace and refuses a result whose manifest differs.
"""
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import time

BASE = Path(__file__).resolve().parents[1]
TRIALS = BASE / ".research/trials"
CORPORA = BASE / ".research/corpora"
DELTA = "workspace-delta.tar.gz"
SETTLED_S = 3600  # A trial touched within the last hour may still be running.


def file_entry(path):
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return ["f", digest, bool(path.stat().st_mode & 0o111)]


def manifest(root):
    """Every path under root: ["d"], ["l", target] or ["f", sha256, executable]. Links are never followed."""
    entries = {}
    for folder, dirs, files in os.walk(root):
        here = Path(folder)
        for name in dirs + files:
            path = here / name
            relative = path.relative_to(root).as_posix()
            if path.is_symlink():
                entries[relative] = ["l", os.readlink(path)]
            elif path.is_dir():
                entries[relative] = ["d"]
            elif path.is_file():
                entries[relative] = file_entry(path)
            else:
                entries[relative] = ["o"]  # Socket or pipe: a workspace holding one is kept.
        dirs[:] = [d for d in dirs if not (here / d).is_symlink()]
    return entries


def digest(entries):
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()


def snapshot(trial):
    """The pinned snapshot the trial's workspace started from, or None."""
    for name in ("session.json", "task.json"):
        if (trial / name).is_file():
            task = json.loads((trial / name).read_text()).get("task")
            if task:
                base = CORPORA / task.rsplit("-", 1)[0]
                return base if base.is_dir() else None
    return None


def write_delta(trial, base, before, after):
    removed = sorted(p for p in before if p not in after)
    changed = sorted(p for p, e in after.items() if before.get(p) != e)
    record = {"snapshot": base.relative_to(BASE).as_posix(), "snapshot_manifest": digest(before),
              "workspace_manifest": digest(after), "removed": removed, "entries": len(after)}
    workspace = trial / "workspace"
    with tarfile.open(trial / DELTA, "w:gz", compresslevel=9) as tar:
        data = json.dumps(record, indent=1).encode()
        info = tarfile.TarInfo("delta.json")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
        for path in changed:
            tar.add(workspace / path, arcname=f"tree/{path}", recursive=False)


def delta_manifest(trial, before):
    """The manifest the snapshot would have with the stored delta applied."""
    with tarfile.open(trial / DELTA, "r:gz") as tar:
        record = json.load(tar.extractfile("delta.json"))
        entries = {p: e for p, e in before.items() if p not in set(record["removed"])}
        for member in tar.getmembers():
            if not member.name.startswith("tree/"):
                continue
            path = member.name[len("tree/"):]
            if member.issym():
                entries[path] = ["l", member.linkname]
            elif member.isdir():
                entries[path] = ["d"]
            else:
                content = tar.extractfile(member).read()
                entries[path] = ["f", hashlib.sha256(content).hexdigest(), bool(member.mode & 0o111)]
    return record, entries


def archive_trial(trial, snapshots):
    workspace = trial / "workspace"
    if workspace.is_symlink() or not workspace.is_dir():
        return "skipped: no workspace folder"
    newest = max((f.stat().st_mtime for f in trial.iterdir() if f.is_file() and not f.is_symlink()), default=0)
    if time.time() - newest < SETTLED_S:  # Files, not the folder: removing a subfolder touches the folder.
        return "skipped: touched within the last hour"
    base = snapshot(trial)
    if base is None:
        return "skipped: no pinned snapshot"
    before = snapshots.setdefault(base, manifest(base))
    after = manifest(workspace)
    if any(e == ["o"] for e in after.values()):
        return "kept: holds a socket or pipe"
    write_delta(trial, base, before, after)
    record, rebuilt = delta_manifest(trial, before)
    if rebuilt != after or record["workspace_manifest"] != digest(after):
        (trial / DELTA).unlink()
        return "kept: delta does not reproduce the workspace"
    shutil.rmtree(workspace)
    return "archived"


def restore_trial(trial):
    workspace = trial / "workspace"
    if workspace.exists():
        raise SystemExit(f"{trial.name}: workspace already exists")
    with tarfile.open(trial / DELTA, "r:gz") as tar:
        record = json.load(tar.extractfile("delta.json"))
        base = BASE / record["snapshot"]
        if digest(manifest(base)) != record["snapshot_manifest"]:
            raise SystemExit(f"{trial.name}: {record['snapshot']} is not the snapshot the delta was taken against")
        subprocess.run(["cp", "-cR", str(base), str(workspace)], check=True)  # APFS clone: no copied bytes.
        for path in reversed(record["removed"]):
            target = workspace / path
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
        for member in tar.getmembers():
            if member.name.startswith("tree/"):
                target = workspace / member.name[len("tree/"):]
                if target.is_symlink() or target.is_file():
                    target.unlink()
                member.name = member.name[len("tree/"):]
                tar.extract(member, workspace, filter="fully_trusted")  # Our own archive, links included.
    if digest(manifest(workspace)) != record["workspace_manifest"]:
        raise SystemExit(f"{trial.name}: restored workspace does not match its manifest")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=("archive", "restore"))
    parser.add_argument("trials", nargs="*")
    args = parser.parse_args()
    trials = [TRIALS / t for t in args.trials] or sorted(p for p in TRIALS.iterdir() if p.is_dir())
    if args.action == "restore":
        for trial in trials:
            restore_trial(trial)
        return
    snapshots, outcomes = {}, {}
    for trial in trials:
        outcome = archive_trial(trial, snapshots)
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
        if not outcome.startswith("archived"):
            print(trial.name, outcome, flush=True)
    print(json.dumps(outcomes, indent=1))


if __name__ == "__main__":
    main()
