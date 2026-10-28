"""Private, read-only copies of the frozen broker inputs.

The copy is useful for insulating a run from later changes to the live
workspace. It is not an atomic filesystem snapshot and does not defend against
a hostile writer running as the same user while the copy is being made. It
also does not establish that an artifact is semantically valid for its source.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile

from readonly_broker import BrokerError
from readonly_broker_v2 import source_manifest_sha256


_ELIGIBLE_EXTENSIONS = {
    ".go", ".py", ".js", ".mjs", ".cjs", ".ts", ".md", ".mdx",
    ".rst", ".txt", ".tsx", ".jsx",
}
_GRAPHIFY_ARTIFACT = Path("graph/graphify-out/graph.json")
_GRAF_ARTIFACT = Path(".graf/index.bin")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_rel(value: str) -> PurePosixPath:
    if (not isinstance(value, str) or not value or "\\" in value or "\x00" in value
            or value.startswith("/")):
        raise BrokerError("Invalid relative path")
    parts = value.split("/")
    if any(part in ("", ".", "..") or part.startswith(".") for part in parts):
        raise BrokerError("Path is hidden, absolute, or traverses directories")
    return PurePosixPath(value)


def _reject_symlink_components(path: Path) -> None:
    """Reject symlinks from the filesystem root through the requested path."""
    path = Path(os.path.abspath(path))
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise BrokerError(f"Symlink path refused: {current}")


class Snapshot:
    """Context manager that copies broker sources and one pinned artifact.

    ``arm_id`` selects the only supported artifact layout. Graphify's graph is
    stored beside the input checkout in the benchmark corpus layout; Graf's
    index lives inside the source root. No caller-controlled destination path
    is accepted.
    """

    def __init__(self, source_root, manifest, artifact_path, artifact_sha256,
                 arm_id, parent):
        supplied_source_root = Path(source_root).absolute()
        if supplied_source_root.is_symlink():
            raise BrokerError("Symlink source root refused")
        self.source_root = supplied_source_root.resolve(strict=True)
        self.manifest = dict(manifest) if isinstance(manifest, dict) else manifest
        supplied_artifact_path = Path(artifact_path).absolute()
        self.original_artifact_path = supplied_artifact_path
        self.artifact_sha256 = artifact_sha256
        self.arm_id = arm_id
        self.parent = Path(parent).absolute()
        self._container: Path | None = None
        self._entered = False
        self.receipt = None
        if not isinstance(self.manifest, dict) or not self.manifest:
            raise BrokerError("Missing source manifest")
        for rel, digest in self.manifest.items():
            _safe_rel(rel)
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise BrokerError("Invalid source digest")
        if not isinstance(artifact_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", artifact_sha256):
            raise BrokerError("Invalid artifact SHA-256")
        if arm_id.startswith("graf_"):
            self._artifact_rel = _GRAF_ARTIFACT
            expected = self.source_root / _GRAF_ARTIFACT
        elif arm_id == "graphify":
            self._artifact_rel = _GRAPHIFY_ARTIFACT
            expected = self.source_root.parent / _GRAPHIFY_ARTIFACT
        else:
            raise BrokerError("Unsupported snapshot arm")
        # Temporary directories on macOS commonly have a /var alias to
        # /private/var. Compare canonical identities while retaining the
        # original spelling for the explicit component-symlink check.
        if supplied_artifact_path.is_symlink():
            raise BrokerError("Symlink artifact path refused")
        if supplied_artifact_path.resolve(strict=False) != expected.resolve(strict=False):
            raise BrokerError("Artifact path does not match supported layout")
        # Keep the canonical-root-based path spelling so symlinks beneath the
        # source root remain visible to the component walk below.
        self.original_artifact_path = expected
        self._manifest_digest = source_manifest_sha256(self.manifest)

    @property
    def root(self) -> Path:
        if self._container is None:
            raise RuntimeError("Snapshot has not been entered")
        return self._container / "input"

    @property
    def artifact_path(self) -> Path:
        if self._container is None:
            raise RuntimeError("Snapshot has not been entered")
        if self.arm_id == "graphify":
            return self._container / _GRAPHIFY_ARTIFACT
        return self.root / _GRAF_ARTIFACT

    @property
    def graph_path(self) -> Path:
        return self.artifact_path

    def _inventory(self) -> set[str]:
        _reject_symlink_components(self.source_root)
        if not self.source_root.is_dir():
            raise BrokerError("Source root is unavailable")
        found = set()
        for candidate in self.source_root.rglob("*"):
            if candidate.is_symlink():
                raise BrokerError("Symlink in source tree refused")
            rel = candidate.relative_to(self.source_root)
            if not candidate.is_file() or candidate.suffix not in _ELIGIBLE_EXTENSIONS:
                continue
            if any(part.startswith(".") or part in {"node_modules", "vendor", "graphify-out"}
                   for part in rel.parts):
                continue
            found.add(rel.as_posix())
        if found != set(self.manifest):
            raise BrokerError("Eligible source inventory differs from frozen manifest")
        return found

    def _read_verified_sources(self) -> dict[str, bytes]:
        self._inventory()
        copied = {}
        for rel, expected in self.manifest.items():
            path = self.source_root.joinpath(*_safe_rel(rel).parts)
            _reject_symlink_components(path)
            if not path.is_file():
                raise BrokerError("Source path is unavailable")
            data = path.read_bytes()
            if _sha(data) != expected:
                raise BrokerError(f"Source bytes differ from frozen manifest: {rel}")
            copied[rel] = data
        return copied

    def _read_verified_artifact(self) -> bytes:
        _reject_symlink_components(self.original_artifact_path)
        if not self.original_artifact_path.is_file():
            raise BrokerError("Pinned retrieval artifact is missing")
        data = self.original_artifact_path.read_bytes()
        if _sha(data) != self.artifact_sha256:
            raise BrokerError("Pinned retrieval artifact hash mismatch")
        return data

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            # fdopen owns the descriptor from here.
            raise

    @staticmethod
    def _make_readonly(root: Path) -> None:
        for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path.is_file():
                path.chmod(0o444)
            elif path.is_dir():
                path.chmod(0o555)
        # The outer directory is private to its owner; snapshot children are
        # readable/traversable for the backend process.
        root.chmod(0o500)

    def __enter__(self):
        if self._entered:
            raise RuntimeError("Snapshot context cannot be entered twice")
        self.parent.mkdir(parents=True, exist_ok=True)
        self._container = Path(tempfile.mkdtemp(prefix="graf-snapshot-", dir=self.parent))
        try:
            # Capture bytes only after validating the complete source inventory,
            # then revalidate the live inventory after all bytes are staged.
            sources = self._read_verified_sources()
            artifact = self._read_verified_artifact()
            self.root.mkdir(mode=0o700)
            for rel, data in sources.items():
                self._write(self.root.joinpath(*_safe_rel(rel).parts), data)
            self._write(self.artifact_path, artifact)
            self._inventory()
            # A second hash pass catches source changes during staging.
            for rel, expected in self.manifest.items():
                if _sha(self.source_root.joinpath(*_safe_rel(rel).parts).read_bytes()) != expected:
                    raise BrokerError(f"Source changed while snapshot was copied: {rel}")
            if _sha(self.original_artifact_path.read_bytes()) != self.artifact_sha256:
                raise BrokerError("Artifact changed while snapshot was copied")
            staged_hashes = {rel: _sha((self.root / Path(*_safe_rel(rel).parts)).read_bytes())
                             for rel in sorted(self.manifest)}
            if any(staged_hashes[rel] != self.manifest[rel] for rel in staged_hashes):
                raise BrokerError("Staged source copy failed hash verification")
            if _sha(self.artifact_path.read_bytes()) != self.artifact_sha256:
                raise BrokerError("Staged artifact copy failed hash verification")
            self._make_readonly(self._container)
            self.receipt = {
                "arm_id": self.arm_id,
                "source_root": str(self.source_root),
                "snapshot_root": str(self.root),
                "original_artifact_path": str(self.original_artifact_path),
                "artifact_path": str(self.artifact_path),
                "source_manifest_sha256": self._manifest_digest,
                "source_hashes": staged_hashes,
                "artifact_sha256": self.artifact_sha256,
            }
            self._entered = True
            return self
        except BaseException:
            self._cleanup()
            raise

    def _cleanup(self) -> None:
        if self._container is None or not self._container.exists():
            return
        for path in sorted(self._container.rglob("*"), key=lambda item: len(item.parts)):
            try:
                if path.is_dir() and not path.is_symlink():
                    path.chmod(0o700)
                elif not path.is_symlink():
                    path.chmod(0o600)
            except OSError:
                pass
        try:
            self._container.chmod(0o700)
        except OSError:
            pass
        shutil.rmtree(self._container)
        self._container = None
        self._entered = False

    def __exit__(self, exc_type, exc, tb):
        self._cleanup()
        return False
