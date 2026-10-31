"""List batch t14's judgment candidates (natural_tasks_t14.repositories_v138); no solver, no test run.

    python benchmark/t14_candidates.py

As t12_candidates over the v138 repository order. Writes .research/history/t14-judgment-candidates.json.
"""
import json
from pathlib import Path

from t12_candidates import judgment_candidates

BASE = Path(__file__).resolve().parents[1]
OUT = BASE / ".research/history/t14-judgment-candidates.json"


def main():
    spec = json.loads((BASE / "benchmark/preregistration.json").read_text())["agent_trials"]["natural_tasks_t14"]
    counts, rows = judgment_candidates(spec["repositories_v138"]["repository_order"])
    OUT.write_text(json.dumps({"automatic_candidates": counts, "candidates": rows}, indent=1) + "\n")
    print(json.dumps(counts), len(rows), "to judge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
