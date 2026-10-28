"""Preserve immutable decision evidence alongside required latest-report aliases."""
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]


def write_decision(result, base=BASE):
    directory = base / "results"
    archive = directory / "decisions"
    archive.mkdir(parents=True, exist_ok=True)
    latest = directory / "decision.json"

    def preserve(content):
        role = json.loads(content).get("workload", "unspecified")
        # Workload names are internal, not paths supplied by a report.
        role = role if role in ("development", "held_out") else "unspecified"
        path = archive / f"{role}-{hashlib.sha256(content).hexdigest()}.json"
        if path.exists() and path.read_bytes() != content:
            raise RuntimeError(f"Immutable report collision: {path}")
        if not path.exists():
            path.write_bytes(content)
        return path, role

    if latest.exists():
        preserve(latest.read_bytes())
    content = (json.dumps(result, indent=2, allow_nan=False) + "\n").encode()
    archived, role = preserve(content)
    if role != "unspecified":
        name = "heldout" if role == "held_out" else role
        (directory / f"decision-{name}.json").write_bytes(content)
    latest.write_bytes(content)
    return archived
