"""Run one paired repair trial: headless Claude Code with only the task broker.

    python benchmark/run_agent_trial.py TASK TRIAL_ID --arm ARM [--catalog FILE]

The solver configuration is frozen in benchmark/preregistration.json. The
agent runs in an empty directory with every built-in tool disabled, no user or
project settings (so no personal instructions or hooks), and the broker as its
only MCP server. The provider-reported usage of every model is recorded, and the
trusted verifier decides the outcome even when the agent never calls finish.
Results stay under .research/trials/TRIAL_ID/.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from agent_trials import BASE, audit_trace, tasks
from task_session import ARMS, Session, prepare_session

SYSTEM_PROMPT = (
    "You are repairing a bug in a software repository. You can act only through the "
    "provided tools: retrieve finds relevant source locations, read shows source lines, "
    "search runs a regex search, edit replaces one exact string in a non-test source file, "
    "test runs the project's verification, and finish ends the attempt. Locate the cause, "
    "make the smallest correct fix, run test, and call finish when the fix is in place or "
    "when you cannot make progress.")
SEARCH_ONLY_PROMPT = SYSTEM_PROMPT.replace(
    "retrieve finds relevant source locations, read shows source lines, ", "read shows source lines, ")
TIMEOUT_SECONDS = 1200
TOOL_BINARIES = {"graf": BASE / "target/release/graf", "graf_floor": BASE / "target/release/graf",
                 "graphify": BASE / ".research/venv/bin/graphify",
                 "search_only": Path(shutil.which("rg") or "rg")}


def system_prompt(arm):
    return SEARCH_ONLY_PROMPT if arm == "search_only" else SYSTEM_PROMPT


def preflight(arm, binary):
    """Refuse to start an agent whose retrieval backend is missing; return the backend's hash."""
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError(f"{arm} backend {binary} is missing or not executable; no trial was started")
    return hashlib.sha256(binary.read_bytes()).hexdigest()


def claude_command(issue, mcp_config, model, budget, arm):
    return ["claude", "-p", f"Issue report:\n\n{issue}",
            "--model", model, "--tools", "", "--strict-mcp-config", "--mcp-config", str(mcp_config),
            "--allowedTools", "mcp__broker", "--setting-sources", "", "--disable-slash-commands",
            "--no-session-persistence", "--output-format", "stream-json", "--verbose",
            "--max-budget-usd", str(budget), "--system-prompt", system_prompt(arm)]


def usage_events(result):
    """One model_usage trace event per model, from the provider-reported totals."""
    if not result or "modelUsage" not in result:
        raise ValueError("No result event with model usage; the attempt's usage is unmeasured")
    events = []
    for model, usage in sorted(result["modelUsage"].items()):
        cached = usage["cacheReadInputTokens"]
        events.append({"type": "model_usage", "component": "solver", "model": model,
                       "input_tokens": usage["inputTokens"] + usage["cacheCreationInputTokens"] + cached,
                       "cached_input_tokens": cached, "output_tokens": usage["outputTokens"],
                       "reasoning_tokens": usage.get("thinkingTokens"), "cost_usd": usage["costUSD"],
                       "cost_basis": "list-price estimate under a subscription"})
    return events


def run(task, trial_id, arm, catalog):
    prereg = json.loads((BASE / "benchmark/preregistration.json").read_text())["agent_trials"]
    binary_sha = preflight(arm, TOOL_BINARIES[arm])
    session = prepare_session(task, trial_id, arm)
    directory = session.directory
    agent_cwd = directory / "agent-cwd"
    agent_cwd.mkdir()
    mcp_config = directory / "mcp.json"
    mcp_config.write_text(json.dumps({"mcpServers": {"broker": {
        "command": sys.executable,
        "args": [str(BASE / "benchmark/broker_mcp.py"), task["id"], trial_id, "--arm", arm,
                 "--catalog", str(catalog)]}}}, indent=2) + "\n")
    command = claude_command(task["issue"], mcp_config, prereg["model"], prereg["max_budget_usd"], arm)
    start = time.perf_counter()
    with (directory / "agent-stream.jsonl").open("w") as stream, \
         (directory / "agent-stderr.txt").open("w") as stderr:
        try:
            process = subprocess.run(command, cwd=agent_cwd, stdin=subprocess.DEVNULL, stdout=stream,
                                     stderr=stderr, timeout=TIMEOUT_SECONDS)
            exit_code = process.returncode
        except subprocess.TimeoutExpired:
            exit_code = "timeout"
    wall = time.perf_counter() - start
    return finalize(task, trial_id, arm, command, exit_code, wall, binary_sha)


def finalize(task, trial_id, arm, command, exit_code, wall, binary_sha):
    session = Session(task, trial_id, arm)
    directory = session.directory
    lines = (directory / "agent-stream.jsonl").read_text().splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    agent_finished = session.closed
    if not agent_finished:
        session.call("finish", {})  # The verifier decides, whatever the agent did.
    tool_events = [json.loads(line) for line in (directory / "tool-events.jsonl").read_text().splitlines()]
    try:
        usage = usage_events(result)
        usage_error = None
    except ValueError as error:
        usage, usage_error = [], str(error)
    trace = directory / "trace.jsonl"
    trace.write_text("".join(json.dumps(e) + "\n" for e in [*usage, *tool_events]))
    passed = tool_events[-1].get("type") == "verification" and tool_events[-1]["passed"] is True
    summary = {"task": task["id"], "trial": trial_id, "arm": arm, "command": command,
               "exit_code": exit_code, "wall_s": wall, "passed": passed,
               "agent_called_finish": agent_finished, "result_subtype": (result or {}).get("subtype"),
               "num_turns": (result or {}).get("num_turns"), "tool_calls": session.tool_count,
               "index_build_s": session.build_seconds,
               "infrastructure_error": any(e.get("response", {}).get("infrastructure") for e in tool_events),
               "tool_binary_sha256": binary_sha,
               "usage_complete": usage_error is None, "usage_error": usage_error,
               "audit": audit_trace(trace) if usage_error is None else None}
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("task")
    parser.add_argument("trial_id")
    parser.add_argument("--arm", required=True, choices=ARMS)
    parser.add_argument("--catalog", type=Path, default=BASE / "benchmark/agent-tasks.json")
    args = parser.parse_args()
    task = next(t for t in tasks(args.catalog) if t["id"] == args.task)
    summary = run(task, args.trial_id, args.arm, args.catalog.resolve())
    print(json.dumps({k: v for k, v in summary.items() if k != "command"}, indent=2))
    return 0 if summary["usage_complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
