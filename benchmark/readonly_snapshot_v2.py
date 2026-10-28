"""Graf snapshots with an in-copy, root-correct index rebuild.

The source snapshot is copied and sealed by :mod:`readonly_snapshot`. Graf
indexes bind their absolute root path, so v2 rebuilds Graf's index inside that
private copy and checks its root-independent exported semantics against a
frozen digest. This remains a same-user filesystem diagnostic, not an atomic
snapshot or an OS security boundary.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from readonly_broker import BrokerError
from readonly_snapshot import Snapshot as BaseSnapshot, _ELIGIBLE_EXTENSIONS, _safe_rel


def _sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_semantic_digest(exported: str) -> str:
    try:
        value = json.loads(exported)
    except (TypeError, ValueError) as exc:
        raise BrokerError("Graf export-index returned invalid JSON") from exc
    if not isinstance(value, dict) or "root" not in value:
        raise BrokerError("Graf export-index omitted root")
    # Root is the sole intentionally varying field. Keep every other field.
    value.pop("root")
    wire = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(wire).hexdigest()


class Snapshot(BaseSnapshot):
    """A private source/artifact copy with a semantically checked Graf index.

    ``index_binary`` and both associated pins are mandatory for Graf arms.
    ``runner`` is injectable for deterministic tests; production defaults to
    ``subprocess.run``.
    """

    def __init__(self, source_root, manifest, artifact_path, artifact_sha256,
                 arm_id, parent, *, index_binary=None, index_binary_sha256=None,
                 index_semantics_sha256=None, runner=subprocess.run):
        super().__init__(source_root, manifest, artifact_path, artifact_sha256,
                         arm_id, parent)
        self.index_binary = None
        self.index_binary_sha256 = index_binary_sha256
        self.index_semantics_sha256 = index_semantics_sha256
        self.runner = runner
        self._original_artifact_sha256 = artifact_sha256
        self._index_operations = []
        if arm_id.startswith("graf_"):
            if index_binary is None:
                raise BrokerError("Graf snapshot requires a pinned index binary")
            if not isinstance(index_binary_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", index_binary_sha256):
                raise BrokerError("Graf snapshot requires a valid index binary SHA-256")
            if not isinstance(index_semantics_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", index_semantics_sha256):
                raise BrokerError("Graf snapshot requires a valid index semantic SHA-256")
            supplied = Path(index_binary).absolute()
            if supplied.is_symlink():
                raise BrokerError("Symlink index binary refused")
            try:
                self.index_binary = supplied.resolve(strict=True)
            except OSError as exc:
                raise BrokerError("Pinned index binary is unavailable") from exc
            if not self.index_binary.is_file():
                raise BrokerError("Pinned index binary is not a file")
            self._check_binary()

    def _check_binary(self) -> None:
        if self.index_binary is None:
            raise BrokerError("Missing pinned index binary")
        try:
            digest = _sha_bytes(self.index_binary.read_bytes())
        except OSError as exc:
            raise BrokerError("Pinned index binary is unavailable") from exc
        if digest != self.index_binary_sha256:
            raise BrokerError("Pinned index binary hash mismatch")

    def _verify_snapshot_sources(self) -> dict[str, str]:
        hashes = {}
        found = set()
        for path in self.root.rglob("*"):
            if path.is_symlink():
                raise BrokerError("Symlink in staged source tree refused")
            rel = path.relative_to(self.root)
            if not path.is_file() or path.suffix not in _ELIGIBLE_EXTENSIONS:
                continue
            if any(part.startswith(".") or part in {"node_modules", "vendor", "graphify-out"}
                   for part in rel.parts):
                continue
            found.add(rel.as_posix())
        if found != set(self.manifest):
            raise BrokerError("Staged source inventory differs from frozen manifest")
        for rel, expected in self.manifest.items():
            source = self.root.joinpath(*_safe_rel(rel).parts)
            digest = _sha_bytes(source.read_bytes())
            if digest != expected:
                raise BrokerError(f"Staged source bytes differ from frozen manifest: {rel}")
            hashes[rel] = digest
        return hashes

    def _run_index_command(self, operation: str, argv: list[str]) -> dict:
        record = {"operation": operation, "argv": list(argv), "status": None,
                  "stdout": "", "stderr": "", "elapsed_seconds": None}
        self._check_binary()
        started = time.perf_counter()
        # Keep the mutable record attached to the receipt before control leaves
        # this process, so post-run integrity failures retain native evidence.
        self._index_operations.append(record)
        try:
            completed = self.runner(argv, capture_output=True, text=True, timeout=60,
                                    check=False, env={**os.environ, "PYTHONHASHSEED": "0"})
            record["status"] = completed.returncode
            record["stdout"] = completed.stdout
            record["stderr"] = completed.stderr
        except Exception as exc:
            stdout = getattr(exc, "stdout", None) or getattr(exc, "output", "") or ""
            stderr = getattr(exc, "stderr", "") or ""
            record["stdout"] = stdout.decode("utf-8", "replace") if isinstance(stdout, bytes) else str(stdout)
            record["stderr"] = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else str(stderr)
            record["error_type"] = type(exc).__name__
            record["elapsed_seconds"] = time.perf_counter() - started
            self._check_binary()
            raise BrokerError(f"Graf snapshot {operation} failed: {type(exc).__name__}") from exc
        finally:
            if record["elapsed_seconds"] is None:
                record["elapsed_seconds"] = time.perf_counter() - started
        self._check_binary()
        self._verify_snapshot_sources()
        if record["status"] != 0:
            raise BrokerError(f"Graf snapshot {operation} exited with status {record['status']}")
        return record

    def _seal_receipt(self) -> None:
        if self.receipt is None:
            return
        self.receipt["original_artifact_sha256"] = self._original_artifact_sha256
        try:
            self.receipt["artifact_sha256"] = _sha_bytes(self.artifact_path.read_bytes())
        except OSError:
            self.receipt["artifact_sha256"] = None
        self.receipt["index_semantics_sha256"] = self.index_semantics_sha256
        self.receipt["index_binary_sha256"] = self.index_binary_sha256
        self.receipt["index_operations"] = list(self._index_operations)
        self.receipt["index_elapsed_samples_seconds"] = [
            item["elapsed_seconds"] for item in self._index_operations
        ]

    def __enter__(self):
        try:
            super().__enter__()
            if not self.arm_id.startswith("graf_"):
                self._seal_receipt()
                return self

            # Only the private artifact directory needs write access for Graf's
            # atomic index replacement; source files remain read-only.
            graf_dir = self.root / ".graf"
            graf_dir.chmod(0o700)
            build_argv = [str(self.index_binary), "--root", str(self.root), "index", "--rebuild"]
            self._run_index_command("rebuild", build_argv)
            export_argv = [str(self.index_binary), "--root", str(self.root), "export-index"]
            exported = self._run_index_command("export-index", export_argv)
            exported_json = json.loads(exported["stdout"])
            expected_root = str(self.root.resolve(strict=True))
            if not isinstance(exported_json, dict) or exported_json.get("root") != expected_root:
                raise BrokerError("Graf export-index root does not match the staged root")
            semantic_digest = _canonical_semantic_digest(exported["stdout"])
            if semantic_digest != self.index_semantics_sha256:
                raise BrokerError("Rebuilt Graf index semantic digest mismatch")
            if not self.artifact_path.is_file():
                raise BrokerError("Graf rebuild did not create the pinned artifact path")
            self._verify_snapshot_sources()
            self._make_readonly(self._container)
            self._seal_receipt()
            return self
        except BaseException:
            try:
                self._seal_receipt()
            finally:
                self._cleanup()
            raise


__all__ = ["Snapshot"]
