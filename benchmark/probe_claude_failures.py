"""Frozen Claude subscription failure diagnostics, without retrieval claims."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

from probe_claude_subscription_v2 import environment, save, sha
from provider_ledger import _parse_request

ROOT = Path(__file__).resolve().parents[1]
MODEL = "claude-haiku-5-5"
CASES = ["tool_retry", "turn_limit", "interrupted_tool"]
PROMPTS = {
    "tool_retry": "Call broker ping with no arguments. It fails once; retry exactly once. After pong, reply exactly OK.",
    "turn_limit": "Call broker ping exactly once with no arguments. After pong, reply exactly OK.",
    "interrupted_tool": "Call broker ping exactly once with no arguments and wait for its reply."}
LIMITS = {"max_cli_invocations": 3, "timeout_seconds_each": 45,
          "estimated_usd_each": 0.10, "max_turns": {"tool_retry": 4, "turn_limit": 1, "interrupted_tool": 2}}
SOURCES = ["benchmark/probe_claude_failures.py", "benchmark/test_probe_claude_failures.py",
           "benchmark/claude_failure_accounting.py", "benchmark/test_claude_failure_accounting.py",
           "benchmark/probe_claude_subscription_v2.py", "benchmark/claude_usage_diagnostic.py",
           "benchmark/provider_ledger.py"]


def append(log, event):
    with Path(log).open("a", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(json.dumps(event, allow_nan=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def server(case, log):
    calls = 0
    for line in sys.stdin:
        message = _parse_request(line.encode())
        if "id" not in message:
            continue
        method, params = message.get("method"), message.get("params", {})
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                      "capabilities": {"tools": {}}, "serverInfo": {"name": "failure-probe", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "ping", "description": "Synthetic accounting probe.",
                                 "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]}
        elif method == "tools/call":
            calls += 1
            append(log, {"event": "tool_begin", "call": calls, "request": message})
            valid = params.get("name") == "ping" and params.get("arguments", {}) == {}
            if case == "interrupted_tool" and valid and calls == 1:
                marker = Path(log).with_name("blocked.json")
                save(marker.with_suffix(".tmp"), {"event": "blocked_tool", "call": calls})
                os.rename(marker.with_suffix(".tmp"), marker)
                # The parent kills this process group after observing the marker.
                while True:
                    time.sleep(1)
            max_calls = 2 if case == "tool_retry" else 1
            failed = not valid or calls > max_calls or (case == "tool_retry" and calls == 1)
            text = "Synthetic temporary failure; retry once." if failed else "pong"
            result = {"content": [{"type": "text", "text": text}], "isError": failed}
            append(log, {"event": "tool_end", "call": calls, "response": result})
        else:
            result = {}
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)


def command(binary, case, config):
    return [str(binary), "-p", PROMPTS[case], "--model", MODEL, "--tools", "",
            "--strict-mcp-config", "--mcp-config", str(config), "--setting-sources", "",
            "--settings", '{"disableAllHooks":true}', "--disable-slash-commands",
            "--no-session-persistence", "--output-format", "stream-json", "--verbose",
            "--include-partial-messages", "--max-budget-usd", "0.10", "--max-turns",
            str(LIMITS["max_turns"][case]), "--permission-mode", "dontAsk",
            "--allowedTools", "mcp__broker__ping", "--system-prompt",
            "Follow the user's instructions concisely. Use only explicitly supplied tools."]


def terminate(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return proc.wait(timeout=5)


def execute(cmd, work, env, case):
    start = time.monotonic()
    with (work / "stream.jsonl").open("xb") as out, (work / "stderr.txt").open("xb") as err:
        os.fchmod(out.fileno(), 0o600)
        os.fchmod(err.fileno(), 0o600)
        proc = subprocess.Popen(cmd, cwd=work, env=env, stdin=subprocess.DEVNULL,
                                stdout=out, stderr=err, start_new_session=True)
        try:
            reason = "exited"
            while proc.poll() is None:
                marker = work / "blocked.json"
                if case == "interrupted_tool" and marker.exists():
                    # Marker rename is atomic after its contents were synchronized.
                    if _parse_request(marker.read_bytes()) == {"event": "blocked_tool", "call": 1}:
                        reason = "intentional_interruption_after_tool_begin"
                        terminate(proc)
                        break
                if time.monotonic() - start >= LIMITS["timeout_seconds_each"]:
                    reason = "timeout"
                    terminate(proc)
                    break
                time.sleep(0.05)
            return {"exit_code": proc.wait(timeout=5), "termination": reason,
                    "wall_seconds": time.monotonic() - start}
        except BaseException:
            terminate(proc)
            raise


def evaluate(case, execution, analysis, records, events):
    begins = [e for e in records if e.get("event") == "tool_begin"]
    ends = [e for e in records if e.get("event") == "tool_end"]
    results = [e for e in events if e.get("type") == "result"]
    inits = [e for e in events if e.get("type") == "system" and e.get("subtype") == "init"]
    models = analysis.get("model_usage_keys", [])
    inventory_ok = len(inits) == 1 and inits[0].get("tools") == ["mcp__broker__ping"]
    requests_ok = all(e.get("call") == i and
                      e.get("request", {}).get("params") == {"name": "ping", "arguments": {}}
                      for i, e in enumerate(begins, 1))
    if not requests_ok:
        return False
    if case == "interrupted_tool":
        return (execution["termination"] == "intentional_interruption_after_tool_begin" and
                execution["exit_code"] != 0 and len(begins) == 1 and not ends and not results and
                analysis.get("accounting_status") == "unknown" and inventory_ok and
                analysis.get("assistant_models") == [MODEL])
    consistent = analysis.get("accounting_status") == "reported_terminal_consistent"
    cost = analysis.get("exposed_estimated_cost")
    common = (consistent and models == [MODEL] and inventory_ok and
              type(cost) in (int, float) and 0 <= cost <= 0.10 and len(results) == 1 and
              execution["termination"] == "exited")
    if case == "tool_retry":
        return (common and execution["exit_code"] == 0 and len(begins) == len(ends) == 2 and
                ends[0]["response"].get("isError") is True and ends[1]["response"].get("isError") is False and
                results[0].get("subtype") == "success" and results[0].get("result", "").strip() == "OK")
    return (common and execution["exit_code"] != 0 and len(begins) == len(ends) == 1 and
            results[0].get("subtype") == "error_max_turns" and results[0].get("is_error") is True)


def run(plan_path):
    from claude_failure_accounting import analyze_failure
    plan_path = Path(plan_path).resolve()
    plan = _parse_request(plan_path.read_bytes())
    if (plan["id"] != "claude-failures-v1" or plan["cases"] != CASES or plan["limits"] != LIMITS or
            plan["requested_model"] != MODEL or plan["prompts"] != PROMPTS or set(plan["sources"]) != set(SOURCES)):
        raise ValueError("unsupported_plan")
    if any(sha(ROOT / name) != expected for name, expected in plan["sources"].items()):
        raise ValueError("source_pin_mismatch")
    if sha(plan["binary"]) != plan["binary_sha256"] or sha(Path(sys.executable).resolve()) != plan["python_sha256"]:
        raise ValueError("runtime_pin_mismatch")
    root = plan_path.parent
    directory = root / "run"
    directory.mkdir(mode=0o700, exist_ok=False)
    report = {"id": plan["id"], "scope": "synthetic_failure_accounting_diagnostic",
              "plan_sha256": sha(plan_path), "cases": [], "cli_invocations": 0,
              "complete_accounting": False, "hard_token_cap": "unverified",
              "internal_provider_attempts": "unmeasured", "agent_savings": "unmeasured",
              "product_promotion_pass": False, "graphify_comparison": "unchanged"}
    env = environment()
    status, auth_code, auth_error = {}, None, None
    try:
        auth = subprocess.run([plan["binary"], "auth", "status"], capture_output=True, env=env, timeout=15)
        auth_code = auth.returncode
        status = _parse_request(auth.stdout)
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        auth_error = type(error).__name__
    logged_in = auth_code == 0 and status.get("loggedIn") is True and status.get("authMethod") == "claude.ai" and status.get("apiProvider") == "firstParty"
    save(directory / "auth-status.json", {"subscription_login": logged_in, "exit_code": auth_code, "error_type": auth_error})
    if logged_in:
        for case in CASES:
            work = directory / case
            work.mkdir(mode=0o700)
            config = work / "mcp.json"
            save(config, {"mcpServers": {"broker": {"command": sys.executable,
                "args": [str(Path(__file__).resolve()), "--server", case, str(work / "tool.jsonl")]}}})
            cmd = command(plan["binary"], case, config)
            save(work / "command.json", cmd)
            report["cli_invocations"] += 1
            try:
                execution = execute(cmd, work, env, case)
            except (OSError, subprocess.SubprocessError, ValueError) as error:
                report["cases"].append({"case": case, "diagnostic_pass": False,
                    "termination": "operational_error", "error_type": type(error).__name__,
                    "accounting_status": "unknown", "exit_code": None})
                break
            raw = (work / "stream.jsonl").read_bytes()
            analysis = analyze_failure(raw)
            try:
                records = [_parse_request(line) for line in (work / "tool.jsonl").read_bytes().splitlines()] if (work / "tool.jsonl").exists() else []
            except ValueError:
                save(work / "analysis.json", analysis)
                report["cases"].append({"case": case, **execution, "diagnostic_pass": False,
                    "error_type": "invalid_tool_journal", "accounting": analysis})
                break
            events = []
            for line in raw.splitlines():
                try:
                    events.append(_parse_request(line))
                except ValueError:
                    continue
            passed = evaluate(case, execution, analysis, records, events)
            save(work / "analysis.json", analysis)
            report["cases"].append({"case": case, **execution, "diagnostic_pass": passed,
                                    "tool_calls": len([e for e in records if e.get("event") == "tool_begin"]),
                                    "accounting": analysis})
            if not passed:
                break
    passed = logged_in and len(report["cases"]) == len(CASES) and all(c["diagnostic_pass"] for c in report["cases"])
    report.update(status="completed" if passed else "failed" if logged_in else "blocked", exit_code=0 if passed else 1)
    report["raw_artifacts"] = {str(p.relative_to(root)): sha(p) for p in sorted(directory.rglob("*")) if p.is_file()}
    save(root / "report.json", report)
    print(json.dumps({"status": report["status"], "exit_code": report["exit_code"], "cli_invocations": report["cli_invocations"]}))
    return report["exit_code"]


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--server":
        server(sys.argv[2], sys.argv[3])
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--plan", required=True)
        raise SystemExit(run(parser.parse_args().plan))
