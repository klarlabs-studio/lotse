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
from retrieval import GRAPHIFY
from task_session import ARMS, GRAF_ORIENT_ARMS, ORIENT_ARMS, Session, prepare_session

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
VERIFIER_TOOLS = {"mcp__broker__test", "mcp__broker__finish"}
VERIFIER_LIMIT_SECONDS = 180  # agent_trials.verify's subprocess timeout.
REPLY_SLACK_SECONDS = 60
# USD per million tokens, from the model catalog of the pinned Claude Code 2.1.310 (v93).
STREAM_PRICES = {"claude-haiku-5-5": {
    "above_prompt_tokens": 100_000,
    "base": {"input": 0.1, "output": 0.5, "cache_write_5m": 0.125, "cache_write_1h": 0.2, "cache_read": 0.01},
    "long": {"input": 0.5, "output": 2.5, "cache_write_5m": 0.625, "cache_write_1h": 1.0, "cache_read": 0.05}}}
ORIENTATION_HEADER = "Orientation (generated automatically from the repository before your first turn):\n\n"
ORIENT_PROMPT = (" The issue report is followed by an automatically generated orientation; use it to decide "
                 "where to look first.")
GRAF_ORIENT_PROMPT = SYSTEM_PROMPT.replace(
    "read shows source lines, ", "read shows source lines, outline lists a file's functions, ") + ORIENT_PROMPT
TOOL_BINARIES = {"graf": BASE / "target/release/graf", "graf_floor": BASE / "target/release/graf",
                 "graf_orient": BASE / ".research/bin/graf-t5",
                 "graf_current_orient": BASE / ".research/bin/graf-t6",
                 "graf_lean_orient": BASE / ".research/bin/graf-a9c9e4d",
                 "graf_pointer_orient": BASE / ".research/bin/graf-c3fee50",
                 "graphify_orient": GRAPHIFY, "graphify": GRAPHIFY,
                 "search_only": Path(shutil.which("rg") or "rg"),
                 "ast_graph": [BASE / "frozen-t4" / name
                               for name in ("ast_retrieve.py", "localization.py", "retrieval.py")]}


def system_prompt(arm):
    if arm in GRAF_ORIENT_ARMS:
        return GRAF_ORIENT_PROMPT
    if arm == "graphify_orient":
        return SYSTEM_PROMPT + ORIENT_PROMPT
    return SEARCH_ONLY_PROMPT if arm == "search_only" else SYSTEM_PROMPT


def issue_prompt(issue, orientation):
    """The issue, followed by the harness-generated orientation when the arm has one."""
    return issue if orientation is None else f"{issue}\n\n{ORIENTATION_HEADER}{orientation}"


class OrientationError(RuntimeError):
    """The harness orientation failed before any model call: an infrastructure abort."""


def orient(session):
    """Generate the orientation before the agent starts; it is logged and counted as a tool payload."""
    if session.arm not in ORIENT_ARMS:
        return None
    response = session.call("orient", {})
    if not response["ok"]:
        raise OrientationError(f"Orientation failed; no agent was started: {response['error']}")
    return response["result"]["content"]


def preflight(arm, binary):
    """Refuse to start an agent whose retrieval backend is missing; return the backend's hash.

    A script backend is a list of source files; its hash covers every one of them in order.
    """
    if isinstance(binary, list):
        if not all(path.is_file() for path in binary):
            raise RuntimeError(f"{arm} backend sources are missing; no trial was started")
        return hashlib.sha256("".join(hashlib.sha256(p.read_bytes()).hexdigest() for p in binary).encode()).hexdigest()
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise RuntimeError(f"{arm} backend {binary} is missing or not executable; no trial was started")
    return hashlib.sha256(binary.read_bytes()).hexdigest()


def solver():
    """A batch's pinned copy of the solver (CLAUDE_BINARY), which its self-update cannot change mid-run."""
    return os.environ.get("CLAUDE_BINARY", "claude")


def solver_model(default):
    """A gateway batch's model (SOLVER_MODEL), else the preregistered agent_trials.model."""
    return os.environ.get("SOLVER_MODEL") or default


def solver_budget(default):
    """A gateway batch's spend cap in the solver's own cost units (SOLVER_BUDGET_USD)."""
    return float(os.environ.get("SOLVER_BUDGET_USD") or default)


def model_cost(usage):
    """The batch's frozen price table (USD per million tokens), else the solver's own estimate.

    Claude Code prices models it does not know at a default rate, so gateway batches freeze theirs.
    """
    prices = os.environ.get("SOLVER_PRICES")
    if not prices:
        return usage["costUSD"], "list-price estimate under a subscription"
    rate = json.loads(prices)
    cost = (usage["inputTokens"] * rate["input"] + usage["cacheReadInputTokens"] * rate["cache_read"]
            + usage["cacheCreationInputTokens"] * rate["cache_write"] + usage["outputTokens"] * rate["output"]) / 1e6
    return cost, "frozen gateway price table (preregistered)"


def claude_command(issue, mcp_config, model, budget, arm):
    return [solver(), "-p", f"Issue report:\n\n{issue}",
            "--model", model, "--tools", "", "--strict-mcp-config", "--mcp-config", str(mcp_config),
            "--allowedTools", "mcp__broker", "--setting-sources", "", "--disable-slash-commands",
            "--no-session-persistence", "--output-format", "stream-json", "--verbose",
            "--max-budget-usd", str(budget), "--system-prompt", system_prompt(arm), *turn_cap()]


def turn_cap():
    """A batch's turn cap (SOLVER_MAX_TURNS): a looping solver stops and still reports its usage."""
    turns = os.environ.get("SOLVER_MAX_TURNS")
    return ["--max-turns", turns] if turns else []


def usage_events(result):
    """One model_usage trace event per model, from the provider-reported totals."""
    if not result or "modelUsage" not in result:
        raise ValueError("No result event with model usage; the attempt's usage is unmeasured")
    events = []
    for model, usage in sorted(result["modelUsage"].items()):
        cached = usage["cacheReadInputTokens"]
        cost, basis = model_cost(usage)
        events.append({"type": "model_usage", "component": "solver", "model": model,
                       "input_tokens": usage["inputTokens"] + usage["cacheCreationInputTokens"] + cached,
                       "cached_input_tokens": cached, "output_tokens": usage["outputTokens"],
                       "reasoning_tokens": usage.get("thinkingTokens"), "cost_usd": cost,
                       "cost_basis": basis})
    return events


def scored_events(tool_events):
    """Drop verdicts that a refused, repeated finish wrote before that broker bug was fixed."""
    kept, dropped = [], 0
    for event in tool_events:
        previous = kept[-1] if kept else {}
        if (event.get("type") == "verification" and previous.get("operation") == "finish"
                and previous.get("response", {}).get("error") == "Trial is already finished"):
            dropped += 1
            continue
        kept.append(event)
    return kept, dropped


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
    prompt = issue_prompt(task["issue"], orient(session))
    command = claude_command(prompt, mcp_config, solver_model(prereg["model"]), solver_budget(prereg["max_budget_usd"]), arm)
    if os.environ.get("SOLVER_VERIFIER_DEADLINE") == "True":  # Batch t8 (v94): the broker fits verifier runs inside it.
        (directory / "deadline.json").write_text(json.dumps({"epoch_seconds": time.time() + TIMEOUT_SECONDS}) + "\n")
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
    agent_finished = session.closed
    if session.deferred == "pending":
        session.call("verdict", {})  # A finish near the deadline left the full verifier run for now.
    elif not agent_finished:
        session.call("finish", {})  # The verifier decides, whatever the agent did.
    return score(session.directory, {
        "task": task["id"], "trial": trial_id, "arm": arm, "command": command, "exit_code": exit_code,
        "wall_s": wall, "agent_called_finish": agent_finished, "tool_calls": session.tool_count,
        "index_build_s": session.build_seconds, "tool_binary_sha256": binary_sha})


def score(directory, fields):
    """Score a trial from its preserved stream and tool events, and write its summary."""
    exit_code = fields["exit_code"]
    lines = (directory / "agent-stream.jsonl").read_text().splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    tool_events, dropped = scored_events(
        [json.loads(line) for line in (directory / "tool-events.jsonl").read_text().splitlines()])
    stream = directory / "agent-stream.jsonl"
    infrastructure = (any(e.get("response", {}).get("infrastructure") for e in tool_events)
                      or lost_reply(stream, exit_code) or gateway_refusal(stream))
    bounds = None
    try:
        if result is None and exit_code == "timeout" and not infrastructure:
            usage, bounds = timeout_usage(stream, attempt_budget(fields["command"]))
        else:
            usage = usage_events(result)
        usage_error = None
    except ValueError as error:
        usage, usage_error = [], str(error)
    trace = directory / "trace.jsonl"
    trace.write_text("".join(json.dumps(e) + "\n" for e in [*usage, *tool_events]))
    verdicts = [e for e in tool_events if e.get("type") == "verification"]
    summary = {**fields, "passed": len(verdicts) == 1 and verdicts[0]["passed"] is True,
               "result_subtype": (result or {}).get("subtype"), "num_turns": (result or {}).get("num_turns"),
               "spurious_verdicts_dropped": dropped, "infrastructure_error": infrastructure,
               "usage_complete": usage_error is None, "usage_error": usage_error,
               "audit": audit_trace(trace) if usage_error is None and not infrastructure else None}
    if bounds:
        summary["solver_timeout"] = True
        summary["cost_bounds_usd"] = bounds
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def rescore(trial_id, reason):
    """Re-score a trial from its preserved artifacts under the current rules, without rerunning
    the agent or the verifier; the earlier summary is kept beside the new one."""
    directory = BASE / ".research/trials" / trial_id
    previous = json.loads((directory / "summary.json").read_text())
    (directory / "summary-before-rescore.json").write_text(json.dumps(previous, indent=2) + "\n")
    keys = ("task", "trial", "arm", "command", "exit_code", "wall_s", "agent_called_finish", "tool_calls",
            "index_build_s", "tool_binary_sha256")
    summary = score(directory, {k: previous[k] for k in keys})
    summary["rescored"] = reason
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def lost_reply(stream, exit_code):
    """A timeout while a tool call had no reply: the broker transport failed, not the solver.

    Legitimate broker calls are bounded far below the trial timeout, so this is an infrastructure
    abort, rerun from scratch under the preregistered completeness rule. Since v93 a pending test
    or finish call still within the verifier's own limit is the solver's timeout, not a lost reply:
    a solver whose edits make the tests hang can spend the trial on calls the broker does answer.
    """
    if exit_code != "timeout" or not stream.is_file():
        return False
    pending, names, elapsed = set(), {}, {}
    for line in stream.read_text().splitlines():
        event = json.loads(line)
        if event.get("type") == "tool_progress":  # A running call may surface only as heartbeats.
            call = str(event.get("tool_use_id", "")).split("-heartbeat-")[0]
            pending.add(call)
            names[call] = event.get("tool_name")
            elapsed[call] = max(elapsed.get(call, 0), event.get("elapsed_time_seconds") or 0)
        for block in event.get("message", {}).get("content", []) if isinstance(event.get("message"), dict) else []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                pending.add(block.get("id"))
                names[block.get("id")] = block.get("name")
            elif block.get("type") == "tool_result":
                pending.discard(block.get("tool_use_id"))
    return any(names.get(call) not in VERIFIER_TOOLS
               or elapsed.get(call, 0) > VERIFIER_LIMIT_SECONDS + REPLY_SLACK_SECONDS for call in pending)


def attempt_budget(command):
    return float(command[command.index("--max-budget-usd") + 1])


def timeout_usage(stream, budget):
    """Usage of a solver timeout (v93), which ends without Claude Code's result event.

    Input and cache tokens come exactly from each model call in the stream. Streamed output counts
    are partial and thinking is not shown, so cost is only bounded: below by the streamed counts at
    the pinned Claude Code's list prices, above by the attempt's budget cap.
    """
    calls = {}
    for line in stream.read_text().splitlines():
        event = json.loads(line)
        message = event.get("message")
        if event.get("type") == "assistant" and isinstance(message, dict) and message.get("usage"):
            calls[message["id"]] = (message.get("model"), message["usage"])
    if not calls:
        raise ValueError("No model call in the stream; the attempt's usage is unmeasured")
    totals = {}
    for model, usage in calls.values():
        if model not in STREAM_PRICES:
            raise ValueError(f"No frozen price table for {model}; the attempt's usage is unmeasured")
        prompt = usage["input_tokens"] + usage["cache_creation_input_tokens"] + usage["cache_read_input_tokens"]
        rate = STREAM_PRICES[model]["long" if prompt > STREAM_PRICES[model]["above_prompt_tokens"] else "base"]
        written = usage.get("cache_creation", {})
        cost = (usage["input_tokens"] * rate["input"] + usage["output_tokens"] * rate["output"]
                + written.get("ephemeral_5m_input_tokens", 0) * rate["cache_write_5m"]
                + written.get("ephemeral_1h_input_tokens", 0) * rate["cache_write_1h"]
                + usage["cache_read_input_tokens"] * rate["cache_read"]) / 1e6
        total = totals.setdefault(model, {"input": 0, "cached": 0, "output": 0, "cost": 0.0})
        total["input"] += prompt
        total["cached"] += usage["cache_read_input_tokens"]
        total["output"] += usage["output_tokens"]
        total["cost"] += cost
    events = [{"type": "model_usage", "component": "solver", "model": model, "input_tokens": t["input"],
               "cached_input_tokens": t["cached"], "output_tokens": t["output"], "reasoning_tokens": None,
               "cost_usd": t["cost"], "cost_basis": "v93 solver timeout: lower bound from streamed per-call usage "
               "(output partial, thinking unshown) at the pinned Claude Code's list prices"}
              for model, t in sorted(totals.items())]
    lower = sum(t["cost"] for t in totals.values())
    return events, [lower, max(budget, lower)]


def gateway_refusal(stream):
    """The gateway refused a model call (e.g. 402 out of credits): Claude Code reports it as a
    synthetic assistant message, not a model reply, so it is an infrastructure abort."""
    if not stream.is_file():
        return False
    for line in stream.read_text().splitlines():
        message = json.loads(line).get("message")
        if isinstance(message, dict) and message.get("model") == "<synthetic>" and any(
                isinstance(b, dict) and str(b.get("text", "")).startswith("API Error:") for b in message.get("content", [])):
            return True
    return False


def recover(task, trial_id, arm, reason):
    """Rebuild a summary from a finished trial's preserved artifacts, without rerunning the agent."""
    directory = BASE / ".research/trials" / trial_id
    if (directory / "summary.json").exists():
        raise ValueError(f"{trial_id} already has a summary")
    prereg = json.loads((BASE / "benchmark/preregistration.json").read_text())["agent_trials"]
    lines = (directory / "agent-stream.jsonl").read_text().splitlines()
    result = next(json.loads(l) for l in reversed(lines) if json.loads(l).get("type") == "result")
    command = claude_command(task["issue"], directory / "mcp.json", solver_model(prereg["model"]), solver_budget(prereg["max_budget_usd"]), arm)
    summary = finalize(task, trial_id, arm, command, None, result["duration_ms"] / 1000,
                       preflight(arm, TOOL_BINARIES[arm]))
    summary["recovered"] = (f"{reason}; summary rebuilt from the preserved artifacts without rerunning the "
                            "agent; exit code not recorded, wall time from the result event")
    (directory / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("task")
    parser.add_argument("trial_id")
    parser.add_argument("--arm", required=True, choices=ARMS)
    parser.add_argument("--catalog", type=Path, default=BASE / "benchmark/agent-tasks.json")
    parser.add_argument("--rescore", metavar="REASON", help="re-score the preserved trial; run nothing")
    args = parser.parse_args()
    if args.rescore:
        summary = rescore(args.trial_id, args.rescore)
        print(json.dumps({k: summary[k] for k in ("trial", "infrastructure_error", "usage_complete")}))
        return 0
    task = next(t for t in tasks(args.catalog) if t["id"] == args.task)
    summary = run(task, args.trial_id, args.arm, args.catalog.resolve())
    print(json.dumps({k: v for k, v in summary.items() if k != "command"}, indent=2))
    return 0 if summary["usage_complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
