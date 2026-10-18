"""Fetch the exact public snapshots in corpora.json without executing repo code."""
import hashlib
import io
import json
from pathlib import Path
import tarfile
import urllib.request

BASE = Path(__file__).resolve().parents[1]


def fetch():
    manifest = json.loads((BASE / "benchmark/corpora.json").read_text())
    receipts = []
    for item in manifest["repositories"]:
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
    (BASE / "results/snapshots.json").write_text(json.dumps(receipts, indent=2) + "\n")


if __name__ == "__main__":
    fetch()
