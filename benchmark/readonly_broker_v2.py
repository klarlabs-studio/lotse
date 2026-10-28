"""Experimental artifact-pinned v2 of the read-only retrieval broker.

Pins and inventories are checked around every child process. These hashes do
not provide an atomic filesystem snapshot: a concurrent writer could still
change files between checks or while a child is reading them.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

from readonly_broker import Broker as BaseBroker, BrokerError


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_manifest_sha256(manifest) -> str:
    wire = json.dumps(manifest, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(wire).hexdigest()


class _PostRunMutation(BrokerError):
    def __init__(self, message, completed):
        super().__init__(message)
        self.stdout = completed.stdout
        self.stderr = completed.stderr
        self.returncode = completed.returncode


class Broker(BaseBroker):
    def __init__(self, plan, arm_id, root, graph_path, journal,
                 max_actions=4, runner=subprocess.run):
        if not isinstance(plan, dict):
            raise ValueError("Plan must be an object")
        self.plan = copy.deepcopy(plan)
        if self.plan.get("subprocess_environment") != {"PYTHONHASHSEED": "0"}:
            raise ValueError("Plan must declare subprocess_environment={'PYTHONHASHSEED':'0'}")
        self.root = Path(root).resolve(strict=True)
        self.graph_path = Path(graph_path)
        arms = [a for a in self.plan.get("arms", []) if isinstance(a, dict) and a.get("id") == arm_id]
        if len(arms) != 1:
            raise ValueError("Exactly one matching arm is required")
        arm = arms[0]
        manifest = self.plan.get("source_manifest")
        if not isinstance(manifest, dict) or not manifest:
            raise ValueError("Missing source manifest")
        digest = source_manifest_sha256(manifest)
        self.manifest = manifest
        pin = self.plan.get("artifacts", {}).get(arm_id) if isinstance(self.plan.get("artifacts"), dict) else None
        if not isinstance(pin, dict):
            raise ValueError("Missing per-arm artifact pin")
        expected_path = (self.root / ".graf" / "index.bin") if arm_id.startswith("graf_") else self.graph_path
        artifact = pin.get("path")
        if not isinstance(artifact, str) or not Path(artifact).is_absolute():
            raise ValueError("Artifact path must be absolute")
        expected_path = expected_path.absolute()
        if Path(artifact) != expected_path:
            raise ValueError("Artifact path does not match backend configuration")
        if pin.get("source_manifest_sha256") != digest:
            raise ValueError("Artifact source manifest binding mismatch")
        if not isinstance(pin.get("sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", pin["sha256"]):
            raise ValueError("Invalid artifact SHA-256")
        # Broker initialization creates the journal; complete every pin check first.
        self._artifact_path = Path(artifact)
        self._artifact_sha = pin["sha256"]
        self._arm_for_checks = arm_id
        binary = Path(arm.get("binary", ""))
        if not binary.is_absolute():
            binary = self.root / binary
        binary = binary.resolve(strict=True)
        if not binary.is_file() or _sha(binary) != arm.get("binary_sha256"):
            raise BrokerError("Backend binary hash mismatch")
        self._native_runner = runner
        self._check_pins()
        super().__init__(self.plan, arm_id, self.root, self.graph_path, journal,
                         max_actions=max_actions, runner=self._guarded_runner)

    def _check_pins(self):
        # Reject symlinks at any component, including ancestors outside root.
        path = self._artifact_path
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current = current / part
            if current.is_symlink():
                raise BrokerError("Artifact path contains a symlink")
        if not path.is_file() or _sha(path) != self._artifact_sha:
            raise BrokerError("Pinned retrieval artifact is missing or stale")
        binary = getattr(self, "binary", None)
        if binary is not None:
            arm = next(a for a in self.plan["arms"] if a.get("id") == self._arm_for_checks)
            if _sha(binary) != arm.get("binary_sha256"):
                raise BrokerError("Backend binary hash mismatch")
        self._verify_inventory()

    def _guarded_runner(self, argv, **kwargs):
        self._check_pins()
        env = dict(os.environ)
        env.update(kwargs.pop("env", {}) or {})
        env["PYTHONHASHSEED"] = "0"
        completed = self._native_runner(argv, env=env, **kwargs)
        try:
            self._check_pins()
        except Exception as exc:
            raise _PostRunMutation(f"Post-run integrity check failed: {exc}", completed) from exc
        return completed

    def _call_tool(self, name, args):
        result = super()._call_tool(name, args)
        # Preserve Graphify's native no-match as an ordinary completed query.
        if name == "retrieve" and self._arm_for_checks == "graphify":
            try:
                record = json.loads(result["content"][0]["text"])
            except (ValueError, KeyError, IndexError, TypeError):
                return result
            if (record.get("returncode") == 4 and record.get("stdout") == "No matching nodes found.\n"
                    and record.get("stderr") == ""):
                result["isError"] = False
                result["outcome"] = "no_match"
                self._failed_backend = False
        return result
