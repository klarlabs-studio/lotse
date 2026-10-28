"""Keep oversized raw results out of git, and bring them back byte-identical.

    python benchmark/raw_archive.py archive results/FILE.json ...   # gzip under .research/evidence/raw/, record, remove
    python benchmark/raw_archive.py restore [results/FILE.json ...]  # every recorded file when none is named

The receipt, results/raw-archive.json, records each file's SHA-256 and size; restore refuses an archive
whose content does not match it.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import sys

BASE = Path(__file__).resolve().parents[1]
RECEIPT = BASE / "results/raw-archive.json"
ARCHIVE_DIR = Path(".research/evidence/raw")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_receipt():
    return json.loads(RECEIPT.read_text())


def archive(paths):
    receipt = load_receipt()
    recorded = {row["result"]: row for row in receipt["files"]}
    for path in paths:
        raw = (BASE / path).read_bytes()
        target = ARCHIVE_DIR / (Path(path).name + ".gz")
        (BASE / target).parent.mkdir(parents=True, exist_ok=True)
        (BASE / target).write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
        stored = (BASE / target).read_bytes()
        if sha(gzip.decompress(stored)) != sha(raw):
            raise SystemExit(f"{path}: archive does not round-trip")
        recorded[path] = {"result": path, "sha256": sha(raw), "bytes": len(raw), "archive": str(target),
                          "archive_sha256": sha(stored), "archive_bytes": len(stored)}
        (BASE / path).unlink()
        print(f"{path}: {len(raw) // 10**6} MB -> {len(stored) // 10**6} MB")
    receipt["files"] = sorted(recorded.values(), key=lambda row: row["result"])
    RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n")


def restore(paths):
    rows = load_receipt()["files"]
    wanted = [row for row in rows if not paths or row["result"] in paths]
    missing = set(paths) - {row["result"] for row in rows}
    if missing:
        raise SystemExit(f"not in the receipt: {sorted(missing)}")
    for row in wanted:
        raw = gzip.decompress((BASE / row["archive"]).read_bytes())
        if sha(raw) != row["sha256"]:
            raise SystemExit(f"{row['result']}: archive content does not match the recorded SHA-256")
        (BASE / row["result"]).write_bytes(raw)
        print(f"{row['result']}: restored and verified")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["archive", "restore"])
    parser.add_argument("paths", nargs="*", help="repository-relative result paths")
    args = parser.parse_args()
    if args.action == "archive" and not args.paths:
        parser.error("archive needs at least one path")
    (archive if args.action == "archive" else restore)(args.paths)
    return 0


if __name__ == "__main__":
    sys.exit(main())
