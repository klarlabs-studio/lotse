"""Prepare isolated repair tasks and verify a repair. Does not invoke an agent.

Fault definitions and reference evidence stay outside the agent's working copy.
A task's fault is one replacement ("fault") or several ("faults", e.g. a reverted
real fix). A natural task also lists "hidden_tests": the fix's test changes are
reverted in the agent's copy and installed from the corpus only while grading.
Dependencies must be provisioned before verification; nonzero exit is never success.
"""
import argparse
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

BASE = Path(__file__).resolve().parents[1]


def tasks(catalog=BASE / "benchmark/agent-tasks.json"):
    return json.loads(Path(catalog).read_text())


def task_faults(task):
    return task["faults"] if "faults" in task else [task["fault"]]


def _apply(root, edits):
    for edit in edits:
        path = root / edit["path"]
        if edit.get("added"):  # Absent at the pin when upstream later removed it (e.g. a consumed changeset).
            path.unlink(missing_ok=True)
            continue
        text = path.read_text()
        if text.count(edit["before"]) != 1:
            raise ValueError(f"Fault must have exactly one source match in {edit['path']}")
        path.write_text(text.replace(edit["before"], edit["after"], 1))


def prepare(task, trial_id):
    if not trial_id.replace("-", "").replace("_", "").isalnum():
        raise ValueError("trial id must be alphanumeric with hyphens or underscores")
    dest = BASE / ".research/trials" / trial_id / "workspace"
    if dest.exists():
        raise ValueError("Refusing to overwrite a trial")
    shutil.copytree(BASE / ".research/corpora" / task["repo"], dest,
                    ignore=shutil.ignore_patterns(".graf", "graphify-out", ".git", "__pycache__",
                                                 ".pytest_cache", "node_modules", "target"))
    _apply(dest, [*task_faults(task), *task.get("hidden_tests", [])])
    runtime = task.get("runtime", "fastify" if task["repo"] == "fastify" else None)
    if runtime:  # Provisioned dependencies, shared read-only; never part of the indexed source.
        (dest / "node_modules").symlink_to(BASE / ".research/task-runtime" / runtime / "node_modules",
                                           target_is_directory=True)
    (dest.parent / "issue.txt").write_text(task["issue"] + "\n")
    (dest.parent / "task.json").write_text(json.dumps({"task": task["id"]}) + "\n")
    print(dest)


def verify(task, workspace, timeout=180):
    root = workspace.resolve()
    allowed = (BASE / ".research/trials").resolve()
    if not root.is_relative_to(allowed) or not root.is_dir():
        raise ValueError("Verification is restricted to prepared trial workspaces")
    if json.loads((root.parent / "task.json").read_text())["task"] != task["id"]:
        raise ValueError("Task identity does not match this workspace")
    venv = BASE / ".research/task-runtime" / task["python_runtime"] if "python_runtime" in task else BASE / ".research/venv"
    command = [str(venv / "bin/python") if a == "{python}" else a for a in task["verify"]]
    env = dict(os.environ)
    env.update(GOCACHE=str(BASE / ".research/go-cache"), GOMODCACHE=str(BASE / ".research/go-modules"),
               PYTHONPATH=str(root / "src"), PYTHONDONTWRITEBYTECODE="1")
    hidden = {e["path"]: root / e["path"] for e in task.get("hidden_tests", [])}
    agent_view = {name: path.read_text() if path.is_file() else None for name, path in hidden.items()}
    start = time.perf_counter()
    try:
        for name, path in hidden.items():
            path.write_text((BASE / ".research/corpora" / task["repo"] / name).read_text())
        run = run_group(command, root, env, timeout)
    finally:
        for name, path in hidden.items():
            if agent_view[name] is None:
                path.unlink(missing_ok=True)
            else:
                path.write_text(agent_view[name])
    result = dict(task=task["id"], command=command, returncode=run.returncode,
                  verification_passed=run.returncode == 0, wall_s=time.perf_counter()-start,
                  stdout=run.stdout, stderr=run.stderr)
    print(json.dumps(result, indent=2))
    return run.returncode


def run_group(command, cwd, env, timeout):
    """Run a test command in its own process group and kill the whole group when it ends or times out, so test
    runners' worker processes (jest, mocha) never outlive the verdict."""
    process = subprocess.Popen(command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                               start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_group(process.pid)
        process.communicate()
        raise
    finally:
        _kill_group(process.pid)
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def _kill_group(pid):
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def audit_trace(path):
    """Validate explicit totals without inferring absent usage or monetary cost."""
    events = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    allowed = {"model_usage", "tool", "verification"}
    usage = []
    for event in events:
        if event.get("type") not in allowed:
            raise ValueError("Unknown trace event type")
        if event["type"] == "model_usage":
            required = {"component", "model", "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "cost_usd"}
            if not required <= event.keys():
                raise ValueError("Missing usage accounting; preserve unknown usage as unmeasured")
            for key in required - {"component", "model"}:
                value = event[key]
                if value is None and key in {"cached_input_tokens", "reasoning_tokens", "cost_usd"}:
                    continue  # Explicitly unmeasured breakdown/charge; never inferred as zero.
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise ValueError("Usage must be a nonnegative number")
                if key != "cost_usd" and not isinstance(value, int):
                    raise ValueError("Token counts must be integers")
            if not all(isinstance(event[k], str) and event[k] for k in ["component", "model"]):
                raise ValueError("Model and component must identify the measured call")
            if ((event["cached_input_tokens"] is not None and event["cached_input_tokens"] > event["input_tokens"])
                    or (event["reasoning_tokens"] is not None and event["reasoning_tokens"] > event["output_tokens"])):
                raise ValueError("Cached input and reasoning must be subsets of their totals")
            usage.append(event)
    verdicts = [i for i, e in enumerate(events) if e["type"] == "verification"]
    if not usage or len(verdicts) != 1:
        raise ValueError("Incomplete trace; cannot score an agent trial")
    # A solver may issue calls alongside finish; they are refused after the verdict and change nothing.
    if any(e["type"] != "tool" or e.get("response", {}).get("ok") is not False for e in events[verdicts[0] + 1:]):
        raise ValueError("Only refused tool calls may follow the verification")
    verdict = events[verdicts[0]]
    if not isinstance(verdict.get("passed"), bool):
        raise ValueError("Verification must report a boolean result")
    # Reasoning and cached tokens are breakdowns, not additions to totals.
    def measured_sum(key):
        return sum(e[key] for e in usage) if all(e[key] is not None for e in usage) else None

    return dict(total_tokens=sum(e["input_tokens"]+e["output_tokens"] for e in usage),
                cached_input_tokens=measured_sum("cached_input_tokens"),
                reasoning_tokens=measured_sum("reasoning_tokens"),
                cost_usd=measured_sum("cost_usd"),
                unmeasured_fields=[k for k in ("cached_input_tokens", "reasoning_tokens", "cost_usd") if any(e[k] is None for e in usage)],
                tool_calls=sum(e["type"] == "tool" for e in events),
                verification_passed=verdict["passed"] is True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    p = sub.add_parser("prepare")
    p.add_argument("task", choices=[t["id"] for t in tasks()])
    p.add_argument("trial_id")
    p = sub.add_parser("verify")
    p.add_argument("task", choices=[t["id"] for t in tasks()])
    p.add_argument("workspace", type=Path)
    p = sub.add_parser("audit")
    p.add_argument("trace", type=Path)
    args = parser.parse_args()
    if args.action == "audit":
        print(json.dumps(audit_trace(args.trace), indent=2))
    else:
        task = next(t for t in tasks() if t["id"] == args.task)
        if args.action == "prepare":
            prepare(task, args.trial_id)
        else:
            sys.exit(verify(task, args.workspace))
