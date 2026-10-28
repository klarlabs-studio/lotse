"""Two tiny subscription diagnostics; not a comparative agent benchmark."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
CASES = ("plain", "ping")
PROMPTS = {"plain": "Reply exactly OK.",
           "ping": "Call the broker ping tool exactly once with no arguments. After its reply, say exactly OK."}
SYSTEM = "Follow the user's instructions concisely. Use only explicitly supplied tools."
SOURCES = ("benchmark/probe_claude_subscription_v2.py", "benchmark/probe_claude_subscription.py", "benchmark/test_probe_claude_subscription.py",
           "benchmark/claude_usage_diagnostic.py", "benchmark/test_claude_usage_diagnostic_v2.py",
           "benchmark/provider_ledger.py")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    with Path(path).open("x", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def environment():
    env = os.environ.copy()
    # Use the normal subscription login, never an inherited API-key/provider override.
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
                "ANTHROPIC_MODEL", "ANTHROPIC_SMALL_FAST_MODEL", "CLAUDE_CODE_OAUTH_TOKEN"):
        env.pop(key, None)
    env["DISABLE_AUTOUPDATER"] = "1"
    return env


def command(binary, case, config):
    return [str(binary), "-p", PROMPTS[case], "--model", "haiku", "--tools", "",
            "--strict-mcp-config", "--mcp-config", str(config),
            "--setting-sources", "", "--settings", '{"disableAllHooks":true}',
            "--disable-slash-commands", "--no-session-persistence",
            "--output-format", "stream-json", "--verbose", "--include-partial-messages",
            "--max-budget-usd", "0.10", "--max-turns", "1" if case == "plain" else "3",
            "--permission-mode", "dontAsk", "--allowedTools", "mcp__broker__ping" if case == "ping" else "",
            "--system-prompt", SYSTEM]


def ping_server(log):
    calls = 0
    for line in sys.stdin:
        message = json.loads(line)
        method, params = message.get("method"), message.get("params", {})
        if "id" not in message:
            continue
        if method == "initialize":
            result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                      "capabilities": {"tools": {}},
                      "serverInfo": {"name": "subscription-diagnostic", "version": "1"}}
        elif method == "tools/list":
            result = {"tools": [{"name": "ping", "description": "Synthetic probe returning pong.",
                                 "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}}]}
        elif method == "tools/call":
            ok = params.get("name") == "ping" and params.get("arguments", {}) == {} and calls == 0
            calls += 1
            result = {"content": [{"type": "text", "text": "pong" if ok else "refused"}], "isError": not ok}
            with Path(log).open("a", encoding="utf-8") as handle:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(json.dumps({"request": message, "response": result}) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        elif method == "ping":
            result = {}
        else:
            result = {}
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)


def run(plan_path):
    from claude_usage_diagnostic import analyze
    plan_path = Path(plan_path).resolve()
    from provider_ledger import _parse_request
    plan = _parse_request(plan_path.read_bytes())
    if (plan["id"] != "claude-subscription-v2" or plan["cases"] != list(CASES) or
            plan["prompts"] != PROMPTS or plan["system_prompt"] != SYSTEM or
            plan["limits"] != {"max_cli_invocations": 2, "timeout_seconds_each": 60,
                               "max_turns": {"plain": 1, "ping": 3}, "estimated_usd_each": 0.10} or
            plan["requested_model"] != "haiku" or set(plan["sources"]) != set(SOURCES)):
        raise ValueError("unsupported_plan")
    for name, expected in plan["sources"].items():
        if sha(ROOT / name) != expected:
            raise ValueError("source_pin_mismatch")
    binary = Path(plan["binary"])
    if sha(binary) != plan["binary_sha256"]:
        raise ValueError("cli_pin_mismatch")
    root = plan_path.parent
    # Exclusive run directory prevents accidental repeats under this plan.
    directory = root / "run"
    directory.mkdir(mode=0o700, exist_ok=False)
    env = environment()
    auth = subprocess.run([str(binary), "auth", "status"], env=env, capture_output=True, text=True, timeout=15)
    try:
        status = json.loads(auth.stdout)
    except ValueError:
        status = {}
    login_ok = status.get("loggedIn") is True and status.get("authMethod") == "claude.ai" and status.get("apiProvider") == "firstParty"
    save(directory / "auth-status.json", {"logged_in_subscription": login_ok, "exit_code": auth.returncode})
    report = {"id": plan["id"], "plan_sha256": sha(plan_path), "scope": "subscription_accounting_diagnostic",
              "cases": [], "cli_invocations": 0, "product_promotion_pass": False,
              "complete_accounting": False, "agent_savings": "unmeasured",
              "internal_provider_attempts": "unmeasured", "hard_token_cap": "unverified",
              "cost_basis": "client-side list-price estimate; subscription charge unverified",
              "graphify_comparison": "unchanged"}
    if not login_ok:
        report.update(status="blocked", reason="subscription_login_unavailable", exit_code=1)
    else:
        for case in CASES:
            work = directory / case
            work.mkdir(mode=0o700)
            config = work / "mcp.json"
            save(config, {"mcpServers": {} if case == "plain" else {"broker": {
                "command": sys.executable, "args": [str(Path(__file__).resolve()), "--ping-server", str(work / "ping.jsonl")]}}})
            cmd = command(binary, case, config)
            save(work / "command.json", cmd)
            report["cli_invocations"] += 1
            start = time.monotonic()
            with (work / "stream.jsonl").open("xb") as out, (work / "stderr.txt").open("xb") as err:
                os.fchmod(out.fileno(), 0o600)
                os.fchmod(err.fileno(), 0o600)
                try:
                    proc = subprocess.Popen(cmd, cwd=work, env=env, stdin=subprocess.DEVNULL,
                                            stdout=out, stderr=err, start_new_session=True)
                    code = proc.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.wait(timeout=5)
                    code = "timeout"
            analysis = analyze((work / "stream.jsonl").read_bytes())
            pings = [json.loads(line) for line in (work / "ping.jsonl").read_text().splitlines()] if (work / "ping.jsonl").exists() else []
            tool_ok = not pings if case == "plain" else len(pings) == 1 and not pings[0]["response"]["isError"]
            save(work / "analysis.json", analysis)
            # Parser's conservative diagnostic flag is independent of promotion.
            estimated = (analysis.get("model_usage_totals") or {}).get("costUSD")
            within_estimate = type(estimated) in (int, float) and 0 <= estimated <= 0.10
            captured = [json.loads(line) for line in (work / "stream.jsonl").read_bytes().splitlines() if line.strip()] if analysis.get("diagnostic_pass") else []
            final_ok = len([e for e in captured if e.get("type") == "result" and e.get("result", "").strip() == "OK"]) == 1
            init_tools = [e.get("tools") for e in captured if e.get("type") == "system" and e.get("subtype") == "init"]
            expected_tools = [] if case == "plain" else ["mcp__broker__ping"]
            tools_ok = len(init_tools) == 1 and init_tools[0] == expected_tools
            passed = code == 0 and analysis.get("diagnostic_pass") is True and tool_ok and within_estimate and final_ok and tools_ok
            report["cases"].append({"case": case, "exit_code": code, "wall_seconds": time.monotonic()-start,
                                    "diagnostic_pass": passed, "tool_calls": len(pings), "usage": analysis,
                                    "within_estimated_budget": within_estimate, "expected_reply": final_ok,
                                    "expected_tool_inventory": tools_ok})
            if not passed:
                break
        passed = len(report["cases"]) == 2 and all(c["diagnostic_pass"] for c in report["cases"])
        report.update(status="completed" if passed else "failed", exit_code=0 if passed else 1)
    report["raw_artifacts"] = {str(p.relative_to(root)): sha(p) for p in sorted(directory.rglob("*")) if p.is_file()}
    save(root / "report.json", report)
    print(json.dumps({"status": report["status"], "exit_code": report["exit_code"], "cli_invocations": report["cli_invocations"]}))
    return report["exit_code"]


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--ping-server":
        ping_server(sys.argv[2])
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--plan", required=True)
        raise SystemExit(run(parser.parse_args().plan))
