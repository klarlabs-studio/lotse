"""Check base-pass / injected-fail / restored-pass without asking an agent to repair."""
import contextlib
import argparse
import io
import json
from pathlib import Path

from agent_trials import BASE, prepare, task_faults, tasks, verify


def main(run_id, catalog, report):
    results = []
    for task in tasks(catalog):
        trial_id = run_id + "-" + task["id"]
        root = BASE / ".research/trials" / trial_id / "workspace"
        if root.exists():
            raise RuntimeError(f"Trial already exists: {root}")
        with contextlib.redirect_stdout(io.StringIO()):
            prepare(task, trial_id)
        if task["repo"] == "fastify":
            (root / "node_modules").symlink_to(BASE / ".research/task-runtime/fastify/node_modules", target_is_directory=True)
        names = sorted({f["path"] for f in task_faults(task)})
        faulty = {name: (root / name).read_text() for name in names}
        original = {name: (BASE / ".research/corpora" / task["repo"] / name).read_text() for name in names}
        codes = {}
        for phase, texts in [("base", original), ("fault", faulty), ("restored", original)]:
            for name, text in texts.items():
                (root / name).write_text(text)
            stream = io.StringIO()
            with contextlib.redirect_stdout(stream):
                code = verify(task, root)
            record = json.loads(stream.getvalue())
            log = BASE / ".research/task-logs" / f"{task['id']}-{phase}.json"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(json.dumps(record, indent=2) + "\n")
            codes[phase] = code
            print(task["id"], phase, code, flush=True)
        results.append(dict(task=task["id"], returncodes=codes,
                            sanity_passed=codes["base"] == 0 and codes["fault"] != 0 and codes["restored"] == 0,
                            kind="fixture_validation_not_agent_repair"))
    Path(report).write_text(json.dumps(results, indent=2) + "\n")
    if not all(r["sanity_passed"] for r in results):
        raise SystemExit("Some task fixtures need repair; inspect .research/task-logs")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="sanity")
    parser.add_argument("--catalog", type=Path, default=BASE / "benchmark/agent-tasks.json")
    parser.add_argument("--report", type=Path, default=BASE / "results/task-sanity.json")
    args = parser.parse_args()
    main(args.run_id, args.catalog, args.report)
