"""Combine the t11 fail-to-pass check (natural_tasks_t11, v123): the v122 run stopped on node-red-5248 before its
report, so the candidates it checked keep their logged return codes; the unreached highlightjs candidates have their
own report. node-red is excluded unchecked."""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
EXCLUDED = {"node-red"}


def logged(log):
    codes = {}
    for line in log.read_text().splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1] in ("base", "fault", "restored"):
            codes.setdefault(parts[0], {})[parts[1]] = parts[2] if parts[2] == "timeout" else int(parts[2])
    return codes


def main():
    candidates = json.loads((BASE / ".research/history/t11-f2p-candidates.json").read_text())
    codes = logged(BASE / ".research/t11-f2p.log")
    rest = {r["task"]: r for r in json.loads((BASE / "results/natural-tasks-t11-f2p-highlightjs.json").read_text())}
    results = []
    for task in candidates:
        if task["repo"] in EXCLUDED:
            continue
        if task["id"] in rest:
            results.append(rest[task["id"]])
            continue
        c = codes[task["id"]]
        results.append(dict(task=task["id"], returncodes=c,
                            sanity_passed=c["base"] == 0 and c["fault"] not in (0, "timeout") and c["restored"] == 0,
                            kind="fixture_validation_not_agent_repair"))
    (BASE / "results/natural-tasks-t11-f2p.json").write_text(json.dumps(results, indent=2) + "\n")
    print(len(results), "checked;", sum(r["sanity_passed"] for r in results), "fail-to-pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
