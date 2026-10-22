"""Fetch the exact public snapshots in corpora.json without executing repo code."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request

BASE = Path(__file__).resolve().parents[1]


def fetch(t4=False, confirmation=False, screen=None):
    prereg = json.loads((BASE / "benchmark/preregistration.json").read_text())
    if t4:  # Batch t4 task repositories, pinned in the preregistration.
        repos = prereg["agent_trials"]["natural_tasks_t4"]
        items = [{"id": k, **v, "role": "agent_tasks"} for k, v in repos["repositories"].items()]
    elif confirmation or screen:  # Localization screen repositories (v21 confirmation, v24 file_screen).
        repos = prereg["localization_screen"][screen or "confirmation"]
        items = [{"id": k, **v, "role": "localization"} for k, v in repos["repositories"].items()]
    else:
        items = json.loads((BASE / "benchmark/corpora.json").read_text())["repositories"]
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
    out = ("results/snapshots-t4.json" if t4 else "results/snapshots-confirmation.json" if confirmation
           else f"results/snapshots-{screen.replace('_', '-')}.json" if screen else "results/snapshots.json")
    (BASE / out).write_text(json.dumps(receipts, indent=2) + "\n")


if __name__ == "__main__":
    import sys
    fetch(t4="--t4" in sys.argv[1:], confirmation="--confirmation" in sys.argv[1:],
          screen="file_screen" if "--file-screen" in sys.argv[1:] else "typescript" if "--typescript" in sys.argv[1:]
          else None)
