"""Fetch the exact public snapshots in corpora.json without executing repo code."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request

BASE = Path(__file__).resolve().parents[1]


def fetch(t4=False, confirmation=False, screen=None, batch=None, baseline=None, only=None):
    prereg = json.loads((BASE / "benchmark/preregistration.json").read_text())
    batch = batch or ("t4" if t4 else None)
    if batch:  # Agent-batch task repositories (natural_tasks_<batch>), pinned in the preregistration.
        repos = prereg["agent_trials"][f"natural_tasks_{batch}"]
        repos = repos.get("repositories_v138", repos)  # natural_tasks_t14 records its repositories in v138.
        items = [{"id": k, **v, "role": "agent_tasks"} for k, v in repos["repositories"].items()
                 if not only or k in only]  # Repositories a later version added beside the fetched ones.
    elif confirmation or screen:  # Localization screen repositories (v21 confirmation, v24 file_screen).
        repos = prereg["localization_screen"][screen or "confirmation"]
        items = [{"id": k, **v, "role": "localization"} for k, v in repos["repositories"].items()]
    else:
        items = json.loads((BASE / "benchmark/corpora.json").read_text())["repositories"]
        if baseline:  # One new baseline revision, beside the snapshots earlier results use.
            items = [item for item in items if item["id"] == baseline]
            if not items:
                raise KeyError(baseline)
    receipts = []
    for item in items:
        url = f"https://codeload.github.com/{item['repository']}/tar.gz/{item['commit']}"
        request = urllib.request.Request(url, headers={"User-Agent": "graf-research"})
        data = urllib.request.urlopen(request, timeout=120).read()
        root = BASE / ".research/corpora" / item["id"]
        if root.exists():
            raise RuntimeError(f"Refusing to overlay snapshot: {root}")
        root.mkdir(parents=True)
        count = 0
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            for member in archive:
                if not member.isfile():
                    continue
                parts = Path(member.name).parts[1:]
                dest = root.joinpath(*parts)
                if not dest.resolve().is_relative_to(root.resolve()):
                    raise ValueError("Archive path escapes snapshot")
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(archive.extractfile(member).read())
                count += 1
        receipts.append({**item, "archive_sha256": hashlib.sha256(data).hexdigest(), "files": count})
        print(item["id"], count, "files", flush=True)
    out = (f"results/snapshots-{batch}.json" if batch else f"results/snapshots-{baseline}.json" if baseline else "results/snapshots-confirmation.json" if confirmation
           else f"results/snapshots-{screen.replace('_', '-')}.json" if screen else "results/snapshots.json")
    if only and (BASE / out).is_file():
        receipts = [r for r in json.loads((BASE / out).read_text()) if r["id"] not in only] + receipts
    (BASE / out).write_text(json.dumps(receipts, indent=2) + "\n")


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    fetch(t4="--t4" in args, batch=next((b for b in ("t7", "t8", "t9", "t10", "t11", "t12", "t14") if f"--{b}" in args), None),
          confirmation="--confirmation" in args,
          screen="file_screen" if "--file-screen" in args else "typescript" if "--typescript" in args else None,
          baseline=args[args.index("--baseline") + 1] if "--baseline" in args else None,
          only=set(args[args.index("--only") + 1].split(",")) if "--only" in args else None)
