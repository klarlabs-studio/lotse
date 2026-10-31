"""Combine the t14 fail-to-pass check (natural_tasks_t14, v140): the repaired repositories' candidates (alasql, luxon,
kratos) take their v140 re-check result; every other candidate keeps its v139 result."""
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
REPAIRED = ("alasql", "luxon", "kratos")


def main():
    first = json.loads((BASE / "results/natural-tasks-t14-f2p-v139.json").read_text())
    recheck = {r["task"]: r for r in json.loads((BASE / "results/natural-tasks-t14-f2p-v140.json").read_text())}
    results = [recheck[r["task"]] if r["task"].rsplit("-", 1)[0] in REPAIRED else r for r in first]
    assert {r["task"] for r in results} >= set(recheck)
    (BASE / "results/natural-tasks-t14-f2p.json").write_text(json.dumps(results, indent=2) + "\n")
    print(len(results), "candidates;", sum(r["sanity_passed"] for r in results), "fail-to-pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
