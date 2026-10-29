"""Batch t10 dry run, no model: prepare every task under each arm, run the installed arms' setup, report, clean up."""
import json
import shutil
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE / "benchmark"))
from run_agent_trial import set_up_installed  # noqa: E402
from task_session import prepare_session, restore_workspace  # noqa: E402

catalog = {t["id"]: t for t in json.loads((BASE / "benchmark/natural-tasks-t10.json").read_text())}
report = []
for task_id in sorted(catalog):
    for arm in ("builtin", "graphify_installed", "graf_installed"):
        trial = f"setup10-{task_id}-{arm}"
        start = time.monotonic()
        session = prepare_session(catalog[task_id], trial, arm)
        row = {"trial": trial, "workspace": str(session.root)}
        try:
            if arm != "builtin":
                set_up_installed(session)
                row["setup"] = [{k: v for k, v in step.items() if k in ("command", "returncode", "seconds")}
                                for step in json.loads((session.directory / "setup.json").read_text())]
            row["files"] = sorted(p.relative_to(session.root).as_posix() for p in session.root.rglob("*")
                                  if p.is_file() and (".claude" in p.parts or p.name == "CLAUDE.md"
                                                      or p.relative_to(session.root).parts[0] == "graphify-out"))
            row["ok"] = True
        except Exception as error:  # noqa: BLE001 - the check reports every failure.
            row["ok"], row["error"] = False, repr(error)
        finally:
            restore_workspace(session.directory)
            row["seconds"] = round(time.monotonic() - start, 1)
        report.append(row)
        print(json.dumps({k: row[k] for k in ("trial", "ok", "seconds")}), flush=True)
out = BASE / "results/t10-dry-run.json"
out.write_text(json.dumps(report, indent=1) + "\n")
for row in report:
    shutil.rmtree(BASE / ".research/trials" / row["trial"])
