"""Fail-closed local subprocess supervisor for experimental agent trials.

This enforces deadlines on an owned POSIX process group. It cannot guarantee a
hard wall-clock bound for process creation or provider-side work. Workers must
be trusted not to escape the group with setsid or equivalent mechanisms.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Mapping, Sequence

MAX_TIMEOUT_SECONDS = 1200.0
DEFAULT_REAP_SECONDS = 2.0


def _validate(argv: Sequence[str], timeout_seconds: float, reap_timeout_seconds: float) -> list[str]:
    if isinstance(argv, (str, bytes)) or not isinstance(argv, Sequence) or not argv:
        raise ValueError("argv must be a nonempty sequence of strings")
    values = list(argv)
    if any(not isinstance(arg, str) or not arg or "\0" in arg for arg in values):
        raise ValueError("argv must contain nonempty strings without NUL")
    if isinstance(timeout_seconds, (bool, str, bytes)) or isinstance(reap_timeout_seconds, (bool, str, bytes)):
        raise ValueError("timeouts must be numeric finite positive numbers")
    try:
        timeout = float(timeout_seconds)
        reap = float(reap_timeout_seconds)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("timeouts must be finite positive numbers") from exc
    if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be finite, positive, and <= {MAX_TIMEOUT_SECONDS:g}")
    if not math.isfinite(reap) or reap <= 0 or reap > 30:
        raise ValueError("reap_timeout_seconds must be finite, positive, and <= 30")
    return values


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _close_quietly(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass


def _event(fd: int, seq: int, name: str, **fields: object) -> None:
    record = {"seq": seq, "event": name, **fields}
    payload = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("journal write made no progress")
        view = view[written:]
    os.fsync(fd)


def _kill_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _group_gone(pgid: int, timeout: float) -> bool:
    end = time.monotonic() + timeout
    while True:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        if time.monotonic() >= end:
            return False
        time.sleep(min(0.025, max(0.0, end - time.monotonic())))


def _cleanup(process: subprocess.Popen[bytes], pgid: int, reap_timeout_seconds: float) -> tuple[bool, bool]:
    """Kill descendants even if leader exited, then boundedly reap and verify group exit."""
    kill_ok = True
    try:
        _kill_group(pgid)
    except OSError:
        kill_ok = False
    try:
        process.wait(timeout=reap_timeout_seconds)
    except subprocess.TimeoutExpired:
        # A second group kill covers a create/kill/reap race; never wait forever.
        try:
            _kill_group(pgid)
        except OSError:
            kill_ok = False
        try:
            process.wait(timeout=reap_timeout_seconds)
        except subprocess.TimeoutExpired:
            pass
    leader_reaped = process.returncode is not None
    group_gone = _group_gone(pgid, reap_timeout_seconds)
    return leader_reaped and kill_ok, group_gone and kill_ok


def _digest(path: Path) -> tuple[str, int]:
    h = hashlib.sha256()
    count = 0
    with path.open("rb") as stream:
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            h.update(block)
            count += len(block)
    return h.hexdigest(), count


def run(directory: str | os.PathLike[str], argv: Sequence[str], *, timeout_seconds: float,
        cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None,
        reap_timeout_seconds: float = DEFAULT_REAP_SECONDS) -> dict[str, object]:
    """Run argv in a fresh private directory, retaining exact stdout/stderr bytes.

    Environment values are forwarded only to the child and are never persisted.
    Existing output directories are rejected. No retry is attempted.
    """
    args = _validate(argv, timeout_seconds, reap_timeout_seconds)
    timeout = float(timeout_seconds)
    reap = float(reap_timeout_seconds)
    if env is not None and (not isinstance(env, Mapping) or any(
            not isinstance(k, str) or not isinstance(v, str) or "\0" in k or "\0" in v
            for k, v in env.items())):
        raise ValueError("env must map strings to strings without NUL")

    outdir = Path(directory)
    parent = outdir.parent
    parent.mkdir(parents=True, exist_ok=True)
    outdir.mkdir(mode=0o700)
    os.chmod(outdir, 0o700)
    _fsync_dir(parent)
    stdout_path, stderr_path, journal_path = (outdir / n for n in ("stdout.bin", "stderr.bin", "events.jsonl"))
    seq = 0
    process: subprocess.Popen[bytes] | None = None
    pgid: int | None = None
    started = time.monotonic()
    deadline = started + timeout
    result: dict[str, object] = {
        "outcome": "spawn_error", "returncode": None, "deadline_exceeded": False,
        "usage": "unknown", "provider_usage": "unknown", "usage_source": "unknown",
        "usage_accounting_complete": False,
        "product_promotion_pass": False, "agent_efficiency_acceptance_pass": False,
        "savings": "unmeasured", "stdout_path": str(stdout_path), "stderr_path": str(stderr_path),
        "group_exit_verified": None, "output_capture_complete": False,
    }

    spawned = False
    with ExitStack() as stack:
        stdout_fd = os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        stack.callback(_close_quietly, stdout_fd)
        stderr_fd = os.open(stderr_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        stack.callback(_close_quietly, stderr_fd)
        journal_fd = os.open(journal_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_APPEND, 0o600)
        stack.callback(_close_quietly, journal_fd)
        _fsync_dir(outdir)

        def record(name: str, **fields: object) -> None:
            nonlocal seq
            seq += 1
            _event(journal_fd, seq, name, **fields)

        try:
            record("start_intent", argv=args, cwd=os.fspath(cwd) if cwd is not None else None,
                   timeout_seconds=timeout, monotonic_started=started,
                   trusted_worker_process_group="expected")
            _fsync_dir(outdir)
            if time.monotonic() >= deadline:
                result.update(outcome="timeout", deadline_exceeded=True,
                              group_exit_verified=True, output_capture_complete=True)
                record("timeout_before_spawn", deadline_exceeded=True)
            else:
                try:
                    process = subprocess.Popen(args, cwd=cwd, env=env, shell=False,
                                               start_new_session=True, stdin=subprocess.DEVNULL,
                                               stdout=stdout_fd, stderr=stderr_fd, close_fds=True)
                    spawned = True
                    pgid = process.pid
                except OSError as exc:
                    result["outcome"] = "spawn_error"
                    result["group_exit_verified"] = True
                    result["output_capture_complete"] = True
                    record("spawn_error", error_type=type(exc).__name__)
                else:
                    record("spawned", pid=process.pid)
                    remaining = deadline - time.monotonic()
                    timed_out = remaining <= 0
                    if not timed_out:
                        try:
                            process.wait(timeout=remaining)
                        except subprocess.TimeoutExpired:
                            timed_out = True
                    result["deadline_exceeded"] = timed_out
                    leader_reaped, group_gone = _cleanup(process, pgid, reap)
                    result["returncode"] = process.returncode
                    result["group_exit_verified"] = group_gone
                    result["output_capture_complete"] = leader_reaped and group_gone
                    result["outcome"] = ("reap_unknown" if not (leader_reaped and group_gone) else "timeout" if timed_out else
                                         "completed" if process.returncode == 0 else "nonzero_exit")
                    record("terminated", outcome=result["outcome"], returncode=process.returncode,
                           deadline_exceeded=timed_out, leader_reaped=leader_reaped,
                           group_exit_verified=group_gone)
        except BaseException:
            # Persistence or cancellation after spawn must not strand the process.
            if process is not None and pgid is not None:
                try:
                    _cleanup(process, pgid, reap)
                except BaseException:
                    pass
            if spawned:
                try:
                    record("cancelled", leader_reaped=process is not None and process.returncode is not None)
                except BaseException:
                    pass
            raise

        # Flush raw bytes before closing or computing their hashes.
        os.fsync(stdout_fd)
        os.fsync(stderr_fd)
        stdout_hash, stdout_bytes = _digest(stdout_path)
        stderr_hash, stderr_bytes = _digest(stderr_path)
        result.update(stdout_sha256=stdout_hash, stdout_bytes=stdout_bytes,
                      stderr_sha256=stderr_hash, stderr_bytes=stderr_bytes)
        record("outputs_hashed", stdout_sha256=stdout_hash, stdout_bytes=stdout_bytes,
               stderr_sha256=stderr_hash, stderr_bytes=stderr_bytes,
               output_capture_complete=result["output_capture_complete"])
        report_path = outdir / "report.json"
        report_fd = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            report_data = (json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n").encode()
            report_view = memoryview(report_data)
            while report_view:
                written = os.write(report_fd, report_view)
                if written <= 0:
                    raise OSError("report write made no progress")
                report_view = report_view[written:]
            os.fsync(report_fd)
        finally:
            os.close(report_fd)
        _fsync_dir(outdir)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", required=True)
    parser.add_argument("--timeout-seconds", required=True, type=float)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    ns = parser.parse_args(argv)
    command = ns.command[1:] if ns.command[:1] == ["--"] else ns.command
    try:
        result = run(ns.output_directory, command, timeout_seconds=ns.timeout_seconds)
    except (OSError, ValueError) as exc:
        print(json.dumps({"outcome": "supervisor_error", "error_type": type(exc).__name__}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["outcome"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
