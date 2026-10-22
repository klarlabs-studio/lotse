"""Describe what agents spend their tool calls on, from preserved trial events (no solver).

    python benchmark/agent_behavior.py BATCH [BATCH ...]

Per arm: turns, reads, distinct files read, repeated reads of a file, the share of
read lines already delivered earlier in the run, read and search payloads, and tool
calls before the first edit. Descriptive only; run it on completed batches.
"""
import collections
import glob
import json
import re
import statistics
import sys

from agent_trials import BASE


def run_profile(events_path):
    seen = collections.defaultdict(set)
    files, profile = set(), collections.Counter()
    operations = []
    for line in open(events_path):
        event = json.loads(line)
        if event.get("type") != "tool":
            continue
        operations.append(event["operation"])
        tokens = event.get("reference_payload_tokens", 0)
        if event["operation"] == "search":
            profile["searches"] += 1
            profile["search_tokens"] += tokens
        if event["operation"] != "read" or not event["response"].get("ok"):
            continue
        result = event["response"]["result"]
        lines = [int(n) for n in re.findall(r"^L(\d+): ", result["content"], re.M)]
        repeated = sum(n in seen[result["path"]] for n in lines)
        profile["reads"] += 1
        profile["read_tokens"] += tokens
        profile["read_lines"] += len(lines)
        profile["repeated_lines"] += repeated
        profile["repeated_read_tokens"] += tokens * repeated / len(lines) if lines else 0
        profile["same_file_reads"] += result["path"] in files
        files.add(result["path"])
        seen[result["path"]].update(lines)
    profile["files_read"] = len(files)
    profile["calls"] = len(operations)
    profile["calls_before_first_edit"] = operations.index("edit") if "edit" in operations else len(operations)
    return profile


def main(batches):
    by_arm = collections.defaultdict(list)
    for batch in batches:
        for path in sorted(glob.glob(str(BASE / f".research/trials/{batch}-*/tool-events.jsonl"))):
            summary = json.load(open(path.replace("tool-events.jsonl", "summary.json")))
            profile = run_profile(path)
            profile["turns"] = summary["num_turns"] or 0
            profile["total_tokens"] = summary["audit"]["total_tokens"]
            by_arm[(batch, summary["arm"])].append(profile)
    report = {}
    for (batch, arm), profiles in sorted(by_arm.items()):
        keys = sorted({k for p in profiles for k in p})
        row = {k: round(statistics.mean(p[k] for p in profiles), 2) for k in keys}
        row["repeated_line_share"] = round(sum(p["repeated_lines"] for p in profiles)
                                           / max(sum(p["read_lines"] for p in profiles), 1), 4)
        row["runs"] = len(profiles)
        report[f"{batch}/{arm}"] = row
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
