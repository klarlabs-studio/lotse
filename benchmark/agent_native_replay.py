"""Post-hoc estimate of what recorded agent sessions would cost if their searches and reads had come from Claude
Code's built-in Grep and Read instead of the benchmark's capped broker tools; no model calls.

    python benchmark/agent_native_replay.py --batches t5 t6 t7 t8 t9

Exploratory and not preregistered. Each search and read result keeps its place in the session (one cache write and a
cache read on every later call, as in agent_headroom_diagnostic.py) and only its size is replaced:
- read: Read's default, the first 2,000 lines of the file in `cat -n` form (from the requested start when it lies
  beyond line 2,000). A file over 25,000 tokens is refused by Read, so that read keeps its broker size. A file already
  read whole and not edited since costs nothing again.
- search, two variants: Grep's default `files_with_matches` (paths only, first 250), and `content` mode with line
  numbers (first 250 lines, lines cut at 500 columns, a result over 20,000 characters replaced by a 2 KB preview).
  `rg` runs over the attempt's final workspace with Grep's flags.
The agent's own choices stay fixed, although a native agent would choose differently. Files the attempt edited are
measured in their final state. Writes results/agent-native-replay.json.

Decision bar stated before the first run: if search and read results reach at least half of the estimated cost of
the search_only arms under the built-in tools, a batch against a built-in-tools baseline is worth proposing.
"""
import argparse
import collections
import json
from pathlib import Path
import subprocess
import sys

from agent_headroom_diagnostic import BASE, PRICE, SEARCH_TOOLS, attempt, calls_and_results, write_price
from retrieval import tokens

OUT = BASE / "results/agent-native-replay.json"
READ_LINES, READ_MAX_TOKENS, LINE_CHARS, GREP_HEAD_LIMIT = 2000, 25000, 2000, 250
# Grep runs ripgrep with --hidden, these directories excluded and --max-columns 500; a result over 20,000 characters
# is saved to a file and the model sees a short preview instead.
VCS_DIRS = (".git", ".svn", ".hg", ".bzr", ".jj", ".sl")
GREP_RESULT_CHARS, PREVIEW_CHARS = 20000, 2000
PERSISTED_NOTE = "Output too large. Full output saved to: /tmp/tool-results/grep.txt\n\nPreview (first 2KB):\n"
VARIANTS = ("files_with_matches", "content")


def native_read(workspace, arguments, held):
    """cl100k size of Read's default result, 0 when the whole file is already held, None when Read would refuse it."""
    path = arguments.get("path")
    if not isinstance(path, str) or path in held:
        return 0 if path in held else None
    file = workspace / path
    if not file.is_file():
        return None
    start = arguments.get("start", 1)
    first = start if isinstance(start, int) and start > READ_LINES else 1
    lines = file.read_text(errors="replace").splitlines()[first - 1:first - 1 + READ_LINES]
    size = len(tokens("\n".join(f"{first + i:>6}\t{line[:LINE_CHARS]}" for i, line in enumerate(lines))))
    if size > READ_MAX_TOKENS:
        return None
    if first == 1:
        held.add(path)
    return size


def native_search(workspace, arguments, variant):
    pattern = arguments.get("pattern")
    if not isinstance(pattern, str):
        return None
    flags = ["--files-with-matches"] if variant == "files_with_matches" else ["--line-number"]
    vcs = [arg for name in VCS_DIRS for arg in ("--glob", f"!{name}")]
    done = subprocess.run(["rg", "--no-config", "--color", "never", "--hidden", *vcs, "--max-columns", "500", *flags,
                           "--", pattern, "."], cwd=workspace, capture_output=True, text=True, timeout=120)
    if done.returncode > 1:
        return None
    lines = [line.removeprefix("./") for line in done.stdout.splitlines()][:GREP_HEAD_LIMIT]
    header = f"Found {len(lines)} files\n" if variant == "files_with_matches" else ""
    text = header + "\n".join(lines)
    if len(text) > GREP_RESULT_CHARS:
        text = PERSISTED_NOTE + text[:PREVIEW_CHARS]
    return len(tokens(text))


def replay(directory, variant):
    """Cost change, in dollars, from giving every search and read result its built-in tool size."""
    calls, _ = calls_and_results(directory / "agent-stream.jsonl")
    workspace, held = directory / "workspace", set()
    delta, refused = 0.0, 0
    for index in range(1, len(calls)):
        previous, usage = calls[index - 1], calls[index]["usage"]
        new = usage.get("cache_creation_input_tokens", 0) + usage.get("input_tokens", 0)
        measured = sum(size for _, size in previous["results"])
        scale = min(1.0, new / measured) if measured else 1.0
        per_token = write_price(usage) + PRICE["cache_read"] * (len(calls) - index - 1)
        for (name, size), arguments in zip(previous["results"], previous["arguments"]):
            if name == "edit":
                held.discard(arguments.get("path"))
            if name not in ("read", "search"):
                continue
            native = native_read(workspace, arguments, held) if name == "read" \
                else native_search(workspace, arguments, variant)
            if native is None:
                refused += 1
                continue
            delta += (native - size) * scale * per_token
    return delta, refused


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--batches", nargs="+", required=True)
    args = parser.parse_args()
    rows = []
    for batch in args.batches:
        for path in sorted((BASE / ".research/trials").glob(f"{batch}-*/summary.json")):
            if not (path.parent / "agent-stream.jsonl").is_file() or not (path.parent / "workspace").is_dir() \
                    or json.loads(path.read_text()).get("audit", {}).get("cost_usd") is None:
                continue
            base = attempt(path.parent)
            row = {"batch": batch, "trial": base["trial"], "task": base["task"], "arm": base["arm"],
                   "passed": base["passed"], "cost_usd": base["cost_usd"], "search_payload_usd": base["search_payload_usd"]}
            for variant in VARIANTS:
                delta, refused = replay(path.parent, variant)
                row[variant] = {"cost_usd": round(base["cost_usd"] + delta, 6),
                                "search_payload_usd": round(base["search_payload_usd"] + delta, 6), "kept_broker_size": refused}
            rows.append(row)
        print(f"{batch}: {sum(r['batch'] == batch for r in rows)} attempts", flush=True)
    groups = collections.defaultdict(list)
    for row in rows:
        groups[(row["batch"], row["arm"])].append(row)
    summary = {}
    for (batch, arm), own in sorted(groups.items()):
        cost = sum(r["cost_usd"] for r in own)
        entry = {"attempts": len(own), "broker_cost_usd": round(cost, 4),
                 "broker_search_share": round(sum(r["search_payload_usd"] for r in own) / cost, 3)}
        for variant in VARIANTS:
            native = sum(r[variant]["cost_usd"] for r in own)
            entry[variant] = {"cost_usd": round(native, 4), "cost_ratio": round(native / cost, 3),
                              "search_share": round(sum(r[variant]["search_payload_usd"] for r in own) / native, 3),
                              "kept_broker_size": sum(r[variant]["kept_broker_size"] for r in own)}
        summary.setdefault(batch, {})[arm] = entry
    OUT.write_text(json.dumps({"exploratory": True, "claude_code": "2.1.312 defaults: Read 2,000 lines, 25,000-token "
                               "refusal; Grep files_with_matches, head_limit 250", "summary": summary, "rows": rows},
                              indent=2) + "\n")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
