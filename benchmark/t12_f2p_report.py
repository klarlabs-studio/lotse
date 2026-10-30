"""Combine the t12 fail-to-pass check (natural_tasks_t12, v130): the v129 run stopped at beego-4396 before its report,
so the candidates it completed keep their logged return codes; the rest have their own report. Candidates with a
hidden test absent at the pin are rejected unchecked."""
import json
from pathlib import Path

from t11_f2p_report import logged

BASE = Path(__file__).resolve().parents[1]


def main():
    candidates = json.loads((BASE / ".research/history/t12-f2p-candidates.json").read_text())
    codes = logged(BASE / ".research/t12-f2p.log")
    rest = {r["task"]: r for r in json.loads((BASE / "results/natural-tasks-t12-f2p-rest.json").read_text())}
    results = []
    for task in candidates:
        if task["id"] in rest:
            results.append(rest[task["id"]])
        elif task["id"] in codes and len(codes[task["id"]]) == 3:
            c = codes[task["id"]]
            results.append(dict(task=task["id"], returncodes=c,
                                sanity_passed=c["base"] == 0 and c["fault"] not in (0, "timeout") and c["restored"] == 0,
                                kind="fixture_validation_not_agent_repair"))
        else:
            results.append(dict(task=task["id"], returncodes=None, sanity_passed=False,
                                reason="a hidden test file is absent at the pin (v130)"))
    (BASE / "results/natural-tasks-t12-f2p.json").write_text(json.dumps(results, indent=2) + "\n")
    print(len(results), "candidates;", sum(r["sanity_passed"] for r in results), "fail-to-pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
