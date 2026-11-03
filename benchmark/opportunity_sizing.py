"""Exploratory, no model calls: size three tool ideas against preregistration v152 (opportunity_sizing).

    python benchmark/opportunity_sizing.py

- test targeting: test-runner Bash calls in the operator's own Claude Code sessions (~/.claude/projects, 60 days,
  subagents excluded). Only aggregate counts are written; no prompt, path, command or output leaves this process.
- impact check: failed test-tool results in t10-t15 attempts, and whether the next edits touch a not-yet-opened
  source file call-linked to an already edited one (graf call-links, target/failure, over the prepared workspace).
- context profile: per-turn context (input + cache creation + cache read) in both data sets, and the first turn's
  share of it.
Writes results/opportunity-sizing.json. Diagnostic only: it picks what to pilot, it supports no claim.
"""
import contextlib
import io
import json
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import time

from agent_trials import BASE, prepare, tasks
from debug_phase import EDITS, SOURCE, TEST, test_output
from loop_phases import ARMS, calls, relative

GRAF = BASE / "target/failure/release/graf"
BATCHES = ("t10", "t11", "t12", "t13", "t14", "t15")
SESSIONS = Path.home() / ".claude/projects"
WINDOW_DAYS = 60
RUNNER = re.compile(r"\b(cargo (?:test|nextest)|go test|python3? -m (?:pytest|unittest)|pytest|npm (?:run )?test|"
                    r"yarn test|pnpm test|npx (?:jest|vitest)|jest|vitest|make test|mvn test|gradle test|ctest|"
                    r"bundle exec rspec|rspec|mix test)\b")
OUTCOME = re.compile(r"\b(passed|failed|ok|FAIL|PASS|test result:|Tests?:|\d+ tests?|no failures)\b")
SETUP = re.compile(r"command not found|no such file|cannot find|not found|no tests ran|no test files|unknown option|"
                   r"unrecognized|ModuleNotFoundError|could not find|error: no such", re.I)
SEGMENT = re.compile(r"\|\||&&|[|;]")


# ---- test targeting (real sessions) ----

def test_command(command):
    """The runner segment of a shell command, or None."""
    return next((s.strip() for s in SEGMENT.split(command) if RUNNER.search(s)), None)


def unscoped(segment):
    rest = segment[RUNNER.search(segment).end():].split()
    return not [w for w in rest if not w.startswith("-") and not re.match(r"^\d*>", w) and w != "2>&1"]


def session_files():
    cutoff = time.time() - WINDOW_DAYS * 86400
    return sorted(p for p in SESSIONS.glob("*/*.jsonl") if p.stat().st_mtime >= cutoff and "subagent" not in p.name)


def test_targeting(files):
    rows = []
    for path in files:
        seq = calls(path)
        tool_chars = sum(len(t) for _, _, t in seq)
        tests = [(seg, text) for name, args, text in seq if name == "Bash"
                 for seg in [test_command(args.get("command") or "")] if seg]
        if not tests:
            continue
        rows.append({"test_calls": len(tests),
                     "setup_failures": sum(not OUTCOME.search(t) and bool(SETUP.search(t)) for _, t in tests),
                     "unscoped": sum(unscoped(s) for s, _ in tests),
                     "test_chars": sum(len(t) for _, t in tests), "tool_chars": tool_chars})
    n = len(rows)
    calls_total = sum(r["test_calls"] for r in rows)
    out = {"sessions_scanned": len(files), "test_running_sessions": n,
           "test_calls_per_session": round(calls_total / n, 2) if n else 0,
           "setup_failures_per_session": round(sum(r["setup_failures"] for r in rows) / n, 2) if n else 0,
           "unscoped_share_of_test_calls": round(sum(r["unscoped"] for r in rows) / calls_total, 3) if n else 0,
           "test_output_share_of_tool_results": round(sum(r["test_chars"] for r in rows)
                                                     / max(1, sum(r["tool_chars"] for r in rows)), 3)}
    out["clears_threshold"] = out["setup_failures_per_session"] >= 2.0 or (
        out["test_output_share_of_tool_results"] >= 0.25 and out["unscoped_share_of_test_calls"] >= 0.5)
    return out


# ---- context profile (both) ----

def turn_contexts(stream):
    """Per distinct assistant message, in order: input + cache creation + cache read tokens."""
    usage = {}
    for line in stream.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        message = event.get("message") if event.get("type") == "assistant" else None
        if isinstance(message, dict) and isinstance(message.get("usage"), dict) and message.get("id"):
            u = message["usage"]
            usage[message["id"]] = sum(u.get(k) or 0 for k in
                                       ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
    return [v for v in usage.values() if v > 0]


def profile(contexts):
    return {"turns": len(contexts), "prefix": contexts[0], "total": sum(contexts),
            "prefix_share": contexts[0] * len(contexts) / sum(contexts)}


def summarize_profiles(profiles):
    if not profiles:
        return {"attempts": 0}
    return {"attempts": len(profiles),
            "median_turns": statistics.median(p["turns"] for p in profiles),
            "median_prefix": statistics.median(p["prefix"] for p in profiles),
            "median_turn_context": statistics.median(p["total"] / p["turns"] for p in profiles),
            "median_prefix_share": round(statistics.median(p["prefix_share"] for p in profiles), 3)}


def benchmark_trials():
    for batch in BATCHES:
        for summary in sorted((BASE / ".research/trials").glob(f"{batch}-*/summary.json")):
            directory = summary.parent
            arm = next((a for a in ARMS if f"-{a}-" in directory.name), None)
            if arm and not directory.name.startswith(f"{batch}-screen-"):
                yield batch, arm, directory, json.loads(summary.read_text())["task"]


# ---- impact check (benchmark) ----

def source(path):
    return bool(SOURCE.search(path)) and not TEST.search(path)


def failures(seq):
    """Per failed test result: (k, files edited before, files opened before, files edited before the next test)."""
    edited, opened, out, k = set(), set(), [], 0
    for i, (name, args, text) in enumerate(seq):
        path = relative(args.get("file_path", ""), "")
        if name in EDITS:
            edited.add(path)
            opened.add(path)
        elif name == "Read":
            opened.add(path)
        elif name == "test" and test_output(text)[0] is False:
            k += 1
            following = set()
            for later, largs, _ in seq[i + 1:]:
                if later in ("test", "finish"):
                    break
                if later in EDITS:
                    following.add(relative(largs.get("file_path", ""), ""))
            out.append((k, set(edited), set(opened), following))
    return out


def call_links(task):
    trial = f"sizing-{task['id']}"
    with contextlib.redirect_stdout(io.StringIO()):
        prepare(task, trial)
    root = BASE / ".research/trials" / trial / "workspace"
    try:
        subprocess.run([str(GRAF), "--root", str(root), "index"], capture_output=True, check=True)
        links = json.loads(subprocess.run([str(GRAF), "--root", str(root), "call-links"], capture_output=True,
                                          text=True, check=True).stdout)["links"]
    finally:
        shutil.rmtree(root.parent)
    linked = {}
    for a, b in links:
        linked.setdefault(a, set()).add(b)
        linked.setdefault(b, set()).add(a)
    return linked


def impact_rows(attempts):
    catalogs = {b: {t["id"]: t for t in tasks(BASE / f"benchmark/natural-tasks-{b}.json")} for b in BATCHES}
    links, rows = {}, []
    for batch, arm, directory, task_id in attempts:
        found = failures(calls(directory / "agent-stream.jsonl"))
        if not found:
            continue
        key = (batch, task_id)
        if key not in links:
            links[key] = call_links(catalogs[batch][task_id])
            print("links", batch, task_id, flush=True)
        linked = links[key]
        for k, edited, opened, following in found:
            new = {f for f in following if source(f) and f not in opened}
            rows.append({"batch": batch, "arm": arm, "failure": k, "new_source_edit": bool(new),
                         "impact_linked": any(linked.get(f, set()) & edited for f in new)})
    return rows


def impact_summary(rows):
    def share(group):
        n = len(group)
        return {"failures": n,
                "impact_linked": sum(r["impact_linked"] for r in group),
                "impact_linked_share": round(sum(r["impact_linked"] for r in group) / n, 3) if n else 0,
                "upper_bound_share": round(sum(r["new_source_edit"] for r in group) / n, 3) if n else 0}
    repeat = [r for r in rows if r["failure"] >= 2]
    out = {"repeat_failures": share(repeat), "first_failures": share([r for r in rows if r["failure"] == 1]),
           "repeat_by_arm": {a: share([r for r in repeat if r["arm"] == a]) for a in ARMS}}
    out["clears_threshold"] = out["repeat_failures"]["impact_linked_share"] >= 0.25
    return out


def main():
    attempts = list(benchmark_trials())
    bench = {(b, a): [] for b in BATCHES for a in ARMS}
    for batch, arm, directory, _ in attempts:
        contexts = turn_contexts(directory / "agent-stream.jsonl")
        if contexts:
            bench[(batch, arm)].append(profile(contexts))
    baseline = statistics.median(p["prefix"] for p in bench[("t15", "builtin")])
    files = session_files()
    real = [profile(c) for f in files for c in [turn_contexts(f)] if len(c) >= 5]
    excess = [max(0, p["prefix"] - baseline) * p["turns"] / p["total"] for p in real]
    context = {"benchmark_by_arm": {a: summarize_profiles([p for b in BATCHES for p in bench[(b, a)]]) for a in ARMS},
               "t15_builtin_median_prefix": baseline, "real_sessions": summarize_profiles(real),
               "real_median_excess_share": round(statistics.median(excess), 3) if excess else 0}
    context["clears_threshold"] = context["real_median_excess_share"] >= 0.10
    report = {"test_targeting": test_targeting(files), "context_profile": context,
              "impact_check": impact_summary(impact_rows(attempts))}
    (BASE / "results/opportunity-sizing.json").write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
